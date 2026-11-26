"""Telegram через Telethon (отдельный поток со своим event loop).

Изменения: строгий поиск контакта (раньше «target in name» и fuzzy 0.55 находили кого попало),
при нескольких похожих — список для уточнения; отправка и ответ разделены на
«подготовить» и «отправить», чтобы между ними было голосовое подтверждение.
"""
import os
import time
import asyncio
import difflib
import threading

try:
    from telethon import TelegramClient
    from telethon.tl.types import User as _TgUser
    _TELEGRAM_OK = True
except ImportError:
    TelegramClient = None
    _TgUser = None
    _TELEGRAM_OK = False
    print("⚠️ telethon не установлен. Установите: python -m pip install telethon")

from settings import TG_SESSION_FILE, get_tg_credentials


# ==================== УПРАВЛЕНИЕ С ТЕЛЕФОНА: РОВНО ОДИН КАНАЛ ====================
class PhoneChannel:
    """Управление с телефона — ровно одним каналом: через свой аккаунт (TgRemote) или, если он не настроен или не может
    работать, через бота (Bot API). Оба сразу нельзя: одно сообщение боту выполнилось бы дважды.

    Запуск — из одного потока, повторный start() ничего не запускает. Пока аккаунт подключается (нет сети при
    автозапуске), пробуем снова раз в retry_every секунд, а бота не включаем — иначе, когда сеть появится, работали бы оба.
    start_remote() -> bool (запущен), remote_pending() -> bool (ещё подключается), start_bot() -> bool (запущен)."""

    def __init__(self, start_remote, remote_pending, start_bot, retry_every=60.0, stop=None, log=print):
        self._start_remote = start_remote
        self._remote_pending = remote_pending
        self._start_bot = start_bot
        self.retry_every = retry_every
        self._stop = stop or threading.Event()
        self.log = log
        self._lock = threading.Lock()
        self._thread = None
        self.channel = None                    # "account" | "bot" | None

    def start(self):
        with self._lock:
            if self._thread is not None:
                return False
            self._thread = threading.Thread(target=self._run, daemon=True, name="tg-start")
        self._thread.start()
        return True

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self):
        told = False
        while not self._stop.is_set():
            if self._start_remote():
                self.channel = "account"
                return
            if not self._remote_pending():
                break
            if not told:
                told = True
                self.log(f"📱 Telegram ещё подключается — жду сеть, проверяю раз в {int(self.retry_every)} с")
            if self._stop.wait(self.retry_every):
                return
        if not self._stop.is_set() and self._start_bot():
            self.channel = "bot"


# ==================== СОПОСТАВЛЕНИЕ ИМЁН (чистое, есть тесты) ====================
def _word_match(t, n):
    """«даниэлю» ~ «даниэль», «васе» ~ «вася», но «маша» !~ «максим»."""
    if len(t) < 3 or len(n) < 3:
        return t == n
    if abs(len(t) - len(n)) > 3:
        return False
    k = max(3, min(len(t), len(n)) - 2)
    return t[:k] == n[:k]


def match_score(target, name, username=""):
    """0..100. 0 — не подходит."""
    t = (target or "").lower().lstrip("@").strip().replace("ё", "е")
    n = (name or "").lower().strip().replace("ё", "е")
    u = (username or "").lower()
    if not t:
        return 0
    if u and t == u:
        return 100
    if n and t == n:
        return 90
    t_words = [w for w in t.split() if w]
    n_words = n.split()
    if t_words and n_words and all(any(_word_match(tw, nw) for nw in n_words) for tw in t_words):
        return 70 + (10 if len(t_words) == len(n_words) else 0)
    if n and difflib.SequenceMatcher(None, t, n).ratio() >= 0.85:
        return 50
    return 0


def pick_best(scored, min_score=50):
    """scored: [(score, label, obj)] → (obj, label) | (None, [labels]) при неоднозначности | (None, [])."""
    scored = sorted([s for s in scored if s[0] >= min_score], key=lambda s: -s[0])
    if not scored:
        return None, []
    top = scored[0][0]
    best = [s for s in scored if s[0] == top]
    if len(best) == 1:
        return best[0][2], best[0][1]
    return None, [s[1] for s in best[:5]]


def _sender_name(m):
    if m.out:
        return "Я"
    s = getattr(m, "sender", None)
    return getattr(s, "first_name", None) or "Собеседник"


