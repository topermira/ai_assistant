"""Расписания и сценарии, честное «сядь на окно», «открой новое окно яндекса»."""
import json
import sys
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import scenarios
import winalias
from intents import get_intent
from test_actions_security import actions_mod, _ctx          # noqa: F401

NOW = datetime(2026, 11, 26, 15, 0)                            # четверг, 15:00


def spec(text, now=NOW):
    r = scenarios.parse(text, now)
    assert r and r[0] == "plan_add", (text, r)
    return r[1]


# ---------------------------------------------------------------- разбор
def test_plan_recurring_and_once():
    assert spec("каждый день в 8:30 скажи погоду") == {"days": list(range(7)), "date": None, "time": "08:30", "kind": "cmd", "text": "скажи погоду"}
    s = spec("по будням в 9 напомни про планёрку")
    assert s["days"] == [0, 1, 2, 3, 4] and s["time"] == "09:00" and s["kind"] == "remind" and s["text"] == "про планёрку"
    assert spec("по понедельникам и средам в восемь тридцать скажи погоду")["days"] == [0, 2]
    assert spec("каждый понедельник в 10:00 запусти сценарий работа")["days"] == [0]
    assert spec("по выходным в 11 утра включи музыку")["days"] == [5, 6]
    assert spec("каждый вечер в 8 включи музыку")["time"] == "20:00"
    assert spec("каждый день в двадцать три ноль пять выключи свет")["time"] == "23:05"
    one = spec("в 23:00 сверни все окна")
    assert one["days"] is None and one["date"] == "2026-11-26" and one["text"] == "сверни все окна"
    assert spec("в 8:00 скажи привет")["date"] == "2026-11-27"                    # 8 утра уже прошло — завтра
    assert spec("завтра в 7 утра напомни выпить воду")["date"] == "2026-11-27"
    assert spec("напомни в 18:00 позвонить маме") ["kind"] == "remind"
    assert spec("в пятницу в 9:30 открой почту")["date"] == "2026-11-27"
    assert spec("в четверг в 9:30 открой почту")["date"] == "2026-12-03"           # сегодня четверг, 9:30 прошло → через неделю
    assert spec("в 8 вечера скажи привет")["time"] == "20:00" and spec("в 12 ночи скажи пока")["time"] == "00:00"
    assert spec("в полдень скажи обед")["time"] == "12:00"


def test_plan_not_triggered_by_ordinary_commands():
    for t in ["в 3 раза громче", "включи музыку в 5 раз громче", "открой яндекс", "поставь таймер на 5 минут",
              "напомни через 10 минут чайник", "громкость 30", "разбуди меня в 7 утра", "в 8 скажи привет", "какое сегодня число"]:
        r = scenarios.parse(t, NOW)
        assert r is None or r[0] != "plan_add", (t, r)
    assert scenarios.parse("каждый день скажи погоду", NOW) == ("plan_ask",)      # время не назвали — переспросим


def test_plan_needs_a_command():
    for t in ["в 8 утра я проснулся и пошёл на работу", "каждый день в 8 утра я просыпаюсь",
              "завтра в 9 я иду к врачу", "в 7 вечера мы ужинаем"]:
        assert scenarios.parse(t, NOW) is None, t
    assert get_intent("в 8 утра я проснулся и пошёл на работу")[0] != "[ACTION_PLAN]"
    assert spec("в 23:00 громкость 10")["text"] == "громкость 10"                 # команда без глагола — тоже команда
    assert spec("каждый день в 8 утра пожалуйста скажи погоду")["text"] == "пожалуйста скажи погоду"
    assert spec("запланируй завтра в 9 звонок маме")["text"] == "звонок маме"     # «запланируй» — явная просьба
    assert spec("каждый день в 8 напомни выпить таблетку")["kind"] == "remind"


