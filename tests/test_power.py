import datetime

import pytest

import power
from intents import get_intent


@pytest.mark.parametrize("phrase", ["выключи системные звуки", "выключи композицию", "выключи компьютерную игру"])
def test_not_pc_shutdown(phrase):
    assert power.parse(phrase) is None
    assert get_intent(phrase)[0] != "[ACTION_POWER]"


@pytest.mark.parametrize("phrase", ["выключи компьютер", "выключи комп", "выключи ноутбук"])
def test_pc_shutdown(phrase):
    assert power.parse(phrase) == ("shutdown", 0)
    assert get_intent(phrase) == ("[ACTION_POWER]", "shutdown|0")


def test_intents():
    assert get_intent("выключи компьютер через 30 минут") == ("[ACTION_POWER]", "shutdown|1800")
    assert get_intent("перезагрузи комп") == ("[ACTION_POWER]", "restart|0")
    assert get_intent("отмени выключение") == ("[ACTION_POWER]", "cancel|0")
    assert get_intent("выключи звук")[0] == "[ACTION_VOLUME]"
    assert get_intent("перезагрузи страницу")[0] == "[ACTION_HOTKEY]"
    assert get_intent("выключись")[0] == "[ACTION_STOP]"


def test_delay():
    assert power.parse_delay("через час") == 3600
    assert power.parse_delay("через 2 часа") == 7200
    assert power.parse_delay("через пять минут") == 300
    now = datetime.datetime(2026, 1, 1, 22, 0)
    assert power.parse_delay("в 23:30", now) == 5400


def test_delay_compound_and_clock():
    now = datetime.datetime(2026, 1, 1, 20, 0)
    assert power.parse_delay("выключи компьютер через час десять минут", now) == 4200
    assert power.parse_delay("выключи компьютер через 2 часа 30 минут", now) == 9000
    assert power.parse_delay("выключи компьютер через полчаса", now) == 1800
    assert power.parse_delay("выключи компьютер в 11 вечера", now) == 3 * 3600
    assert power.parse_delay("выключи компьютер в 23", now) == 3 * 3600
    assert power.parse_delay("выключи компьютер в 7 утра", now) == 11 * 3600
    assert power.parse_delay("выключи компьютер", now) == 0


@pytest.mark.parametrize("phrase", ["выключи компьютер через немного", "выключи компьютер к 23:00",
                                    "выключи компьютер завтра", "выключи компьютер вечером"])
def test_delay_named_but_unclear(phrase):
    assert power.parse_delay(phrase) == power.UNKNOWN


def test_unclear_time_asks_instead_of_shutting_down_now(monkeypatch):
    calls, asked = [], []
    monkeypatch.setattr(power.subprocess, "run", lambda *a, **k: calls.append(a[0]))
    out = power.run("shutdown", power.UNKNOWN, lambda q: asked.append(q) or True)
    assert not calls and not asked
    assert out.startswith("Не поняла, когда выключить компьютер.")
    assert get_intent("выключи компьютер завтра") == ("[ACTION_POWER]", f"shutdown|{power.UNKNOWN}")


def test_run_needs_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(power.subprocess, "run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0})())
    assert power.run("shutdown", 1800, lambda q: False) == "Хорошо, не трогаю." and not calls
    out = power.run("shutdown", 1800, lambda q: True)
    assert ["shutdown", "/s", "/t", "1800"] in calls and "через 30 мин" in out
    calls.clear()
    power.run("restart", 0, lambda q: True)
    assert ["shutdown", "/r", "/t", str(power.MIN_DELAY)] in calls
