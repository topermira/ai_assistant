"""Управление Рокси с телефона через своего Telegram-бота (BotFather).

Настройка (один раз):
  1. В .env:  TG_BOT_TOKEN=<токен от BotFather>
  2. Запусти Рокси — в журнале появится код привязки. Отправь боту:  /start <код>
     Твой Telegram-ID сохранится в config.json («tg_bot_owner»), остальным бот отвечать не будет.

Что умеет:
  * любой текст — как команда Рокси («выключи комп через час», «громкость 30», «что за ошибка на экране»);
    ответ приходит сообщением (вслух на компьютере не говорит — тебя рядом нет);
  * /screen — снимок экрана;  /help — подсказка;  /unlink — отвязать телефон.
  * Опасное (выключение, удаление…) спрашивает «да/нет» здесь же, в чате.

Без сторонних библиотек: Bot API через urllib, долгий опрос getUpdates.
"""
import io
import json
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://api.telegram.org/bot{token}/{method}"
TG_HOST = "api.telegram.org"

# Только IPv4 для api.telegram.org: по IPv6 соединение у части провайдеров «открывается», но ответ не
# приходит — запросы висели до таймаута («The read operation timed out»), а повторы ловили 429.
_orig_getaddrinfo = socket.getaddrinfo


def _gai_v4_for_telegram(host, *args, **kw):
    res = _orig_getaddrinfo(host, *args, **kw)
    if host == TG_HOST:
        v4 = [r for r in res if r[0] == socket.AF_INET]
        return v4 or res
    return res


if not getattr(socket.getaddrinfo, "_roxy_v4", False):
    _gai_v4_for_telegram._roxy_v4 = True
    socket.getaddrinfo = _gai_v4_for_telegram
HELP = ("Я Рокси. Пиши мне как голосом: «выключи компьютер через час», «громкость 30», "
        "«поставь таймер на 10 минут», «что на экране».\n"
        "/screen — снимок экрана\n/unlink — отвязать этот телефон")
_NOT_YOURS = "Это личный бот. Код привязки показан в журнале Рокси на компьютере: /start <код>"
PAIR_MAX_FAILS = 5          # неверных кодов привязки подряд — новый код и пауза (код из 6 цифр иначе можно подобрать)
PAIR_LOCK_SEC = 600
PAIR_LOCKED = "Слишком много неверных кодов. Попробуй через 10 минут."


