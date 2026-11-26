"""LLM-роутер: каноническая фраза доходит до действия, белый список интентов, экран в облако — только после «да».
Модель не вызывается: _chat подменён заглушкой, которая «вызывает» нужный инструмент."""
import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from test_actions_security import actions_mod, _ctx  # noqa: E402,F401 — та же фикстура с заглушками Windows-библиотек
from test_telegram_bot import main_mod  # noqa: E402,F401


@pytest.fixture()
def llm_mod(monkeypatch):
    for name in ("openai", "groq"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    return importlib.import_module("llm")


def _stub_router(llm_mod, monkeypatch, tool, args):
    call = types.SimpleNamespace(function=types.SimpleNamespace(name=tool, arguments=json.dumps(args, ensure_ascii=False)))
    resp = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(tool_calls=[call], content=None))])
    seen = []
    monkeypatch.setattr(llm_mod, "_chat", lambda *a, **kw: seen.append(a) or resp)
    return seen


# ---------- llm_route: фраза и белый список ----------
@pytest.mark.parametrize("tool,args,intent,phrase", [
    ("open_target", {"target": "ютуб"}, "[ACTION_OPEN]", "открой ютуб"),
    ("type_text", {"text": "привет мир"}, "[ACTION_TYPE]", "напечатай привет мир"),
    ("window_action", {"action": "закрой", "window": "хром"}, "[ACTION_WINDOW]", "закрой хром"),
    ("create_item", {"name": "отчёт"}, "[ACTION_CREATE]", "создай файл с именем отчёт"),
])
def test_router_returns_canonical_phrase(llm_mod, monkeypatch, tool, args, intent, phrase):
    _stub_router(llm_mod, monkeypatch, tool, args)
    r = llm_mod.llm_route("ну это самое, сделай по-братски")
    assert tuple(r)[0] == intent and r.phrase == phrase and r.tool == tool
    i, _t = r                                             # распаковывается как обычная пара (intent, task)
    assert i == intent


@pytest.mark.parametrize("tool,args", [
    ("window_action", {"action": "закрой", "window": "все окна"}),     # не должно стать CLOSE_ALL
    ("type_text", {"text": "выключи компьютер"}),                      # не должно стать POWER
    ("type_text", {"text": "удали файл отчёт"}),                       # не должно стать DELETE_FILE
    ("open_target", {"target": "хром в 23:00"}),                       # только OPEN, не расписание
    ("create_item", {"name": "снег", "is_folder": True}),               # «создай … снег» — магия Live2D, не CREATE
])
def test_router_phrase_cannot_become_other_intent(llm_mod, monkeypatch, tool, args):
    _stub_router(llm_mod, monkeypatch, tool, args)
    r = llm_mod.llm_route("что-то непонятное")
    if r[0] != llm_mod.ROUTER_REJECTED:                   # «хром в 23:00» остаётся OPEN — это и есть заявленный интент
        assert r[0] in llm_mod.ROUTER_INTENTS[tool]
    assert r[0] not in ("[ACTION_CLOSE_ALL]", "[ACTION_POWER]", "[ACTION_DELETE_FILE]", "[ACTION_PLAN]", "[ACTION_PET]")


def test_router_rejects_closing_all_windows(llm_mod, monkeypatch):
    _stub_router(llm_mod, monkeypatch, "window_action", {"action": "закрой", "window": "все окна"})
    assert llm_mod.llm_route("позакрывай-ка всё")[0] == llm_mod.ROUTER_REJECTED


@pytest.mark.parametrize("tool", ["delete_file", "shutdown_pc", "kill_process", "send_file", ""])
def test_router_rejects_unknown_tool(llm_mod, monkeypatch, tool):
    _stub_router(llm_mod, monkeypatch, tool, {"path": "C:/важное"})
    assert llm_mod.llm_route("убери этот файлик")[0] == llm_mod.ROUTER_REJECTED