def test_scenario_run_needs_verb():
    assert scenarios.parse("сценарий фильма классный", NOW) is None
    assert get_intent("сценарий фильма классный")[0] != "[ACTION_PLAN]"
    assert scenarios.parse("включи сценарий фильма классный") == ("scen_run", "фильма классный")
    assert scenarios.parse("Рокси, выполни сценарий утро понедельника") == ("scen_run", "утро понедельника")


def test_scenario_phrases():
    assert scenarios.parse("создай сценарий работа: открой pycharm, громкость 20, открой яндекс") == \
        ("scen_save", "работа", ["открой pycharm", "громкость 20", "открой яндекс"])
    assert scenarios.parse("запомни сценарий вечер это тише потом заблокируй") == ("scen_save", "вечер", ["тише", "заблокируй"])
    assert scenarios.parse("создай сценарий вечер") == ("scen_ask",)
    assert scenarios.parse("запусти сценарий работа") == ("scen_run", "работа")
    assert scenarios.parse("сценарий работы") == ("scen_run", "работы")
    assert scenarios.parse("какие сценарии") == ("scen_list",)
    assert scenarios.parse("что в сценарии работа") == ("scen_show", "работа")
    assert scenarios.parse("удали сценарий работа") == ("scen_del", "работа")
    assert scenarios.parse("какое расписание") == ("plan_list",)
    assert scenarios.parse("удали расписание 2") == ("plan_del", 2)
    assert scenarios.parse("удали все расписания") == ("plan_del", "all")


def test_intents_route_plans_before_power():
    assert get_intent("каждый день в 8:30 скажи погоду") == ("[ACTION_PLAN]", "plan_add")
    assert get_intent("в 23:00 выключи компьютер") == ("[ACTION_PLAN]", "plan_add")      # не выключать СЕЙЧАС
    assert get_intent("выключи компьютер")[0] == "[ACTION_POWER]"
    assert get_intent("запусти сценарий работа") == ("[ACTION_PLAN]", "scen_run")
    assert get_intent("какие сценарии") == ("[ACTION_PLAN]", "scen_list")


# ---------------------------------------------------------------- хранение и срабатывание
@pytest.fixture()
def planner(tmp_path, monkeypatch):
    p = scenarios.Planner(str(tmp_path / "sc.json"), str(tmp_path / "pl.json"))
    monkeypatch.setattr(scenarios, "_PLANNER", p)
    return p


def test_planner_roundtrip_and_dedupe(planner, tmp_path):
    s = spec("каждый день в 8:30 скажи погоду")
    assert planner.add_plan(s) == planner.add_plan(s)                  # одинаковое не дублируем
    planner.save_scenario("работа", ["открой pycharm", "громкость 20"])
    again = scenarios.Planner(str(tmp_path / "sc.json"), str(tmp_path / "pl.json"))
    assert again.list_plans() == ["каждый день в 08:30 — скажи погоду"] and again.scenarios == {"работа": ["открой pycharm", "громкость 20"]}
    assert not (tmp_path / "pl.json.tmp").exists()


def test_planner_due_rules(planner):
    daily = spec("каждый день в 8:30 скажи погоду")
    mon = spec("каждый понедельник в 8:30 скажи погоду")
    once = spec("в 23:00 сверни все окна")
    for s in (daily, mon, once):
        planner.add_plan(s)
    thu = datetime(2026, 11, 26, 8, 30, 20)
    got = planner.due(thu)
    assert [g["text"] for g in got] == ["скажи погоду"] and got[0]["days"] == daily["days"]   # понедельничное в четверг молчит
    assert planner.due(datetime(2026, 11, 26, 8, 31)) == []                                   # то же задание дважды за день — нет
    assert planner.due(datetime(2026, 11, 26, 8, 20)) == []
    ran = planner.due(datetime(2026, 11, 26, 23, 0, 30))
    assert [r["text"] for r in ran] == ["сверни все окна"]
    assert "сверни все окна" not in " ".join(planner.list_plans())                            # разовое удалилось
    assert planner.due(datetime(2026, 11, 27, 8, 30, 5))[0]["text"] == "скажи погоду"         # на следующий день — снова


