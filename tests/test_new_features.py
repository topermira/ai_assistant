"""Тесты правок после код-ревью: чат-префиксы, время/дата, погода на завтра, пути, питомец."""
import os
import sys
import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intents import get_intent, match_pet  # noqa: E402
import clock  # noqa: E402
import pathsafe  # noqa: E402
import weather  # noqa: E402


# ---------- intents ----------
@pytest.mark.parametrize("phrase", ["нетфликс открой", "нетбук включи", "где мои файлы, открой папку"])
def test_chat_prefix_needs_word_boundary(phrase):
    # раньше фраза обрубалась на «нет…»/«где…» и уходила в болталку, не дойдя до роутера
    intent, _ = get_intent(phrase, llm_router=lambda t: ("[ACTION_OPEN]", ""))
    assert intent != "[CHAT]", phrase


@pytest.mark.parametrize("phrase", ["где находится париж", "нет, не надо", "почему небо синее", "что такое питон"])
def test_real_chat_questions_still_chat(phrase):
    assert get_intent(phrase)[0] == "[CHAT]", phrase


@pytest.mark.parametrize("phrase", ["объясни ошибку", "Рокси, что за ошибка", "посмотри на экран"])
def test_screen_phrases_beat_chat(phrase):
    assert get_intent(phrase)[0] == "[ACTION_SCREEN]", phrase


@pytest.mark.parametrize("phrase,kind", [
    ("который час", "time"), ("сколько сейчас времени", "time"), ("какое сегодня число", "date"),
    ("какой сегодня день недели", "date"), ("скажи какое число и который час", "both"),
])
def test_time_intent(phrase, kind):
    assert get_intent(phrase) == ("[ACTION_TIME]", kind)


def test_timer_phrases_not_stolen_by_time():
    assert get_intent("сколько осталось на таймере")[0] == "[ACTION_TIMER_LIST]"
    assert get_intent("поставь таймер на 5 минут")[0] == "[ACTION_TIMER]"


@pytest.mark.parametrize("phrase,name", [("зевни", "yawn"), ("потянись", "stretch"), ("оглянись", "look_around"),
                                         ("осмотрись вокруг", "look_around"), ("посмотри по сторонам", "look_around"),
                                         ("обопрись о стену", "lean"), ("прислонись к краю", "lean")])
def test_pet_new_commands(phrase, name):
    assert match_pet(phrase) == name


# ---------- clock ----------
def test_say_time_plural_forms():
    t = lambda h, m: clock.say_time(datetime.datetime(2026, 11, 20, h, m))   # noqa: E731
    assert t(1, 1) == "Сейчас 1 час 1 минута."
    assert t(2, 2) == "Сейчас 2 часа 2 минуты."
    assert t(11, 11) == "Сейчас 11 часов 11 минут."
    assert t(14, 0) == "Сейчас ровно 14 часов."
    assert t(21, 21) == "Сейчас 21 час 21 минута."


def test_say_date_and_prompt():
    d = datetime.datetime(2026, 11, 20, 0, 20)          # пятница
    assert clock.say_date(d) == "Сегодня пятница, 20 ноября 2026 года."
    assert "пятница" in clock.now_for_prompt(d) and "00:20" in clock.now_for_prompt(d)
    assert clock.say_now("both", d).startswith("Сегодня пятница")


# ---------- weather ----------
def test_weather_day_parse():
    assert weather.parse_weather_day("какая погода завтра") == 1
    assert weather.parse_weather_day("погода послезавтра в Москве") == 2
    assert weather.parse_weather_day("какая погода") == 0


@pytest.mark.parametrize("phrase,city", [
    ("какая погода завтра в Москве", "Москве"),
    ("погода на завтра в Казани", "Казани"),
    ("какая погода в Астане", "Астане"),
    ("какая погода завтра", None),
    ("погода на сегодня", None),
])
def test_weather_city_with_day_words(phrase, city):
    assert weather._extract_weather_city(phrase) == city


def test_forecast_format():
    hourly = [{"weatherDesc": [{"value": "Light snow"}], "chanceofrain": "0", "chanceofsnow": "70"} for _ in range(8)]
    data = {"weather": [{}, {"mintempC": "-5", "maxtempC": "-1", "hourly": hourly}, {}]}
    out = weather._format_forecast(data, "Москве", 1)
    assert out.startswith("Завтра в Москве: днём небольшой снег")
    assert "от -5 до -1°C" in out and "вероятность снега 70%" in out
    assert weather._format_forecast({"weather": []}, None, 1) is None


