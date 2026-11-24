"""Баги из лога Telegram-управления: имя файла, «сдвинь на 100», «курсор на 20 вверх», окна на другом языке."""
import sys
import types

import pcctl
import winalias
from intents import get_intent, create_name


def test_create_file_name_without_word_file():
    assert create_name("Создай ArsPidoras.txt") == "ArsPidoras.txt"
    assert create_name("создай мне новый файл заметки.txt") == "заметки.txt"
    assert create_name("создай файл Арс.txt") == "Арс.txt"
    assert create_name("создай папку проекты на рабочем столе") == "проекты"
    assert create_name("создай файл") == ""
    assert get_intent("Создай ArsPidoras.txt")[0] == "[ACTION_CREATE]"
    assert get_intent("создай новый файл") == ("[ACTION_HOTKEY]", "ctrl+n|Новый файл")   # без имени — Ctrl+N, как раньше


def test_close_file_window_is_not_ctrl_n():
    assert get_intent("Закрой Новый файл.txt")[0] == "[ACTION_WINDOW]"
    assert get_intent("закрой новый документ")[0] == "[ACTION_WINDOW]"
    assert get_intent("Закрой блокнот")[0] == "[ACTION_WINDOW]"


def test_move_without_direction_asks(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyautogui", types.SimpleNamespace(FAILSAFE=True))
    assert pcctl.parse_mouse("сдвинь на 100") == ("ask",)
    assert pcctl.parse_mouse("передвинь на сто пикселей") == ("ask",)
    assert get_intent("Сдвинь на 100") == ("[ACTION_MOUSE]", "ask")
    assert "вверх, вниз" in pcctl.mouse(("ask",))
    assert pcctl.parse_mouse("сдвинь на 100 вниз") == ("move", 0, 100)
    assert pcctl.parse_mouse("подними громкость на 100") is None        # про громкость — не мышь
    assert pcctl.parse_mouse("сдвинь окно влево") is None


def test_cursor_direction_without_verb():
    assert pcctl.parse_mouse("курсор на 20 вверх") == ("move", 0, -20)
    assert pcctl.parse_mouse("мышку на 50 пикселей влево") == ("move", -50, 0)
    assert pcctl.parse_mouse("курсор вправо") == ("move", 100, 0)
    assert get_intent("Курсор на 20 вверх") == ("[ACTION_MOUSE]", "move|0|-20")


def test_window_aliases_other_language():
    assert "kladblok" in winalias.needles("блокнот")
    assert winalias.process_for("Блокнот") == "notepad.exe"
    assert winalias.process_for("яндекс") == "browser.exe"
    assert winalias.process_for("Yandex") == "browser.exe"
    assert winalias.process_for("Новый файл.txt") is None
    assert winalias.needles("Новый файл.txt") == ["новый файл.txt"]


def test_pick_window():
    cands = [("Naamloos - Kladblok", "notepad.exe", 800, 600, False),
             ("Telegram", "telegram.exe", 900, 700, False)]
    assert winalias.pick_window(cands, "notepad.exe") == 0
    assert winalias.pick_window(cands, "chrome.exe") is None
    mini = [("Новый файл.txt", "notepad.exe", 160, 28, True)]       # свёрнутое окно маленькое, но подходит
    assert winalias.pick_window(mini, "notepad.exe") == 0
    tiny = [("", "notepad.exe", 800, 600, False), ("x", "notepad.exe", 10, 10, False)]
    assert winalias.pick_window(tiny, "notepad.exe") is None        # без заголовка / крошечное — не берём


def test_browsers_not_multi_window():
    assert "browser.exe" in winalias.BROWSER_EXES
    assert "explorer.exe" in winalias.MULTI_WINDOW_EXES
    assert not (winalias.BROWSER_EXES & winalias.MULTI_WINDOW_EXES)


def test_router_does_not_make_volume_from_move(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setitem(sys.modules, "openai", MagicMock())
    sys.modules.pop("llm", None)
    import llm
    assert llm.router_phrase_ok("media", "громкость 100", "Сдвинь на 100") is False
    assert llm.router_phrase_ok("media", "громкость 100", "сделай громкость на сто") is True
    assert llm.router_phrase_ok("media", "пауза", "Сдвинь на 100") is True
    assert llm.router_phrase_ok("timer", "таймер", "что угодно") is True
    sys.modules.pop("llm", None)