def test_planner_missed_runs(planner):
    planner.add_plan(spec("каждый день в 8:30 скажи погоду"))
    planner.add_plan(spec("в 23:00 сверни все окна"))
    assert planner.due(datetime(2026, 11, 26, 9, 0)) == []                 # Рокси включили на 30 минут позже — пропуск
    assert planner.due(datetime(2026, 11, 26, 23, 5))[0]["text"] == "сверни все окна"    # разовое: опоздание 5 минут допустимо
    planner.add_plan(spec("в 23:30 выключи свет", datetime(2026, 11, 26, 23, 6)))
    assert planner.due(datetime(2026, 11, 27, 0, 5)) == []                # дата прошла — выбросили
    assert all("выключи свет" not in d for d in planner.list_plans())


def test_late_reminders_fire_with_note(planner):
    planner.add_plan(spec("в 23:00 напомни выпить таблетку"))         # разовое, 26.11
    planner.add_plan(spec("каждый день в 9:00 напомни про планёрку"))
    planner.add_plan(spec("в 23:00 сверни все окна"))                 # команда, 26.11
    got = planner.due(datetime(2026, 11, 27, 1, 30))                  # ПК спал: опоздание 2,5 ч, через полночь
    assert [(g["text"], g.get("late_min")) for g in got] == [("выпить таблетку", 150)]
    assert all("таблетку" not in d and "сверни" not in d for d in planner.list_plans())   # команда — пропуск, как раньше
    assert [(g["text"], g["late_min"]) for g in planner.due(datetime(2026, 11, 27, 14, 0))] == [("про планёрку", 300)]
    assert planner.due(datetime(2026, 11, 27, 14, 1)) == []                                 # второй раз за день — нет
    assert planner.due(datetime(2026, 11, 28, 15, 30)) == []                                # больше 6 ч — пропуск
    on_time = planner.due(datetime(2026, 11, 29, 9, 5))
    assert on_time[0]["text"] == "про планёрку" and "late_min" not in on_time[0]            # 5 минут — без пометки
    assert scenarios.reminder_phrase("выпить таблетку", 150) == "Опоздала на 2 часа 30 минут. Напоминаю: выпить таблетку."
    assert scenarios.reminder_phrase("про планёрку", 21) == "Опоздала на 21 минуту. Напоминаю про планёрку."
    assert scenarios.reminder_phrase("про планёрку") == "Напоминаю про планёрку."


def test_broken_schedule_record_is_skipped(tmp_path):
    pl_path = tmp_path / "pl.json"
    good = {"days": [3], "date": None, "time": "08:30", "kind": "cmd", "text": "скажи погоду", "last": None}
    records = [good, {"days": "mon", "date": None, "time": "08:00", "kind": "cmd", "text": "x"},
               {"days": [3], "date": None, "time": "25:99", "kind": "cmd", "text": "y"},
               {"days": None, "date": "завтра", "time": "09:00", "kind": "remind", "text": "z"}, "мусор", 5]
    pl_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    p = scenarios.Planner(str(tmp_path / "sc.json"), str(pl_path))
    assert p.list_plans() == ["по четвергам в 08:30 — скажи погоду"]
    assert json.loads((tmp_path / "pl.json.broken").read_text(encoding="utf-8")) == records   # копия до перезаписи
    assert [d["text"] for d in p.due(datetime(2026, 11, 26, 8, 30, 10))] == ["скажи погоду"]


def test_broken_schedule_file_is_kept_as_copy(tmp_path):
    pl_path = tmp_path / "pl.json"
    pl_path.write_text("[{битый", encoding="utf-8")
    p = scenarios.Planner(str(tmp_path / "sc.json"), str(pl_path))
    assert p.plans == [] and (tmp_path / "pl.json.broken").read_text(encoding="utf-8") == "[{битый"
    p.add_plan(spec("каждый день в 9:00 скажи привет"))                 # новая запись копию не трогает
    assert (tmp_path / "pl.json.broken").read_text(encoding="utf-8") == "[{битый"
    assert len(json.loads(pl_path.read_text(encoding="utf-8"))) == 1


