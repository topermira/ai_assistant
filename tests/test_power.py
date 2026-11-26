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


def test_run_needs_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(power.subprocess, "run", lambda *a, **k: calls.append(a[0]) or type("R", (), {"returncode": 0})())
    assert power.run("shutdown", 1800, lambda q: False) == "Хорошо, не трогаю." and not calls
    out = power.run("shutdown", 1800, lambda q: True)
    assert ["shutdown", "/s", "/t", "1800"] in calls and "через 30 мин" in out
    calls.clear()
    power.run("restart", 0, lambda q: True)
    assert ["shutdown", "/r", "/t", str(power.MIN_DELAY)] in calls
