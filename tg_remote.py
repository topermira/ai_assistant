"""Управление Рокси из чата с ботом — но читает она его через ТВОЙ аккаунт Telegram (Telethon, как
«Telegram: сессия найдена»), а не через Bot API бота.

Зачем: Bot API (api.telegram.org) с твоей сети отвечает «429 Too Many Requests» / молчит, а обычный
протокол Telegram, которым работает твой аккаунт, — нет.

Как это выглядит: пишешь своему боту (или в «Избранное») с телефона — «громкость 30». Рокси видит твоё
сообщение через твой аккаунт, выполняет и отвечает в тот же чат строкой «🤖 …» (от твоего же имени —
бот при этом не нужен и может вообще не работать). Чужие сообщения она не читает: только ТВОИ исходящие
в этом одном чате.

Настройка: config.json → "tg_remote_chat": "RoxyAIAssistant_bot" (имя бота без @) или "me" (Избранное).
"""
import io
import os
import queue
import tempfile
import threading
import time

PREFIX = "🤖 "
HELP = ("Пиши мне как голосом. Примеры:\n"
        "• Мышь: «передвинь на 300 вправо», «кликни», «нажми ПКМ», «двойной клик», «прокрути вниз», "
        "«зажми левую» … «отпусти»\n"
        "• Сетка: /grid — снимок с клетками, потом «Б4», «кликни Б4», «нажми правой в Б4»\n"
        "• Клавиши: «нажми Enter», «Ctrl+C», «нажми Alt Tab», «нажми стрелку вниз 3 раза»\n"
        "• Текст: «напиши привет» — туда, где курсор; «положи в буфер: текст», «что в буфере»\n"
        "• Файлы: «пришли файл отчёт.docx»; файл, присланный сюда, сохраню в «Загрузки»\n"
        "• Компьютер: «как дела у компа», «что запущено», «заверши хром», «заблокируй», «переведи в сон», "
        "«выключи компьютер через час», «громкость 30», «яркость 50»\n"
        "• Голосовые сообщения: просто наговори команду — распознаю и выполню\n"
        "• Приблизить клетку: «увеличь Б4» (потом «кликни Б4.5» — клетка делится на 9 частей, как цифры на Num-клавиатуре)\n"
        "• Камера и микрофон (если разрешены): «сними с веб-камеры», «что ты слышишь»\n"
        "/screen — снимок экрана, /grid — снимок с сеткой, /menu — кнопки-команды, /help — эта подсказка.")

MENU = ("Быстрые команды — нажми на нужную:\n"
        "📸 /screen — экран · /grid — экран с сеткой\n"
        "🖱 /mouse — режим мыши (стрелками)\n"
        "      /up /down /left /right — двигать курсор на шаг\n"
        "      /click · /rclick · /dclick — клик, правый, двойной\n"
        "      /more /less — шаг больше/меньше · /stop — выйти из режима мыши\n"
        "🔍 /zoom Б4 — приблизить клетку (напиши после /zoom имя клетки)\n"
        "🎥 /cam — снимок с веб-камеры · 🎙 /mic — что слышно в комнате\n"
        "❔ /help — все примеры фраз")

# нажимаемые команды (Telegram делает «/команду» в тексте кликабельной) → обычная фраза для Рокси.
# Стрелки ↑↓←→ понимаются всегда, а не только в режиме мыши.
SLASH = {
    "/up": "↑", "/down": "↓", "/left": "←", "/right": "→",
    "/mouse": "режим мыши", "/stop": "хватит мышь",
    "/click": "кликни", "/rclick": "кликни правой", "/dclick": "двойной клик",
    "/more": "шаг больше", "/less": "шаг меньше",
    "/zoom": "увеличь", "/cam": "сними с веб-камеры", "/mic": "что ты слышишь",
}

MAX_VOICE_SEC = 90


def expand_slash(text):
    """«/up», «/zoom@Bot Б4» → фраза для Рокси; не наша команда → None."""
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    cmd = head.split("@")[0].lower()
    if cmd not in SLASH:
        return None
    return (SLASH[cmd] + " " + rest.strip()).strip()