def test_due_survives_bad_plan_in_memory(planner):
    planner.add_plan(spec("каждый день в 8:30 скажи погоду"))
    planner.plans.append({"id": 99, "days": "mon", "date": None, "time": "08:30", "kind": "cmd", "text": "x", "last": None})
    assert [d["text"] for d in planner.due(datetime(2026, 11, 26, 8, 30, 10))] == ["скажи погоду"]


def test_planner_delete_and_limits(planner):
    for h in (9, 10, 11):
        planner.add_plan(spec(f"каждый день в {h}:00 скажи привет"))
    assert planner.delete_plan(2) == 1 and len(planner.list_plans()) == 2
    assert planner.delete_plan(9) == 0
    assert planner.delete_plan("all") == 2 and planner.list_plans() == []


def test_scenario_lookup_by_stem(planner):
    planner.save_scenario("работа", ["громкость 20"])
    assert planner.find_scenario("работы")[0] == "работа"
    assert planner.find_scenario("Работа")[0] == "работа"
    assert planner.find_scenario("кино")[0] is None
    assert planner.delete_scenario("работы") == "работа" and planner.scenarios == {}


# ---------------------------------------------------------------- действия
def test_plan_actions(actions_mod, planner):
    ctx, asked = _ctx(actions_mod, True)
    run = lambda t: actions_mod.execute_pc_action("[ACTION_PLAN]", t, "", ctx)
    assert "Поставила: каждый день в 08:30 — скажи погоду" in run("каждый день в 8:30 скажи погоду")
    assert "Поставила: по будням в 07:00" in run("по будням в 7:00 включи музыку")
    assert "каждый день в 08:30" in run("какое расписание")
    assert "Такого сценария пока нет" in run("каждый понедельник в 10:00 запусти сценарий работа")
    assert "Не поняла расписание" in run("каждый день скажи погоду")
    assert "«Стоп» по расписанию не ставлю" in run("каждый день в 8:00 стоп")
    assert "Удалила расписание" in run("удали расписание 1")
    assert "Удалила расписаний" in run("удали все расписания") and asked
    assert "Расписаний нет" in run("какое расписание")


def test_scenario_actions_and_run(actions_mod, planner, monkeypatch):
    monkeypatch.setattr(actions_mod.time, "sleep", lambda s: None)
    ctx, asked = _ctx(actions_mod, True)
    run = lambda t: actions_mod.execute_pc_action("[ACTION_PLAN]", t, "", ctx)
    out = run("создай сценарий работа: открой pycharm, громкость 20, расскажи что-нибудь интересное")
    assert "Запомнила сценарий «работа»: шагов 3" in out and "расскажи что-нибудь интересное" in out
    assert "работа" in run("какие сценарии") and "1) открой pycharm" in run("что в сценарии работа")
    calls = []
    with monkeypatch.context() as m:
        m.setattr(actions_mod, "execute_pc_action",
                  lambda intent, text, task, c=None: calls.append((intent, text)) or f"ok {text}")
        res = actions_mod.run_scenario(ctx, "работа", planner.scenarios["работа"])
    assert [c[0] for c in calls] == ["[ACTION_OPEN]", "[ACTION_VOLUME]"]       # третий шаг — болтовня, пропущен
    assert "выполнено 2 из 3" in res and "не похоже на команду" in res
    assert "Нет сценария «кино»" in actions_mod.execute_pc_action("[ACTION_PLAN]", "запусти сценарий кино", "", ctx)
    assert "Удалила сценарий" in actions_mod.execute_pc_action("[ACTION_PLAN]", "удали сценарий работа", "", ctx)


