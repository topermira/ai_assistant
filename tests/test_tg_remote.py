import asyncio
import threading
import types

import tg_remote


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, peer, text):
        self.sent.append(text)


class FakeSvc:
    def __init__(self):
        self.client = FakeClient()

    def call(self, coro, timeout=60):
        return asyncio.run(coro)


def ev(text):
    return types.SimpleNamespace(raw_text=text)


def make(handler):
    r = tg_remote.TgRemote(FakeSvc(), "me", handler, log=lambda *_: None)
    r.peer = "me"
    return r


def test_own_replies_ignored_and_command_runs():
    got = []
    r = make(lambda t, reply, ask, flush, **kw: (got.append(t), reply("ок")))
    asyncio.run(r._on_msg(ev("🤖 старый ответ")))
    asyncio.run(r._on_msg(ev("громкость 30")))
    assert r.jobs.qsize() == 1
    r._run(r.jobs.get())
    assert got == ["громкость 30"] and r.svc.client.sent[-1] == "🤖 ок"


def test_confirmation_reads_next_message():
    def handler(t, reply, ask, flush, **kw):
        reply("да!" if ask("Точно?") == "да" else "отмена")
    r = make(handler)
    th = threading.Thread(target=r._run, args=("выключи комп",))
    th.start()
    for _ in range(200):
        if r.waiting:
            break
        threading.Event().wait(0.01)
    asyncio.run(r._on_msg(ev("да")))
    th.join(3)
    assert r.svc.client.sent[-1] == "🤖 да!" and r.jobs.empty()


def test_files_from_phone_are_queued_and_own_photos_ignored():
    r = make(lambda *a, **kw: None)
    photo = types.SimpleNamespace(raw_text="", message=types.SimpleNamespace(photo=object(), document=None,
                                                                              voice=None, sticker=None))
    mine = types.SimpleNamespace(raw_text="🤖 Курсор в Б4", message=types.SimpleNamespace(photo=object()))
    asyncio.run(r._on_msg(mine))
    assert r.jobs.empty()
    asyncio.run(r._on_msg(photo))
    kind, _ = r.jobs.get_nowait()
    assert kind == "__file__"


def fwd(text, **media):
    m = types.SimpleNamespace(fwd_from=object(), voice=None, sticker=None, photo=None, document=None)
    for k, v in media.items():
        setattr(m, k, v)
    return types.SimpleNamespace(raw_text=text, message=m)


def test_forwarded_messages_are_not_commands():
    r = make(lambda *a, **kw: None)
    asyncio.run(r._on_msg(fwd("удали файл отчёт")))
    asyncio.run(r._on_msg(fwd("", voice=object())))
    assert r.jobs.empty()
    r.waiting = True                                       # и ответом «да» на вопрос пересланное не считается
    asyncio.run(r._on_msg(fwd("да")))
    assert r.answers.empty()
    r.waiting = False
    asyncio.run(r._on_msg(fwd("", document=object())))     # пересланный файл — в «Загрузки», как раньше
    assert r.jobs.get_nowait()[0] == "__file__"
    asyncio.run(r._on_msg(ev("громкость 30")))            # своё сообщение — команда
    assert r.jobs.get_nowait() == "громкость 30"


def test_stopped_remote_ignores_messages():
    r = make(lambda *a, **kw: None)
    r.stop()                                               # у FakeSvc нет loop — снятие обработчика молча пропускается
    asyncio.run(r._on_msg(ev("громкость 30")))
    assert r.jobs.empty()


# ---------------------------------------------------------------- один канал управления, переподключение
import telegram_service  # noqa: E402


def _quiet(*_):
    pass


def test_phone_channel_uses_bot_when_account_not_configured():
    bot = []
    ch = telegram_service.PhoneChannel(lambda: False, lambda: False, lambda: bot.append(1) or True,
                                       retry_every=0.01, log=_quiet)
    assert ch.start() and not ch.start()                   # второй запуск ничего не делает
    ch.join(2)
    assert bot == [1] and ch.channel == "bot"


def test_phone_channel_waits_for_account_and_never_starts_bot():
    tries, bot = [], []

    def remote():
        tries.append(1)
        return len(tries) >= 3                             # сеть появилась к третьей попытке

    ch = telegram_service.PhoneChannel(remote, lambda: True, lambda: bot.append(1) or True,
                                       retry_every=0.01, log=_quiet)
    ch.start()
    ch.join(2)
    assert len(tries) == 3 and bot == [] and ch.channel == "account"


def test_phone_channel_started_from_many_threads_runs_once():
    calls, gate = [], threading.Event()

    def remote():
        calls.append(1)
        gate.wait(2)
        return True

    ch = telegram_service.PhoneChannel(remote, lambda: False, lambda: True, retry_every=0.01, log=_quiet)
    starters = [threading.Thread(target=ch.start) for _ in range(5)]
    for t in starters:
        t.start()
    for t in starters:
        t.join(2)
    gate.set()
    ch.join(2)
    assert calls == [1] and ch.channel == "account"


def test_phone_channel_stops_on_exit_without_bot():
    stop, bot = threading.Event(), []
    ch = telegram_service.PhoneChannel(lambda: False, lambda: True, lambda: bot.append(1) or True,
                                       retry_every=10, stop=stop, log=_quiet)
    ch.start()
    stop.set()
    ch.join(2)
    assert bot == [] and ch.channel is None


def test_telegram_client_reconnects_forever(monkeypatch):
    seen = {}

    class FakeTgClient:
        def __init__(self, *a, **kw):
            seen.update(kw)

        async def connect(self):
            await asyncio.sleep(0.2)

        async def is_user_authorized(self):
            return True

    monkeypatch.setattr(telegram_service, "TelegramClient", FakeTgClient)
    svc = telegram_service.TelegramService()
    assert not svc.pending()
    svc.start(1, "hash", "session")
    assert svc.pending()                                   # подключается — не ошибка, а «подожди»
    assert svc.ready.wait(5) and not svc.error and not svc.pending()
    assert seen["connection_retries"] == -1 and seen["retry_delay"] == 5
    svc.loop.call_soon_threadsafe(svc.loop.stop)
    svc.thread.join(5)


def test_send_file_passes_through_to_chat():
    calls = []

    class C(FakeClient):
        async def send_file(self, peer, f, caption="", force_document=False):
            calls.append((getattr(f, "name", f), caption, force_document))
    r = make(lambda t, reply, ask, flush, send_file=None: send_file(b"jpg", "s.jpg", "Курсор в Б4", True))
    r.svc.client = C()
    r._run("кликни")
    assert calls == [("s.jpg", "🤖 Курсор в Б4", False)]