# ---------- пути ----------
def test_safe_filename():
    assert pathsafe.safe_filename("..\\..\\evil:name?.txt") == "evilname.txt"
    assert pathsafe.safe_filename("con.txt") == ""
    assert pathsafe.safe_filename("...") == ""
    assert pathsafe.safe_filename("отчёт.txt") == "отчёт.txt"


def test_protected_paths(tmp_path):
    home = os.path.expanduser("~")
    assert pathsafe.is_protected_path(os.path.join(home, "Documents"))
    assert pathsafe.is_protected_path(home)
    assert pathsafe.is_protected_path(os.path.abspath(os.sep))
    assert not pathsafe.is_protected_path(os.path.join(home, "Documents", "old_reports"))
    assert pathsafe.is_protected_path(str(tmp_path), extra=(str(tmp_path),))
    assert pathsafe.is_protected_path(str(tmp_path / "sub"), project_dir=str(tmp_path / "sub"))
    assert pathsafe.is_protected_path("")


# ---------- ActionContext: подтверждения ----------
from actionctx import ActionContext  # noqa: E402


def test_context_without_confirm_denies():
    ctx = ActionContext({})                       # раньше такой контекст молча разрешал всё
    assert ctx.confirm("Удалить?") is False
    assert ctx.confirm("Отправить?", force=True) is False


def test_context_confirm_dangerous_off_skips_normal_but_not_forced():
    asked = []
    ctx = ActionContext({"confirm_dangerous": False}, confirm=lambda q: asked.append(q) or False)
    assert ctx.confirm("обычное") is True and not asked          # пользователь отключил вопросы
    assert ctx.confirm("telegram", force=True) is False and asked == ["telegram"]   # но Telegram/облако — всегда


def test_context_normal_flow():
    ctx = ActionContext({"confirm_dangerous": True}, confirm=lambda q: True)
    assert ctx.confirm("ok") is True


# ---------- защита Vision-агента ----------
import agent_guard  # noqa: E402


@pytest.mark.parametrize("action", [
    {"action": "hotkey", "keys": ["win", "r"]}, {"action": "hotkey", "keys": ["alt", "f4"]},
    {"action": "hotkey", "keys": ["ctrl", "shift", "esc"]}, {"action": "press", "key": "win"},
    {"action": "press", "key": "winleft"}, {"action": "type", "text": "x" * 501},
    {"action": "hotkey", "keys": "win+r"}, {"action": "click", "x": "100", "y": 5}, {"action": "format_c"},
    {"action": "type", "text": ""}, {"action": "scroll", "clicks": 9999},
    {"action": "hotkey", "keys": ["ctrl", "esc"]}, {"action": "hotkey", "keys": ["Ctrl", "Escape"]},
])
def test_agent_guard_blocks(action):
    ok, why = agent_guard.validate_action(action)
    assert not ok and why


@pytest.mark.parametrize("action", [
    {"action": "click", "x": 10, "y": 20}, {"action": "type", "text": "привет"}, {"action": "press", "key": "enter"},
    {"action": "hotkey", "keys": ["ctrl", "s"]}, {"action": "scroll", "clicks": -3, "x": 1, "y": 2},
    {"action": "done", "message": "ok"}, {"action": "wait", "seconds": 2}, {"action": "press", "key": "esc"},
])
def test_agent_guard_allows(action):
    assert agent_guard.validate_action(action)[0]


def test_agent_guard_system_processes():
    assert agent_guard.is_system_process("PowerShell.exe") and agent_guard.is_system_process("cmd.exe")
    assert agent_guard.is_system_process("SearchHost.exe")
    assert agent_guard.is_system_process("StartMenuExperienceHost.exe")
    assert not agent_guard.is_system_process("chrome.exe") and not agent_guard.is_system_process(None)


# ---------- очистка папок ----------
def test_clear_rules():
    home = os.path.expanduser("~")
    assert pathsafe.is_clear_forbidden(os.path.join(home, "Documents"))
    assert pathsafe.is_clear_forbidden(home)
    assert not pathsafe.is_clear_forbidden(os.path.join(home, "Downloads"))        # обычный сценарий
    assert not pathsafe.is_clear_forbidden(os.path.join(home, "Desktop"))
    assert not pathsafe.is_clear_forbidden(os.path.join(home, "Documents", "tmp_stuff"))


