"""Таймеры по абсолютному времени: сон ПК (скачок time.time вперёд), «Опоздала на …», отмена, перезапуск, остановка.
time.time подменяется только внутри timers.py — остальной процесс видит настоящие часы."""
import os
import sys
import json
import time
import types
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import timers as timers_mod  # noqa: E402
from timers import TimerManager, late_note  # noqa: E402


@pytest.fixture()
def clock(monkeypatch):
    now = [1_700_000_000.0]
    monkeypatch.setattr(timers_mod, "time", types.SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(timers_mod, "POLL_SEC", 0.01)
    return now


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _fired_list():
    fired, ev = [], threading.Event()
    return fired, ev, lambda m: (fired.append(m), ev.set())


def test_sleep_counts_toward_timer(clock):
    """«Напомни через 10 минут» + ПК спал 5 минут — сработает на 10-й минуте по часам, а не на 15-й."""
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb)
    tm.add(600, "Напоминаю: чайник.")
    clock[0] += 300                                    # 5 минут бодрствования
    assert not ev.wait(0.1)
    clock[0] += 300                                    # ещё 5 минут — во сне; при пробуждении часы уже на 10-й минуте
    assert ev.wait(3) and fired == ["Напоминаю: чайник."]    # опоздание < минуты — без пометки
    tm.stop()


def test_fired_after_wake_says_how_late(clock):
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb)
    tm.add(600, "Напоминаю: чайник.")
    clock[0] += 600 + 5 * 60                           # проспал срабатывание на 5 минут
    assert ev.wait(3)
    assert fired == ["Опоздала на 5 минут. Напоминаю: чайник."]
    tm.stop()


def test_overslept_more_than_limit_is_dropped(clock, tmp_path):
    path = str(tmp_path / "timers.json")
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb, storage_path=path)
    tm.add(60, "старое")
    clock[0] += 60 + timers_mod.MAX_OVERDUE_SEC + 60   # больше 6 часов — как в restore(): выбрасываем
    assert _wait(lambda: not tm._timers)
    assert not ev.wait(0.1) and fired == []
    assert json.load(open(path, encoding="utf-8")) == []
    tm.stop()


def test_several_due_fire_in_order_with_one_thread(clock):
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb)
    before = sum(t.name == "timers" for t in threading.enumerate())
    for i, sec in enumerate((30, 10, 20)):
        tm.add(sec, f"t{sec}")
    assert sum(t.name == "timers" for t in threading.enumerate()) - before <= 1    # один поток на все таймеры
    clock[0] += 31
    assert _wait(lambda: len(fired) == 3) and fired == ["t10", "t20", "t30"]
    tm.stop()


def test_cancel_before_due(clock):
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb)
    tm.add(60, "x")
    tm.add(120, "y")
    assert tm.cancel_all() == 2
    clock[0] += 1000
    assert not ev.wait(0.2) and fired == []
    assert _wait(lambda: tm._thread is None)           # таймеров нет — поток завершился сам


def test_cancel_during_fire_does_not_deadlock_or_repeat(clock):
    started, release, fired = threading.Event(), threading.Event(), []

    def slow(msg):                                     # «озвучка» идёт долго
        fired.append(msg)
        started.set()
        release.wait(3)

    tm = TimerManager(slow)
    tm.add(10, "первый")
    tm.add(500, "второй")
    clock[0] += 11
    assert started.wait(3)
    t0 = time.monotonic()
    assert tm.cancel_all() == 1                        # колбэк идёт без замка — отмена не ждёт озвучку
    assert time.monotonic() - t0 < 0.5
    release.set()
    clock[0] += 1000
    time.sleep(0.1)
    assert fired == ["первый"]                         # «первый» не повторился, «второй» отменён
    tm.stop()


def test_add_from_callback_does_not_deadlock(clock):
    fired = []
    tm = TimerManager(None)
    tm._on_fire = lambda m: (fired.append(m), m == "a" and tm.add(5, "b"))
    tm.add(1, "a")
    clock[0] += 2
    assert _wait(lambda: fired == ["a"])
    clock[0] += 10
    assert _wait(lambda: fired == ["a", "b"])
    tm.stop()


