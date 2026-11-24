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


def test_send_file_passes_through_to_chat():
    calls = []

    class C(FakeClient):
        async def send_file(self, peer, f, caption="", force_document=False):
            calls.append((getattr(f, "name", f), caption, force_document))
    r = make(lambda t, reply, ask, flush, send_file=None: send_file(b"jpg", "s.jpg", "Курсор в Б4", True))
    r.svc.client = C()
    r._run("кликни")
    assert calls == [("s.jpg", "🤖 Курсор в Б4", False)]
