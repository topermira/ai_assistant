import telegram_bot


class FakeBot(telegram_bot.RoxyBot):
    def __init__(self, owner=None, **kw):
        self.owner = owner
        self.sent = []
        self.handled = []
        super().__init__("T", self._handle, lambda: self.owner, self._set, log=lambda *_: None)

    def _set(self, v):
        self.owner = v

    def _handle(self, text, reply, ask, flush):
        self.handled.append(text)
        if text == "опасно":
            reply("ок" if ask("Точно?") == "да" else "нет")
        else:
            reply("ответ: " + text)

    def _call(self, method, params=None, timeout=40):
        return {}

    def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def upd(text, uid=5, chat=5):
    return {"update_id": 1, "message": {"chat": {"id": chat, "type": "private"}, "from": {"id": uid}, "text": text}}


def test_pairing_and_strangers():
    b = FakeBot()
    code = b._pair_code()
    b._on_update(upd("/start 000000" if code != "000000" else "/start 111111"))
    assert b.owner is None
    b._on_update(upd(f"/start {code}"))
    assert b.owner == 5
    b._on_update(upd("громкость 30", uid=9, chat=9))
    assert b.sent[-1] == (9, "Это личный бот.") and not b._jobs


def test_command_and_reply():
    b = FakeBot(owner=5)
    b._on_update(upd("громкость 30"))
    chat, text = b._jobs.pop(0)
    b._run(chat, text)
    assert b.handled == ["громкость 30"] and b.sent[-1] == (5, "ответ: громкость 30")


def test_confirm_in_chat():
    import threading
    b = FakeBot(owner=5)
    t = threading.Thread(target=b._run, args=(5, "опасно"))
    t.start()
    for _ in range(100):
        if 5 in b._waiting:
            break
        threading.Event().wait(0.02)
    b._on_update(upd("да"))
    t.join(3)
    assert b.sent[-1] == (5, "ок")


# ---------------------------------------------------------------- «пока» из Telegram Рокси не выключает
import importlib  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def main_mod(monkeypatch):
    """main.py без звука, окон и сети: тяжёлые модули — заглушки (gui.install_redirect тоже)."""
    before = set(sys.modules)
    for name in ("gui", "keyboard", "audio", "stt", "bargein", "llm", "actions", "telegram_service", "winutil",
                 "live2d_client", "library"):
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    sys.modules.pop("main", None)
    mod = importlib.import_module("main")
    yield mod
    for name in set(sys.modules) - before:          # модули проекта, импортированные поверх заглушек, — убрать
        f = getattr(sys.modules.get(name), "__file__", None)
        if isinstance(f, str) and os.path.abspath(f).startswith(ROOT + os.sep) and os.sep + ".venv" + os.sep not in f:
            sys.modules.pop(name, None)


class _Stopped(Exception):
    pass


def test_stop_words_from_telegram_keep_roxy_running(main_mod, monkeypatch):
    calls, replies = [], []
    monkeypatch.setattr(main_mod, "shutdown", lambda: calls.append("shutdown"))
    main_mod._sink.reply, main_mod._sink.flush = replies.append, lambda: None
    try:
        for text in ("пока", "спать", "выход", "Рокси, стоп"):
            main_mod.handle(text)
    finally:
        main_mod._sink.reply = main_mod._sink.flush = None
    assert calls == []
    assert replies == ["Выключить Рокси можно только с компьютера или голосом."] * 4


def test_stop_by_voice_still_shuts_down(main_mod, monkeypatch):
    def stop():
        raise _Stopped()
    monkeypatch.setattr(main_mod, "shutdown", stop)
    with pytest.raises(_Stopped):
        main_mod.handle("пока")