class TgRemote:
    def __init__(self, service, chat, handle_text, log=print, transcribe=None):
        self.svc = service              # telegram_service.TG_SERVICE (уже подключённый)
        self.chat = chat
        self.handle_text = handle_text
        self.log = log
        self.peer = None
        self.jobs = queue.Queue()
        self.answers = queue.Queue()
        self.waiting = False
        self.transcribe = transcribe     # путь к аудио → текст (stt.transcribe); None — голосовые не поддерживаются

    # ---------- запуск ----------
    def start(self):
        from telethon import events

        async def setup():
            self.peer = await self.svc.client.get_input_entity(self.chat)
            ent = await self.svc.client.get_entity(self.peer)
            self.svc.client.add_event_handler(self._on_msg, events.NewMessage(chats=[self.peer], outgoing=True))
            return getattr(ent, "username", None) or getattr(ent, "first_name", None) or self.chat

        name = self.svc.call(setup(), timeout=30)
        self.log(f"📱 Управление с телефона: пиши в чат «{name}» — Рокси читает его через твой аккаунт")
        threading.Thread(target=self._worker, daemon=True, name="tg-remote").start()

    async def _on_msg(self, event):
        text = (event.raw_text or "").strip()
        if text.startswith(PREFIX.strip()):                    # свои ответы (и подписи к своим снимкам) не читаем
            return
        msg = getattr(event, "message", None)
        if getattr(msg, "voice", None):
            if self.waiting:                                    # голосом можно ответить «да/нет» на вопрос
                threading.Thread(target=self._voice_answer, args=(msg,), daemon=True, name="tg-voice").start()
            else:
                self.jobs.put(("__voice__", msg))
            return
        if getattr(msg, "sticker", None):
            return
        if getattr(msg, "photo", None) or getattr(msg, "document", None):
            self.jobs.put(("__file__", msg))                  # файл с телефона → «Загрузки»
            return
        if not text:
            return
        if self.waiting:
            self.answers.put(text)
            return
        self.jobs.put(text)

    # ---------- отправка ----------
    def send(self, text):
        text = (text or "").strip() or "…"

        async def _send():
            for i in range(0, len(text), 3900):
                await self.svc.client.send_message(self.peer, PREFIX + text[i:i + 3900])
        try:
            self.svc.call(_send(), timeout=30)
        except Exception as e:
            self.log(f"⚠️ Ответ в Telegram не ушёл: {e}")

    def send_file(self, data, name="file", caption="", photo=False):
        """data — bytes или путь к файлу. photo=True — как фото (сжатое превью), иначе документом (как есть)."""
        if isinstance(data, (bytes, bytearray)):
            src = io.BytesIO(data)
            src.name = name
        else:
            src = data

        async def _send():
            await self.svc.client.send_file(self.peer, src, caption=PREFIX + (caption or name),
                                            force_document=not photo)
        size = len(data) if isinstance(data, (bytes, bytearray)) else 0
        self.svc.call(_send(), timeout=max(90, 60 + size // (256 * 1024)))

    def send_screen(self, grid=False):
        import screengrid
        data, cap = screengrid.screenshot(grid=grid)
        self.send_file(data, "screen.jpg", cap, photo=True)

    # ---------- голосовые ----------
    def _voice_to_text(self, msg):
        """Голосовое из чата → текст. Бросает RuntimeError с понятным текстом."""
        if self.transcribe is None:
            raise RuntimeError("распознавание голосовых не подключено — напиши текстом")
        f = getattr(msg, "file", None)
        dur = getattr(f, "duration", None) or 0
        if dur and dur > MAX_VOICE_SEC:
            raise RuntimeError(f"голосовое слишком длинное ({int(dur)} с, максимум {MAX_VOICE_SEC})")
        try:
            from settings import TEMP_DIR
            os.makedirs(TEMP_DIR, exist_ok=True)
            folder = TEMP_DIR
        except Exception:
            folder = tempfile.gettempdir()
        path = os.path.join(folder, f"tg_voice_{int(time.time() * 1000)}.ogg")

        async def _dl():
            return await self.svc.client.download_media(msg, file=path)
        got = self.svc.call(_dl(), timeout=90)
        try:
            text = (self.transcribe(got or path) or "").strip()
        finally:
            try:
                os.remove(got or path)
            except OSError:
                pass
        return text

    def _voice_answer(self, msg):
        try:
            text = self._voice_to_text(msg)
        except Exception as e:
            self.log(f"⚠️ Голосовой ответ не разобрала: {e}")
            return
        if text:
            self.log(f"🎙 Ответ голосом: {text}")
            self.answers.put(text)

    def _save_incoming(self, msg):
        """Файл из чата → «Загрузки» (с «(1)», если имя занято)."""
        from pathsafe import safe_filename
        from phonefiles import unique_path
        try:
            from winutil import get_known_folder
            folder = get_known_folder("Downloads")
        except Exception:
            folder = None
        import os
        folder = folder or os.path.join(os.path.expanduser("~"), "Downloads")
        f = getattr(msg, "file", None)
        name = getattr(f, "name", None) if f else None
        if not name:
            ext = (getattr(f, "ext", None) or ".jpg") if f else ".jpg"
            name = time.strftime("telegram_%Y-%m-%d_%H-%M-%S") + ext
        target = unique_path(folder, safe_filename(name) or "file")

        async def _dl():
            return await self.svc.client.download_media(msg, file=target)
        size = getattr(f, "size", 0) or 0
        path = self.svc.call(_dl(), timeout=max(120, 60 + size // (256 * 1024)))
        self.log(f"📥 Из Telegram сохранила: {path}")
        caption = (msg.message or "").strip()
        self.send(f"Сохранила в «Загрузки»: {os.path.basename(path or target)}"
                  + (" (подпись к файлу не выполняю как команду)" if caption else ""))

    # ---------- выполнение ----------
    def _worker(self):
        while True:
            text = self.jobs.get()
            try:
                if isinstance(text, tuple):
                    kind, msg = text
                    if kind == "__file__":
                        self._save_incoming(msg)
                    else:
                        self._run_voice(msg)
                elif text in ("/help", "/start"):
                    self.send(HELP)
                elif text in ("/menu", "/m"):
                    self.send(MENU)
                elif text in ("/screen", "/grid"):
                    self.send_screen(grid=(text == "/grid"))
                elif text.split("@")[0].strip().lower() == "/zoom":
                    self.send("Напиши, какую клетку приблизить, например: /zoom Б4 (клетки видно на снимке /grid). "
                              "Потом можно ещё ближе: /zoom Б4.5")
                elif expand_slash(text):
                    self._run(expand_slash(text))
                else:
                    self._run(text)
            except Exception as e:
                self.send(f"Ошибка: {type(e).__name__}: {e}")

    def _run_voice(self, msg):
        try:
            text = self._voice_to_text(msg)
        except Exception as e:
            self.send(f"Голосовое не получилось: {e}")
            return
        if not text:
            self.send("Не разобрала, что сказано в голосовом. Скажи ещё раз или напиши текстом.")
            return
        self.log(f"🎙 Голосовое из Telegram: {text}")
        self.send(f"🎙 Услышала: «{text}»")
        self._run(text)

    def _run(self, text):
        replies = []

        def reply(t):
            if t and t.strip():
                replies.append(t.strip())

        def flush():
            if replies:
                self.send("\n".join(replies))
                replies.clear()

        def ask(question):
            flush()
            while not self.answers.empty():
                self.answers.get_nowait()
            self.waiting = True
            try:
                self.send(f"{question}\n(ответь «да» или «нет», жду минуту)")
                try:
                    return self.answers.get(timeout=60)
                except queue.Empty:
                    return None
            finally:
                self.waiting = False

        def send_file(data, name, caption="", photo=False):
            flush()                                  # сначала текст, который уже сказан, потом файл
            self.send_file(data, name, caption, photo)

        self.handle_text(text, reply, ask, flush, send_file=send_file)
        self.send("\n".join(replies) if replies else "Готово.")
