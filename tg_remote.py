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
import queue
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
        "/screen — снимок экрана, /grid — снимок с сеткой, /help — эта подсказка.")


class TgRemote:
    def __init__(self, service, chat, handle_text, log=print):
        self.svc = service              # telegram_service.TG_SERVICE (уже подключённый)
        self.chat = chat
        self.handle_text = handle_text
        self.log = log
        self.peer = None
        self.jobs = queue.Queue()
        self.answers = queue.Queue()
        self.waiting = False

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
            self.jobs.put(("__voice__", None))
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
                        self.send("Голосовые пока не понимаю — напиши текстом.")
                elif text in ("/help", "/start"):
                    self.send(HELP)
                elif text in ("/screen", "/grid"):
                    self.send_screen(grid=(text == "/grid"))
                else:
                    self._run(text)
            except Exception as e:
                self.send(f"Ошибка: {type(e).__name__}: {e}")

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