def test_scenario_never_runs_stop_or_nested_plans(actions_mod, planner, monkeypatch):
    monkeypatch.setattr(actions_mod.time, "sleep", lambda s: None)
    ctx, _ = _ctx(actions_mod, True)
    calls = []
    monkeypatch.setattr(actions_mod, "execute_pc_action", lambda i, t, k, c=None: calls.append(i) or "ok")
    res = actions_mod.run_scenario(ctx, "x", ["стоп", "запусти сценарий x", "каждый день в 8:00 скажи погоду"])
    assert calls == [] and "выполнено 0 из 3" in res


# ---------------------------------------------------------------- «сядь на окно» — честный ответ
def test_perch_reply_is_honest(actions_mod, monkeypatch):
    import live2d_client
    L = live2d_client.LIVE2D
    L._ws = object()                                           # «подключено»
    ctx, _ = _ctx(actions_mod, True)
    go = lambda: actions_mod.execute_pc_action("[ACTION_PET]", "сядь на окно", "perch", ctx)
    monkeypatch.setattr(L, "behavior_result", lambda name, timeout=2.5: {"ok": False, "reason": "все окна развёрнуты"})
    assert go() == "Не получилось сесть на окно: все окна развёрнуты."
    monkeypatch.setattr(L, "behavior_result", lambda name, timeout=2.5: {"ok": True, "reason": ""})
    assert go() == "Сажусь на окно."
    monkeypatch.setattr(L, "behavior_result", lambda name, timeout=2.5: None)      # старая версия окна не отвечает
    assert go() == "Сажусь на окно."
    L._ws = None
    assert go() == "Окно Live2D не подключено."


def test_client_waits_for_window_answer():
    import json as _json
    import live2d_client
    cl = live2d_client.Live2DClient()
    sent = []

    class WS:
        def __init__(self):
            self.msgs = [_json.dumps({"type": "behavior_result", "name": "perch", "ok": False, "reason": "нет окон"})]

        def send(self, payload):
            sent.append(_json.loads(payload))

        def recv(self):
            if self.msgs:
                return self.msgs.pop(0)
            raise OSError("closed")
    ws = WS()
    cl._ws, cl._running = ws, True
    import threading
    t = threading.Timer(0.05, cl._reader, args=(ws,))
    t.start()
    res = cl.behavior_result("perch", timeout=2)
    t.join()
    assert sent == [{"type": "behavior", "name": "perch"}] and res["ok"] is False and res["reason"] == "нет окон"
    cl2 = live2d_client.Live2DClient()
    cl2._ws = WS()
    cl2._ws.msgs = []
    assert cl2.behavior_result("perch", timeout=0.1) is None


# ---------------------------------------------------------------- новое окно браузера
def test_new_window_phrases():
    assert winalias.split_new_window("новое окно яндекса") == (True, "яндекса")
    assert winalias.split_new_window("яндекс в новом окне") == (True, "яндекс")
    assert winalias.split_new_window("яндекс") == (False, "яндекс")
    assert winalias.new_window_command("яндекса") == 'start "" browser --new-window'
    assert winalias.new_window_command("хроме") == 'start "" chrome --new-window'
    assert winalias.new_window_command("блокнота") is None
    assert get_intent("открой новое окно яндекса")[0] == "[ACTION_OPEN]"


def test_open_new_browser_window(actions_mod, monkeypatch):
    popen = MagicMock()
    monkeypatch.setattr(actions_mod.subprocess, "Popen", popen)
    ctx, _ = _ctx(actions_mod, True)
    res = actions_mod.execute_pc_action("[ACTION_OPEN]", "открой новое окно яндекса", "", ctx)
    assert res == "Открываю новое окно яндекса." and popen.call_args[0][0] == 'start "" browser --new-window'
    assert "Новое окно чего" in actions_mod.execute_pc_action("[ACTION_OPEN]", "открой новое окно", "", ctx)
    assert "Яндекс, Хром" in actions_mod.execute_pc_action("[ACTION_OPEN]", "открой новое окно блокнота", "", ctx)
