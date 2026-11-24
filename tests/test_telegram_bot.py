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
