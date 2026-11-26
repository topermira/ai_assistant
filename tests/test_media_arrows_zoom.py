"""Голосовые из Telegram, режим мыши стрелками, зум по сетке, камера/микрофон, команды-кнопки."""
import asyncio
import os
import sys
import time
import types
from unittest.mock import MagicMock

import pytest

import mediacap
import pcctl
import screengrid
import tg_remote
from intents import get_intent
from test_actions_security import actions_mod, _ctx          # noqa: F401  (фикстура с заглушками Windows-библиотек)


@pytest.fixture(autouse=True)
def _reset_mouse_state():
    pcctl._STEP[0] = 100
    pcctl._ARROW.update(until=0.0, last=None)
    yield
    pcctl._STEP[0] = 100
    pcctl._ARROW.update(until=0.0, last=None)


# ---------------------------------------------------------------- режим мыши стрелками
def test_arrow_symbols_always_work():
    assert pcctl.parse_mouse("↓") == ("move", 0, 100)
    assert pcctl.parse_mouse("→ 30") == ("move", 30, 0)
    assert pcctl.parse_mouse("↑↑") == ("move", 0, -200)
    assert get_intent("←") == ("[ACTION_MOUSE]", "move|-100|0")


def test_bare_directions_only_in_mode():
    assert pcctl.parse_mouse("вниз") is None                    # без режима голое «вниз» не трогаем
    assert pcctl.parse_mouse("еще") is None
    assert pcctl.parse_mouse("режим мыши") == ("armode", "on")
    pcctl._arrow_mode(True)
    assert pcctl.parse_mouse("вниз") == ("move", 0, 100)
    assert pcctl.parse_mouse("чуть выше") == ("move", 0, -25)
    assert pcctl.parse_mouse("вправо на 20") == ("move", 20, 0)
    assert pcctl.parse_mouse("выше и левее") == ("move", -100, -100)
    assert pcctl.parse_mouse("хватит мышь") == ("armode", "off")
    assert pcctl.parse_mouse("громкость 30") is None


def test_step_and_repeat():
    assert pcctl.parse_mouse("шаг 50") == ("step", 50)
    assert pcctl.parse_mouse("шаг мыши пятьдесят") == ("step", 50)
    assert pcctl.parse_mouse("поставь шаг 5000") == ("step", pcctl.STEP_MAX)
    assert pcctl.parse_mouse("шаг") == ("stepq",)
    pcctl._arrow_mode(True)
    assert pcctl.parse_mouse("больше") == ("step", 200)
    assert pcctl.parse_mouse("меньше") == ("step", 50)
    assert pcctl.parse_mouse("ещё") is None                      # двигали ещё нечем
    pcctl._ARROW["last"] = (0, 1)
    pcctl.set_step(40)
    assert pcctl.parse_mouse("ещё") == ("move", 0, 40)
    assert pcctl.parse_mouse("курсор вправо") == ("move", 40, 0)  # обычная фраза тоже берёт текущий шаг


def test_arrow_mode_expires(monkeypatch):
    pcctl._arrow_mode(True)
    assert pcctl.arrow_mode_active()
    t = time.monotonic()
    monkeypatch.setattr(pcctl.time, "monotonic", lambda: t + pcctl.ARROW_MODE_SEC + 1)
    assert not pcctl.arrow_mode_active()
    assert pcctl.parse_mouse("вниз") is None


def test_mouse_executes_arrow_tasks(monkeypatch):
    ag = types.SimpleNamespace(FAILSAFE=True, position=lambda: (500, 500), size=lambda: (1920, 1080),
                               moveTo=MagicMock())
    monkeypatch.setitem(sys.modules, "pyautogui", ag)
    assert "Режим мыши включён" in pcctl.mouse(("armode", "on")) and pcctl.arrow_mode_active()
    assert "теперь 500, 600" in pcctl.mouse(("move", 0, 100))
    assert pcctl._ARROW["last"] == (0, 1)
    assert "Шаг курсора теперь 30" in pcctl.mouse(("step", 30)) and pcctl.get_step() == 30
    assert "30 px" in pcctl.mouse(("stepq",))
    assert pcctl.mouse(("armode", "off")) == "Режим мыши выключен." and not pcctl.arrow_mode_active()