class TelegramService:
    def __init__(self):
        self.loop = None
        self.client = None
        self.thread = None
        self.ready = threading.Event()
        self.error = None
        self._dialogs = None          # кэш списка диалогов: iter_dialogs() по всем чатам при каждой команде был медленным
        self._dialogs_at = 0.0

    def start(self, api_id, api_hash, session_name):
        if self.thread is not None:
            return
        self.loop = asyncio.new_event_loop()

        def runner():
            try:
                asyncio.set_event_loop(self.loop)
                # connection_retries=-1: без сети (автозапуск раньше Wi-Fi, обрыв, сон ПК) Telethon переподключается
                # бесконечно, раз в 5 с. Раньше после 5 попыток клиент отключался навсегда — до перезапуска Рокси.
                self.client = TelegramClient(session_name, api_id, api_hash, loop=self.loop,
                                             connection_retries=-1, retry_delay=5)
                self.loop.run_until_complete(self.client.connect())
                if not self.loop.run_until_complete(self.client.is_user_authorized()):
                    self.error = "сессия не авторизована. Запусти: python telegram_auth.py"
                    print(f"❌ {self.error}")
                    self.ready.set()
                    return
                print("✅ Telegram-клиент подключён (сессия валидна).")
                self.ready.set()
                self.loop.run_forever()
            except Exception as e:
                self.error = e
                print(f"❌ Telegram start: {e}")
                self.ready.set()

        self.thread = threading.Thread(target=runner, daemon=True, name="telegram")
        self.thread.start()

    def pending(self):
        """Клиент запущен и ещё подключается (нет сети) — не ошибка, а «подожди»."""
        return self.thread is not None and not self.ready.is_set() and not self.error

    def call(self, coro, timeout=60):
        if not self.ready.wait(10):
            raise RuntimeError("Telegram не готов (таймаут инициализации)")
        if self.error:
            raise RuntimeError(f"Telegram: {self.error}")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    @staticmethod
    def _score_dialogs(dialogs, name_or_username):
        scored = []
        for d in dialogs:
            ent = d.entity
            if _TgUser and isinstance(ent, _TgUser):
                full = f"{ent.first_name or ''} {ent.last_name or ''}".strip()
                s = match_score(name_or_username, full, ent.username or "")
                label = full or (ent.username or "")
            else:
                s = match_score(name_or_username, d.name or "")
                s = s if s >= 70 else 0          # группы/каналы — только точнее
                label = d.name or ""
            if s:
                scored.append((s, label, d))
        return pick_best(scored)

    async def _find_internal(self, name_or_username):
        ttl = 300.0
        stale = self._dialogs is None or time.monotonic() - self._dialogs_at > ttl
        if stale:
            self._dialogs = [d async for d in self.client.iter_dialogs()]
            self._dialogs_at = time.monotonic()
        res = self._score_dialogs(self._dialogs, name_or_username)
        if res[0] is None and not res[1] and not stale:
            # в кэше не нашли — возможно, чат появился недавно: обновляем список и пробуем ещё раз
            self._dialogs = [d async for d in self.client.iter_dialogs()]
            self._dialogs_at = time.monotonic()
            res = self._score_dialogs(self._dialogs, name_or_username)
        return res

    def find_dialog(self, name_or_username):
        """→ (dialog, label) | (None, [варианты]) | (None, [])."""
        if self.error or not self.ready.is_set():
            return None, []
        return self.call(self._find_internal(name_or_username))

    def send_to_dialog(self, dialog, label, text):
        async def _send():
            await self.client.send_message(dialog.entity, text)
            return f"Отправила {label}: «{text[:120]}»"
        try:
            return self.call(_send())
        except Exception as e:
            return f"Ошибка отправки: {e}"

    def read_last(self, dialog, limit=10):
        async def _read():
            msgs = []
            async for m in self.client.iter_messages(dialog.entity, limit=limit):
                msgs.append(f"{_sender_name(m)}: {m.text or '[вложение]'}")
            msgs.reverse()
            return msgs
        return self.call(_read())


TG_SERVICE = TelegramService()


def _tg_session_exists():
    return os.path.exists(TG_SESSION_FILE + ".session")


def _init_telegram_if_needed(config):
    if not _TELEGRAM_OK: return False, "telethon не установлен"
    if TG_SERVICE.thread is not None:
        if TG_SERVICE.error: return False, f"Telegram: {TG_SERVICE.error}"
        if not TG_SERVICE.ready.is_set(): return False, "Telegram ещё инициализируется"
        return True, "ok"
    if not _tg_session_exists():
        return False, "нет файла сессии. Запусти: python telegram_auth.py"
    api_id, api_hash = get_tg_credentials(config)
    if not api_id or not api_hash:
        return False, "TG_API_ID/TG_API_HASH не заданы (впиши их в .env)"
    try:
        TG_SERVICE.start(api_id, api_hash, TG_SESSION_FILE)
        if not TG_SERVICE.ready.wait(15): return False, "Telegram не подключился за 15 сек"
        if TG_SERVICE.error: return False, f"Telegram: {TG_SERVICE.error}"
        return True, "запущен"
    except Exception as e:
        return False, str(e)