def test_router_whitelist_has_no_dangerous_intents(llm_mod):
    dangerous = {"[ACTION_POWER]", "[ACTION_DELETE_FILE]", "[ACTION_CLEAR_FOLDER]", "[ACTION_SYS]", "[ACTION_PHONE]",
                 "[ACTION_CLOSE_ALL]", "[ACTION_PLAN]", "[ACTION_TG_REPLY]", "[ACTION_KEYS]", "[ACTION_MOUSE]"}
    assert not dangerous & set().union(*llm_mod.ROUTER_INTENTS.values())
    assert set(llm_mod.ROUTER_INTENTS) == {t["function"]["name"] for t in llm_mod.ROUTER_TOOLS}


def test_router_screen_keeps_user_question(llm_mod, monkeypatch):
    _stub_router(llm_mod, monkeypatch, "look_at_screen", {})
    r = llm_mod.llm_route("глянь, почему у меня тут всё красное")
    assert r[0] == "[ACTION_SCREEN]" and r[1] == "глянь, почему у меня тут всё красное"


def test_router_volume_filter_still_works(llm_mod, monkeypatch):
    _stub_router(llm_mod, monkeypatch, "media", {"action": "громкость", "level": 100})
    assert llm_mod.llm_route("сдвинь на 100") is None


# ---------- execute_routed: действие получает фразу роутера ----------
def test_routed_open_uses_router_phrase(actions_mod, monkeypatch):
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda q: (None, False))
    launch = MagicMock(return_value=(True, "start"))
    monkeypatch.setattr(actions_mod, "_launch_by_name", launch)
    ctx, asked = _ctx(actions_mod, True)
    # исходную фразу регулярка OPEN не понимает — поэтому её и разбирал роутер
    assert "Не поняла" in actions_mod.execute_pc_action("[ACTION_OPEN]", "мне бы глянуть кинопоиск", "", ctx)
    res = actions_mod.execute_routed("[ACTION_OPEN]", "открой кинопоиск", "", ctx)
    assert res == "Открываю кинопоиск." and launch.call_args[0][0] == "кинопоиск" and not asked


def test_routed_type_uses_router_phrase(actions_mod, monkeypatch):
    typed = MagicMock(return_value=(True, "Блокнот"))
    monkeypatch.setattr(actions_mod, "type_at_cursor", typed)
    ctx, _ = _ctx(actions_mod, True)
    res = actions_mod.execute_routed("[ACTION_TYPE]", "напечатай привет мир", "", ctx)
    assert typed.call_args[0][0] == "привет мир" and res.startswith("Напечатала")


def test_routed_window_uses_router_phrase(actions_mod, monkeypatch):
    win = MagicMock(title="Google Chrome")
    find = MagicMock(return_value=win)
    monkeypatch.setattr(actions_mod, "_find_window_by_title", find)
    ctx, _ = _ctx(actions_mod, True)
    res = actions_mod.execute_routed("[ACTION_WINDOW]", "закрой хром", "", ctx)
    assert find.call_args[0][0] == "хром" and win.close.called and res == "Закрыла 'Google Chrome'."