def test_callback_error_does_not_kill_thread(clock):
    fired = []

    def cb(m):
        fired.append(m)
        if m == "boom":
            raise RuntimeError("сломалось")

    tm = TimerManager(cb)
    tm.add(1, "boom")
    tm.add(5, "after")
    clock[0] += 2
    assert _wait(lambda: fired == ["boom"])
    clock[0] += 10
    assert _wait(lambda: fired == ["boom", "after"])
    tm.stop()


def test_restart_restores_from_file_and_wall_clock(clock, tmp_path):
    path = str(tmp_path / "timers.json")
    tm = TimerManager(lambda m: None, storage_path=path)
    tm.add(600, "Напоминаю: чайник.")
    saved = json.load(open(path, encoding="utf-8"))
    assert saved == [{"at": clock[0] + 600, "message": "Напоминаю: чайник."}]   # формат файла прежний
    tm.stop()
    assert json.load(open(path, encoding="utf-8")) == saved                   # выход файл не трогает

    clock[0] += 200                                    # перезапуск через 200 с
    fired, ev, cb = _fired_list()
    tm2 = TimerManager(cb, storage_path=path)
    assert tm2.restore() == (1, 0)
    assert tm2.describe() == [("6 минут 40 секунд", "Напоминаю: чайник.")]
    clock[0] += 400
    assert ev.wait(3) and fired == ["Напоминаю: чайник."]
    assert json.load(open(path, encoding="utf-8")) == []
    tm2.stop()


def test_restore_overdue_keeps_old_note_without_late_prefix(clock, tmp_path):
    path = tmp_path / "timers.json"
    path.write_text(json.dumps([{"at": clock[0] - 3600, "message": "чайник"}]), encoding="utf-8")   # старая запись
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb, storage_path=str(path))
    assert tm.restore(startup_delay=10) == (0, 1)
    assert not ev.wait(0.1)                            # ждёт startup_delay
    clock[0] += 11
    assert ev.wait(3) and fired == ["Пока меня не было, сработало: чайник"]
    tm.stop()


def test_stop_ends_thread_and_keeps_file(clock, tmp_path):
    path = str(tmp_path / "timers.json")
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb, storage_path=path)
    tm.add(60, "x")
    t = tm._thread
    assert t.is_alive()
    tm.stop()
    assert not t.is_alive() and tm._thread is None
    clock[0] += 100
    assert not ev.wait(0.1) and fired == []
    assert len(json.load(open(path, encoding="utf-8"))) == 1
    tm.stop()                                          # повторный stop — без ошибок


def test_stop_during_fire_returns(clock):
    started, release = threading.Event(), threading.Event()
    tm = TimerManager(lambda m: (started.set(), release.wait(5)))
    tm.add(1, "долго")
    tm.add(2, "не успеет")
    clock[0] += 5
    assert started.wait(3)
    t0 = time.monotonic()
    tm.stop(timeout=0.3)                               # колбэк висит — stop не виснет вместе с ним
    assert time.monotonic() - t0 < 1.0
    release.set()


def test_add_after_stop_works_again(clock):
    fired, ev, cb = _fired_list()
    tm = TimerManager(cb)
    tm.add(10, "a")
    tm.stop()
    tm.add(10, "b")
    clock[0] += 11
    assert ev.wait(3) and fired == ["b"]
    tm.stop()


@pytest.mark.parametrize("late,text", [
    (61, "Опоздала на 1 минуту. "), (5 * 60, "Опоздала на 5 минут. "), (21 * 60, "Опоздала на 21 минуту. "),
    (3 * 60, "Опоздала на 3 минуты. "), (125 * 60, "Опоздала на 2 часа 5 минут. "), (3600, "Опоздала на 1 час. "),
])
def test_late_note(late, text):
    assert late_note(late) == text