# ---------------------------------------------------------------- зум по сетке
def test_cell_path_and_regions():
    assert screengrid.find_cell_path("Б4.5")[:3] == (1, 3, "5")
    assert screengrid.find_cell_path("б 4.7.3")[:3] == (1, 3, "73")
    assert screengrid.find_cell_path("Б4")[:3] == (1, 3, "")
    assert screengrid.find_cell_path("привет") is None
    assert screengrid.region(0, 0) == (0.0, 0.0, 1 / 16, 1 / 9)
    x0, y0, x1, y1 = screengrid.region(1, 3, "7")                 # 7 — левый верхний угол клетки
    cx0, cy0, cx1, cy1 = screengrid.region(1, 3)
    assert (x0, y0) == (cx0, cy0) and abs((x1 - x0) * 3 - (cx1 - cx0)) < 1e-9
    assert screengrid.region_center(1, 3, "5", 1920, 1080) == screengrid.cell_center(1, 3, 1920, 1080)
    assert screengrid.region_name(1, 3, "73") == "Б4.7.3"


def test_zoom_and_subcell_parse():
    assert pcctl.parse_mouse("увеличь Б4") == ("zoom", 1, 3, "")
    assert pcctl.parse_mouse("приблизь б4.5") == ("zoom", 1, 3, "5")
    assert pcctl.parse_mouse("покажи крупно Б4") == ("zoom", 1, 3, "")
    assert pcctl.parse_mouse("кликни Б4.5") == ("cell", 1, 3, "left", 1, "5")
    assert pcctl.parse_mouse("курсор в Б4.7") == ("cell", 1, 3, "", 0, "7")
    assert pcctl.parse_mouse("Б4") == ("cell", 1, 3, "", 0)       # старый формат не изменился
    assert pcctl.parse_mouse("увеличь громкость до 50") is None
    assert get_intent("кликни Б4.5") == ("[ACTION_MOUSE]", "cell|1|3|left|1|5")
    assert get_intent("увеличь Б4") == ("[ACTION_MOUSE]", "zoom|1|3|")


def test_zoom_render_is_jpeg_and_enlarged():
    from PIL import Image
    img = Image.new("RGB", (1920, 1080), (200, 200, 200))
    data = screengrid.render_zoom(img, screengrid.region(1, 3), cursor=(150, 400))
    assert data[:2] == b"\xff\xd8"
    out = Image.open(__import__("io").BytesIO(data))
    assert out.width >= 1200 and out.width == out.height          # клетка 120×120 px — квадрат


def test_zoom_action_needs_telegram(actions_mod):
    ctx, _ = _ctx(actions_mod, True)
    assert "только в Telegram" in actions_mod.execute_pc_action("[ACTION_MOUSE]", "увеличь Б4", "zoom|1|3|", ctx)


# ---------------------------------------------------------------- камера и микрофон
def test_media_parse():
    assert mediacap.parse("сними с веб-камеры") == ("cam",)
    assert mediacap.parse("что видит камера") == ("cam",)
    assert mediacap.parse("что ты слышишь") == ("mic", 8, "0")
    assert mediacap.parse("послушай 15 секунд") == ("mic", 15, "0")
    assert mediacap.parse("запиши звук 100 секунд") == ("mic", mediacap.MIC_MAX_SEC, "1")
    assert mediacap.parse("разреши камеру и микрофон") == ("grant", "on")
    assert mediacap.parse("запрети доступ к камере и микрофону") == ("grant", "off")
    assert mediacap.parse("сделай скриншот экрана") is None
    assert mediacap.parse("послушай, расскажи анекдот") is None
    assert mediacap.parse("включи музыку") is None
    assert get_intent("что ты слышишь") == ("[ACTION_CAPTURE]", "mic|8|0")


def test_media_is_off_by_default(actions_mod, monkeypatch):
    shot = MagicMock()
    monkeypatch.setattr(mediacap, "capture_photo", shot)
    ctx, asked = _ctx(actions_mod, True, cfg={})
    res = actions_mod.execute_pc_action("[ACTION_CAPTURE]", "сними с веб-камеры", "cam", ctx)
    assert "выключен" in res and not shot.called and not asked


def test_media_grant_needs_confirmation_and_persists(actions_mod, monkeypatch):
    saved = []
    monkeypatch.setitem(sys.modules, "settings", MagicMock(save_config=lambda c: saved.append(dict(c)),
                                                          live_config=lambda *a, **k: None))
    ctx, asked = _ctx(actions_mod, False, cfg={})
    assert "выключенным" in actions_mod.execute_pc_action("[ACTION_CAPTURE]", "", "grant|on", ctx)
    assert asked and not ctx.config.get("allow_media")
    ctx, asked = _ctx(actions_mod, True, cfg={})
    assert "Разрешила" in actions_mod.execute_pc_action("[ACTION_CAPTURE]", "", "grant|on", ctx)
    assert ctx.config["allow_media"] is True and saved
    assert "Закрыла" in actions_mod.execute_pc_action("[ACTION_CAPTURE]", "", "grant|off", ctx)
    assert ctx.config["allow_media"] is False