class RoxyBot:
    def __init__(self, token, handle_text, get_owner, set_owner, log=print):
        """handle_text(text, reply, ask, flush) — выполнить команду; reply(str) — ответ в чат;
        ask(question) -> bool|None — спросить да/нет в чате."""
        self.token = token
        self.handle_text = handle_text
        self.get_owner = get_owner
        self.set_owner = set_owner
        self.log = log
        self.offset = None
        self.code = None
        self._pair_fails = 0
        self._pair_locked_until = 0.0
        self._stop = threading.Event()
        self._answers = {}                  # chat_id -> queue ответов на «да/нет»
        self._waiting = set()
        self._lock = threading.Lock()
        self._jobs = []
        self._job_ev = threading.Event()

    # ---------- Bot API ----------
    def _call(self, method, params=None, timeout=40):
        data = urllib.parse.urlencode(params).encode() if params else None   # без параметров — GET
        req = urllib.request.Request(API.format(token=self.token, method=method), data=data)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
        if not res.get("ok"):
            raise RuntimeError(res.get("description", "Telegram error"))
        return res.get("result")

    def send(self, chat_id, text):
        text = (text or "").strip() or "…"
        for i in range(0, len(text), 3900):                # лимит сообщения 4096
            try:
                self._call("sendMessage", {"chat_id": chat_id, "text": text[i:i + 3900]}, timeout=20)
            except Exception as e:
                self.log(f"⚠️ Бот: не отправилось сообщение: {e}")

    def send_photo(self, chat_id, png_bytes, caption=""):
        b = uuid.uuid4().hex
        parts = []
        for k, v in (("chat_id", str(chat_id)), ("caption", caption)):
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
                     f'Content-Type: image/png\r\n\r\n'.encode() + png_bytes + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        req = urllib.request.Request(API.format(token=self.token, method="sendPhoto"), data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()

    # ---------- привязка ----------
    def _pair_code(self):
        if not self.code:
            self.code = f"{random.SystemRandom().randint(0, 999999):06d}"
        return self.code

    # ---------- цикл ----------
    def start(self):
        threading.Thread(target=self._poll, daemon=True, name="tg-bot-poll").start()
        threading.Thread(target=self._worker, daemon=True, name="tg-bot-work").start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        delay = 5
        while not self._stop.is_set():                     # нет сети/таймаут — пробуем снова, а не сдаёмся
            try:
                me = self._call("getMe", timeout=30)
                self.log(f"📱 Telegram-бот @{me.get('username')} на связи")
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 404):
                    self.log("⚠️ Telegram-бот: токен не подошёл — проверь TG_BOT_TOKEN в .env (без пробелов и кавычек)")
                    return
                if e.code == 429:                               # «слишком часто» — ждём, сколько просит Telegram
                    try:
                        delay = max(delay, int(json.loads(e.read().decode())["parameters"]["retry_after"]) + 1)
                    except Exception:
                        pass
                self.log(f"⚠️ Telegram-бот: Telegram ответил {e.code}, повтор через {delay} с")
            except Exception as e:
                px = urllib.request.getproxies()
                self.log(f"⚠️ Telegram-бот: нет ответа от api.telegram.org ({e})"
                         + (f"; системный прокси: {px}" if px else "") + f", повтор через {delay} с")
            time.sleep(delay)
            delay = min(120, delay * 2)
        if not self.get_owner():
            self.log(f"📱 Чтобы привязать телефон, отправь боту:  /start {self._pair_code()}")
        delay = 2
        while not self._stop.is_set():
            try:
                params = {"timeout": 30, "allowed_updates": json.dumps(["message"])}
                if self.offset is not None:
                    params["offset"] = self.offset
                for upd in self._call("getUpdates", params, timeout=45) or []:
                    self.offset = upd["update_id"] + 1
                    self._on_update(upd)
                delay = 2
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    self.log("⚠️ Бот: этим ботом уже опрашивает другая копия Рокси — закрой лишнюю")
                elif e.code == 429:
                    try:
                        delay = max(delay, int(json.loads(e.read().decode())["parameters"]["retry_after"]) + 1)
                    except Exception:
                        pass
                time.sleep(delay)
                delay = min(60, delay * 2)
                continue
            except Exception as e:
                if self._stop.is_set():
                    break
                self.log(f"⚠️ Бот: нет связи с Telegram ({type(e).__name__}), повтор через {delay} с")
                time.sleep(delay)
                delay = min(60, delay * 2)

    def _on_update(self, upd):
        msg = upd.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        user = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat or not user or not text or (msg.get("chat") or {}).get("type") != "private":
            return
        owner = self.get_owner()
        if not owner:
            self._pairing(chat, user, text)
            return
        if int(user) != int(owner):
            self.send(chat, "Это личный бот.")
            return
        with self._lock:
            if chat in self._waiting:                          # ответ на «да/нет»
                self._answers[chat] = text
                return
        if text in ("/start", "/help"):
            self.send(chat, HELP)
            return
        if text == "/unlink":
            self.set_owner(None)
            self.send(chat, "Отвязала. Чтобы привязать снова — код появится в журнале Рокси после перезапуска.")
            return
        self._jobs.append((chat, text))
        self._job_ev.set()

    def _pairing(self, chat, user, text):
        """«/start <код>» от телефона, пока бот ни к кому не привязан. Каждый неверный код — в журнал (кто и когда);
        после PAIR_MAX_FAILS неверных — новый код и пауза PAIR_LOCK_SEC, в паузу не принимается даже верный."""
        parts = text.split()
        guess = parts[-1] if text.startswith("/start") and len(parts) > 1 else None
        if guess is None or not self.code:
            self.send(chat, _NOT_YOURS)
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if time.monotonic() < self._pair_locked_until:
            self.log(f"⚠️ Бот: попытка привязки во время паузы — Telegram ID {user}, {stamp}")
            self.send(chat, PAIR_LOCKED)
            return
        if guess == self.code:
            self.set_owner(int(user))
            self.code = None
            self._pair_fails = 0
            self.log(f"📱 Телефон привязан (Telegram ID {user})")
            self.send(chat, "Готово, теперь я слушаюсь тебя здесь. " + HELP)
            return
        self._pair_fails += 1
        self.log(f"⚠️ Бот: неверный код привязки — Telegram ID {user}, {stamp} "
                 f"(попытка {self._pair_fails} из {PAIR_MAX_FAILS})")
        if self._pair_fails < PAIR_MAX_FAILS:
            self.send(chat, _NOT_YOURS)
            return
        self._pair_fails = 0
        self._pair_locked_until = time.monotonic() + PAIR_LOCK_SEC
        self.code = None
        self.log(f"📱 Бот: {PAIR_MAX_FAILS} неверных кодов подряд — привязка закрыта на {PAIR_LOCK_SEC // 60} минут. "
                 f"Новый код (после паузы): /start {self._pair_code()}")
        self.send(chat, PAIR_LOCKED)

    def _worker(self):
        while not self._stop.is_set():
            if not self._job_ev.wait(0.5):
                continue
            self._job_ev.clear()
            while self._jobs:
                chat, text = self._jobs.pop(0)
                try:
                    if text == "/screen":
                        self._screen(chat)
                    else:
                        self._run(chat, text)
                except Exception as e:
                    self.send(chat, f"Ошибка: {type(e).__name__}: {e}")

    def _screen(self, chat):
        from vision import _mss_grab
        import mss.tools
        shot = _mss_grab()
        png = mss.tools.to_png(shot.rgb, shot.size)
        self.send_photo(chat, png, "Экран сейчас")

    def _run(self, chat, text):
        replies = []

        def reply(t):
            if t and t.strip():
                replies.append(t.strip())

        def ask(question):
            if replies:                                         # то, что уже сказано, — до вопроса
                self.send(chat, "\n".join(replies))
                replies.clear()
            with self._lock:
                self._waiting.add(chat)
                self._answers.pop(chat, None)
            self.send(chat, f"{question}\n(ответь «да» или «нет», жду минуту)")
            try:
                end = time.monotonic() + 60
                while time.monotonic() < end and not self._stop.is_set():
                    with self._lock:
                        ans = self._answers.pop(chat, None)
                    if ans is not None:
                        return ans
                    time.sleep(0.3)
                return None
            finally:
                with self._lock:
                    self._waiting.discard(chat)

        def flush():
            if replies:
                self.send(chat, "\n".join(replies))
                replies.clear()

        try:
            self._call("sendChatAction", {"chat_id": chat, "action": "typing"}, timeout=10)
        except Exception:
            pass
        self.handle_text(text, reply, ask, flush)
        self.send(chat, "\n".join(replies) if replies else "Готово.")