def test_routed_create_makes_named_file(actions_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(actions_mod, "DESKTOP_PATH", str(tmp_path))
    monkeypatch.setattr(actions_mod, "_open_path", lambda p: True)
    ctx, _ = _ctx(actions_mod, True)
    res = actions_mod.execute_routed("[ACTION_CREATE]", "создай файл с именем отчёт", "", ctx)
    assert (tmp_path / "отчёт.txt").exists() and not (tmp_path / "Новый файл.txt").exists()
    assert res == "Создала файл отчёт.txt."


def test_routed_screen_asks_before_cloud_and_keeps_question(actions_mod, monkeypatch):
    look = MagicMock(return_value="Вижу ошибку импорта.")
    monkeypatch.setattr(actions_mod, "read_screen_and_analyze", look)
    q = "глянь, почему у меня тут всё красное"
    ctx, asked = _ctx(actions_mod, False)                                         # «нет» — снимок не уходит
    assert actions_mod.execute_routed("[ACTION_SCREEN]", "что на экране", q, ctx) == "Хорошо, не смотрю."
    assert asked and not look.called
    ctx2, asked2 = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})    # force: спросит даже так
    actions_mod.execute_routed("[ACTION_SCREEN]", "что на экране", q, ctx2)
    assert asked2 and not look.called
    ctx3 = actions_mod.ActionContext({}, confirm=None)                            # некого спросить — отказ
    actions_mod.execute_routed("[ACTION_SCREEN]", "что на экране", q, ctx3)
    assert not look.called
    ctx4, asked4 = _ctx(actions_mod, True)                                        # «да» — вопрос пользователя целиком
    assert actions_mod.execute_routed("[ACTION_SCREEN]", "что на экране", q, ctx4) == "Вижу ошибку импорта."
    assert asked4 and look.call_args[0][0] == q
    ctx5, asked5 = _ctx(actions_mod, False, cfg={"send_screenshots_to_cloud": True})   # явное разрешение в конфиге
    actions_mod.execute_routed("[ACTION_SCREEN]", "что на экране", q, ctx5)
    assert not asked5 and look.call_count == 2


def test_routed_telegram_send_still_asks(actions_mod, monkeypatch):
    monkeypatch.setattr(actions_mod, "_init_telegram_if_needed", lambda cfg: (True, "ok"))
    monkeypatch.setattr(actions_mod, "_tg_find", lambda c: ("dlg", "Вася"))
    monkeypatch.setattr(actions_mod, "rewrite_message_for_contact", lambda c, t: t)
    svc = MagicMock()
    monkeypatch.setattr(actions_mod, "TG_SERVICE", svc)
    ctx, asked = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})
    res = actions_mod.execute_routed("[ACTION_TG_SEND]", "напиши в телеграм вася привет", "вася|привет", ctx)
    assert res == "Не отправила." and asked and not svc.send_to_dialog.called


# ---------- main.handle: фраза роутера и отказ ----------
class _Routed(tuple):
    def __new__(cls, intent, task, phrase):
        self = super().__new__(cls, (intent, task))
        self.phrase = phrase
        return self


def test_handle_passes_router_phrase(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod, "_get_intent", lambda t: _Routed("[ACTION_OPEN]", "", "открой кинопоиск"))
    routed, plain = MagicMock(return_value="Открываю кинопоиск."), MagicMock(return_value="Не поняла.")
    monkeypatch.setattr(main_mod, "execute_routed", routed)
    monkeypatch.setattr(main_mod, "execute_pc_action", plain)
    main_mod.handle("мне бы глянуть кинопоиск")
    assert routed.call_args[0][:3] == ("[ACTION_OPEN]", "открой кинопоиск", "") and not plain.called


def test_handle_plain_intent_uses_user_text(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod, "_get_intent", lambda t: ("[ACTION_OPEN]", ""))
    routed, plain = MagicMock(), MagicMock(return_value="Открываю блокнот.")
    monkeypatch.setattr(main_mod, "execute_routed", routed)
    monkeypatch.setattr(main_mod, "execute_pc_action", plain)
    main_mod.handle("открой блокнот")
    assert plain.call_args[0][:2] == ("[ACTION_OPEN]", "открой блокнот") and not routed.called


def test_handle_rejected_router_says_not_understood(main_mod, monkeypatch):
    monkeypatch.setattr(main_mod.llm, "ROUTER_REJECTED", "[ROUTER_REJECTED]")
    monkeypatch.setattr(main_mod, "_get_intent", lambda t: _Routed("[ROUTER_REJECTED]", "", "закрой все окна"))
    said, routed, plain = [], MagicMock(), MagicMock()
    monkeypatch.setattr(main_mod, "say", lambda text, *a, **kw: said.append(text))
    monkeypatch.setattr(main_mod, "execute_routed", routed)
    monkeypatch.setattr(main_mod, "execute_pc_action", plain)
    main_mod.handle("позакрывай-ка всё")
    assert said == ["Не поняла."] and not routed.called and not plain.called