# ---------- постоянные таймеры ----------
import json  # noqa: E402
import time  # noqa: E402
import threading  # noqa: E402
from timers import TimerManager  # noqa: E402


def test_timers_persist_and_restore(tmp_path):
    path = str(tmp_path / "timers.json")
    tm = TimerManager(lambda m: None, storage_path=path)
    tm.add(3600, "выключить чайник")
    saved = json.load(open(path, encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["message"] == "выключить чайник" and saved[0]["at"] > time.time() + 3500
    tm.stop()                                             # выход из программы: файл остаётся
    assert json.load(open(path, encoding="utf-8"))

    tm2 = TimerManager(lambda m: None, storage_path=path)
    pending, overdue = tm2.restore()
    assert (pending, overdue) == (1, 0)
    assert tm2.describe()[0][1] == "выключить чайник"
    tm2.cancel_all()                                      # отмена пользователем: файл очищается
    assert json.load(open(path, encoding="utf-8")) == []


def test_timers_overdue_fire_with_note_and_old_dropped(tmp_path):
    path = tmp_path / "timers.json"
    now = time.time()
    path.write_text(json.dumps([{"at": now - 60, "message": "свежее"}, {"at": now - 10 * 3600, "message": "древнее"},
                                {"at": "мусор", "message": 1}, {"nope": 1}]), encoding="utf-8")
    fired, ev = [], threading.Event()
    tm = TimerManager(lambda m: (fired.append(m), ev.set()), storage_path=str(path))
    pending, overdue = tm.restore(startup_delay=0.01)
    assert (pending, overdue) == (0, 1)
    assert ev.wait(3)
    assert fired == ["Пока меня не было, сработало: свежее"]
    tm.stop()


def test_timers_broken_file_does_not_crash(tmp_path):
    path = tmp_path / "timers.json"
    path.write_text("{не json", encoding="utf-8")
    assert TimerManager(lambda m: None, storage_path=str(path)).restore() == (0, 0)


def test_timers_without_storage_still_work():
    tm = TimerManager(lambda m: None)
    tm.add(100, "x")
    assert tm.describe() and tm.cancel_all() == 1


# ---------- RVC: зависший rvc-next.exe ----------
import importlib.util  # noqa: E402
import subprocess  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


def _real_audio(monkeypatch):
    """Настоящий audio.py под своим именем (test_bargein подменяет модуль «audio» пустышкой), без звуковых библиотек."""
    for name in ("sounddevice", "soundfile", "miniaudio", "edge_tts"):
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("audio_under_test", os.path.join(root, "audio.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rvc_cli_timeout_returns_false(monkeypatch, tmp_path):
    audio_mod = _real_audio(monkeypatch)
    monkeypatch.setattr(audio_mod, "TEMP_DIR", str(tmp_path))
    seen = {}

    def hang(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(audio_mod.subprocess, "run", hang)
    assert audio_mod._rvc_convert_cli("in.wav", str(tmp_path / "out.wav")) is False
    assert seen["timeout"] == 120
    assert not list(tmp_path.iterdir())                  # временная папка rvc_* убрана


# ---------- губы по звукам ----------
import numpy as np  # noqa: E402
import visemes  # noqa: E402


def _vowel(f1, f2, sr=24000, f0=210, f3=2900):
    t = np.arange(int(sr * 0.05)) / sr
    sig = np.zeros_like(t)
    for h in range(1, int(5000 / f0)):
        f = f0 * h
        amp = sum(np.exp(-((f - F) / 110) ** 2) * a for F, a in ((f1, 1.0), (f2, 0.55), (f3, 0.25)))
        sig += amp * np.sin(2 * np.pi * f * t + h)
    return sig * 0.3


@pytest.mark.parametrize("f1,f2,want", [(850, 1400, "aa"), (320, 2550, "ih"), (380, 820, "ou"),
                                        (560, 1950, "ee"), (560, 950, "oh")])
def test_viseme_from_formants(f1, f2, want):
    w = visemes.viseme_weights(_vowel(f1, f2), 24000)
    assert max(w, key=w.get) == want
    assert abs(sum(w.values()) - 1) < 1e-6


def test_viseme_silence():
    assert visemes.viseme_weights(np.zeros(1200), 24000) is None
