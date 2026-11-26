"""Выключение / перезагрузка компьютера по команде («выключи комп через 30 минут», «отмени выключение»)."""
import datetime
import re
import subprocess

from scenarios import apply_part, parse_clock
from timers import parse_duration

# Только сам компьютер. Раньше было компьютер\w*, комп\w*, систем\w+ — и «выключи компьютерную игру»,
# «выключи композицию», «выключи системные звуки» спрашивали, выключить ли компьютер.
_DEV = r"(?:компьютер(?:а|у|ом)?\b|комп(?:а|у)?\b|пк\b|ноут(?:бук\w*)?\b|систему\b|windows|винд(?:у|ows)?\b)"
_RE_CANCEL = re.compile(r"\b(?:отмени\w*|не\s+(?:выключай|перезагружай)|стоп)\s+(?:\w+\s+)?(?:выключени\w*|перезагрузк\w*|завершени\w*)"
                        r"|\bне\s+(?:выключай|перезагружай)\s+" + _DEV)
_RE_OFF = re.compile(r"\b(?:выключи\w*|отключи\w*|заверши\s+работу|завершение\s+работы|выключение)\s+(?:мне\s+)?" + _DEV
                     + r"|\b" + _DEV + r"\s+(?:выключи\w*)")
_RE_RESTART = re.compile(r"\b(?:перезагрузи\w*|перезапусти\w*|перезагрузка|ребутни)\s+(?:мне\s+)?" + _DEV)

UNKNOWN = -1            # время названо, но не разобрано: переспросить, а не выключать «сейчас»
_RE_TIME_WORDS = re.compile(r"\d|\b(?:через|утр\w*|вечер\w*|ноч\w*|днём|днем|полдень|полночь|пол\w+ого|"
                            r"сегодня|завтра|послезавтра)\b")


def parse_delay(lower, now=None):
    """Секунды до выключения: «через 30 минут», «через час десять минут», «в 23:30», «в 11 вечера», «в 23».
    Время не названо — 0 (сейчас). Названо, но не разобрано («к 23:00», «завтра», «вечером») — UNKNOWN.
    Разбор — общий с таймерами (timers.parse_duration) и расписаниями (scenarios.parse_clock): раньше здесь был
    свой, и «через час десять минут» давало 10 минут, а «в 11 вечера» — «сейчас»."""
    now = now or datetime.datetime.now()
    if re.search(r"\bчерез\b", lower):
        secs, _ = parse_duration(lower)
        return int(secs) if secs else UNKNOWN
    clock = parse_clock(lower)
    if clock:
        hh, mm, _start, _end, part, _explicit = clock
        t = now.replace(hour=apply_part(hh, part) % 24, minute=mm, second=0, microsecond=0)
        if t <= now:
            t += datetime.timedelta(days=1)
        return int((t - now).total_seconds())
    return UNKNOWN if _RE_TIME_WORDS.search(lower) else 0


def parse(lower):
    """→ ('cancel'|'shutdown'|'restart', секунды) или None."""
    if _RE_CANCEL.search(lower):
        return "cancel", 0
    if _RE_RESTART.search(lower):
        return "restart", parse_delay(lower)
    if _RE_OFF.search(lower):
        return "shutdown", parse_delay(lower)
    return None


def when(sec):
    if sec < 60:
        return "сейчас"
    if sec % 3600 == 0:
        h = sec // 3600
        return "через час" if h == 1 else f"через {h} ч"
    if sec >= 3600:
        return f"через {sec // 3600} ч {sec % 3600 // 60} мин"
    return f"через {sec // 60} мин"


MIN_DELAY = 20          # «сейчас» — всё равно 20 с: успеть сохранить и чтобы ответ ушёл в Telegram


def run(kind, sec, confirm):
    if kind == "cancel":
        r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
        return "Отменила выключение." if r.returncode == 0 else "Выключение и так не было запланировано."
    word = "Выключить" if kind == "shutdown" else "Перезагрузить"
    if sec < 0:                         # UNKNOWN: время сказали, но непонятно какое — ничего не выключаем
        verb = "выключи" if kind == "shutdown" else "перезагрузи"
        return (f"Не поняла, когда {word.lower()} компьютер. Скажи, например: «{verb} компьютер через 30 минут» "
                f"или «{verb} компьютер в 23:30».")
    if not confirm(f"{word} компьютер {when(sec)}?"):
        return "Хорошо, не трогаю."
    t = max(MIN_DELAY, int(sec))
    flag = "/s" if kind == "shutdown" else "/r"
    subprocess.run(["shutdown", "/a"], capture_output=True)              # заменить прошлый план, если был
    r = subprocess.run(["shutdown", flag, "/t", str(t)], capture_output=True, text=True)
    if r.returncode != 0:
        return f"Windows не дала: {(r.stderr or r.stdout).strip()[:200]}"
    act = "Выключу" if kind == "shutdown" else "Перезагружу"
    return (f"{act} компьютер {when(sec)}." if sec >= 60 else f"{act} компьютер через {t} секунд — сохрани всё.") \
        + " Передумаешь — скажи «отмени выключение»."
