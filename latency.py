"""Замер задержки голосового ответа: конец твоей фразы → текст → понимание → первый звук её ответа.

Каждый ответ — одна строка в консоли и в logs/latency.log, например:
  ⏱ тишина 1.2 | распознавание 0.7 | понимание 0.1 | до первого звука 1.4 | всего 3.4 с
"""
import os
import time

_t = {}
_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "latency.log")


def start(t_speech_end, silence):
    """Запись команды кончилась: t_speech_end — когда ты замолчал, silence — сколько ждали тишину."""
    _t.clear()
    _t.update(end=t_speech_end, silence=silence, stop=time.monotonic())


def mark(name):
    if _t and name not in _t:
        _t[name] = time.monotonic()


def first_sound():
    """Первый звук ответа пошёл — печатаем раскладку (один раз на команду)."""
    if not _t or "sound" in _t:
        return
    now = _t["sound"] = time.monotonic()
    end, stop = _t["end"], _t["stop"]
    stt_t, intent_t = _t.get("stt", stop), _t.get("intent", _t.get("stt", stop))
    if now - end > 60:
        _t.clear()
        return
    parts = [("тишина", stop - end), ("распознавание", stt_t - stop), ("понимание", intent_t - stt_t),
             ("до первого звука", now - intent_t)]
    line = " | ".join(f"{n} {v:.1f}" for n, v in parts) + f" | всего {now - end:.1f} с"
    if _t.get("what"):
        line += f"  [{_t['what']}]"
    print(f"⏱ {line}")
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def note(what):
    if _t:
        _t["what"] = what


def done():
    _t.clear()