def selftest():
    """Проверка связи с ботом: .venv\\Scripts\\python.exe telegram_bot.py --test (токен не печатается)."""
    import settings
    tok = settings.TG_BOT_TOKEN
    print("токен в .env:", "есть" if tok else "НЕТ", f"(длина {len(tok)})" if tok else "")
    print("прокси:", urllib.request.getproxies() or "нет")
    for fam, name in ((socket.AF_INET, "IPv4"), (socket.AF_INET6, "IPv6")):
        try:
            addrs = sorted({a[4][0] for a in _orig_getaddrinfo(TG_HOST, 443, fam)})
        except OSError as e:
            print(f"{name}: адрес не найден ({e})")
            continue
        t = time.time()
        try:
            c = socket.create_connection((addrs[0], 443), timeout=10)
            c.close()
            print(f"{name}: {addrs[0]} соединение за {time.time() - t:.1f} с")
        except OSError as e:
            print(f"{name}: {addrs[0]} не соединяется ({e})")
    if tok:
        for method in ("GET", "POST"):
            t = time.time()
            try:
                req = urllib.request.Request(API.format(token=tok, method="getMe"),
                                             data=b"" if method == "POST" else None)
                with urllib.request.urlopen(req, timeout=30) as r:
                    me = json.loads(r.read().decode()).get("result", {})
                print(f"getMe ({method}): ок за {time.time() - t:.1f} с — бот @{me.get('username')}")
            except urllib.error.HTTPError as e:
                print(f"getMe ({method}): Telegram ответил {e.code} {e.read()[:200]!r}")
            except Exception as e:
                print(f"getMe ({method}): {type(e).__name__}: {e} ({time.time() - t:.1f} с)")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        selftest()