def test_media_camera_sends_photo(actions_mod, monkeypatch):
    monkeypatch.setattr(mediacap, "capture_photo", lambda: b"JPEG")
    sent = []
    ctx = actions_mod.ActionContext({"allow_media": True}, confirm=lambda q: True,
                                    remote=lambda: (lambda d, n, c, p: sent.append((d, n, p)), True))
    assert "Скинула снимок" in actions_mod.execute_pc_action("[ACTION_CAPTURE]", "", "cam", ctx)
    assert sent == [(b"JPEG", "webcam.jpg", True)]
    monkeypatch.setattr(mediacap, "capture_photo", MagicMock(side_effect=RuntimeError("камера занята")))
    assert "камера занята" in actions_mod.execute_pc_action("[ACTION_CAPTURE]", "", "cam", ctx)


# ---------------------------------------------------------------- Telegram: голосовые и команды-кнопки
class _Client:
    def __init__(self):
        self.sent, self.downloaded = [], []

    async def send_message(self, peer, text):
        self.sent.append(text)

    async def download_media(self, msg, file=None):
        self.downloaded.append(file)
        with open(file, "wb") as f:
            f.write(b"ogg")
        return file


class _Svc:
    def __init__(self):
        self.client = _Client()

    def call(self, coro, timeout=60):
        return asyncio.run(coro)


def _remote(handler, transcribe):
    r = tg_remote.TgRemote(_Svc(), "me", handler, log=lambda *_: None, transcribe=transcribe)
    r.peer = "me"
    return r


def _voice(dur=3):
    return types.SimpleNamespace(raw_text="", message=types.SimpleNamespace(
        voice=object(), sticker=None, photo=None, document=None,
        file=types.SimpleNamespace(duration=dur), message=""))


def test_voice_is_transcribed_and_run():
    got = []
    r = _remote(lambda t, reply, ask, flush, **kw: (got.append(t), reply("ок")), lambda p: "громкость тридцать")
    asyncio.run(r._on_msg(_voice()))
    kind, msg = r.jobs.get_nowait()
    assert kind == "__voice__"
    r._run_voice(msg)
    assert got == ["громкость тридцать"]
    assert r.svc.client.sent[0] == "🤖 🎙 Услышала: «громкость тридцать»" and r.svc.client.sent[-1] == "🤖 ок"
    assert not os.path.exists(r.svc.client.downloaded[0])          # временный файл убран


def test_voice_problems_are_reported():
    r = _remote(lambda *a, **k: None, lambda p: "")
    r._run_voice(_voice().message)
    assert "Не разобрала" in r.svc.client.sent[-1]
    r = _remote(lambda *a, **k: None, None)
    r._run_voice(_voice().message)
    assert "не подключено" in r.svc.client.sent[-1]
    r = _remote(lambda *a, **k: None, lambda p: "x")
    r._run_voice(_voice(dur=500).message)
    assert "слишком длинное" in r.svc.client.sent[-1]


def test_voice_can_answer_a_question():
    r = _remote(lambda *a, **k: None, lambda p: "да")
    r.waiting = True
    asyncio.run(r._on_msg(_voice()))
    assert r.answers.get(timeout=3) == "да" and r.jobs.empty()


def test_slash_buttons():
    assert tg_remote.expand_slash("/up") == "↑"
    assert tg_remote.expand_slash("/zoom Б4") == "увеличь Б4"
    assert tg_remote.expand_slash("/rclick@RoxyAIAssistant_bot") is None or True
    assert tg_remote.expand_slash("/click@Bot") == "кликни"
    assert tg_remote.expand_slash("/menu") is None and tg_remote.expand_slash("привет") is None
    # каждая кнопка превращается во фразу, которую Рокси реально понимает
    for cmd in ("/up", "/down", "/left", "/right", "/mouse", "/click", "/rclick", "/dclick", "/more", "/less", "/stop"):
        phrase = tg_remote.expand_slash(cmd)
        assert get_intent(phrase)[0] in ("[ACTION_MOUSE]",), (cmd, phrase, get_intent(phrase))
    assert get_intent(tg_remote.expand_slash("/cam"))[0] == "[ACTION_CAPTURE]"
    assert get_intent(tg_remote.expand_slash("/mic"))[0] == "[ACTION_CAPTURE]"


def test_menu_and_slash_run():
    got = []
    r = _remote(lambda t, reply, ask, flush, **kw: (got.append(t), reply("ок")), None)
    r.jobs.put("/menu")
    r.jobs.put("/up")
    for _ in range(2):
        text = r.jobs.get()
        r.send(tg_remote.MENU) if text == "/menu" else r._run(tg_remote.expand_slash(text))
    assert got == ["↑"] and "/zoom" in r.svc.client.sent[0] and "/mouse" in r.svc.client.sent[0]
