"""Время и дата по-русски. Берутся с часов компьютера — LLM их не знает и раньше выдумывала."""
import datetime

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _plural(n, one, few, many):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def say_time(now=None):
    now = now or datetime.datetime.now()
    h, m = now.hour, now.minute
    hours = f"{h} {_plural(h, 'час', 'часа', 'часов')}"
    if m == 0:
        return f"Сейчас ровно {hours}."
    return f"Сейчас {hours} {m} {_plural(m, 'минута', 'минуты', 'минут')}."


def say_date(now=None):
    now = now or datetime.datetime.now()
    return f"Сегодня {_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} {now.year} года."


def say_now(kind="time", now=None):
    """kind: 'time' | 'date' | 'both'"""
    now = now or datetime.datetime.now()
    if kind == "date":
        return say_date(now)
    if kind == "both":
        return f"{say_date(now)} {say_time(now)}"
    return say_time(now)


def now_for_prompt(now=None):
    """Строка для системного промпта LLM."""
    now = now or datetime.datetime.now()
    return (f"{_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} {now.year} года, "
            f"{now.hour:02d}:{now.minute:02d}")
