"""Выключение / перезагрузка компьютера по команде («выключи комп через 30 минут», «отмени выключение»)."""
import datetime
import re
import subprocess

# Только сам компьютер. Раньше было компьютер\w*, комп\w*, систем\w+ — и «выключи компьютерную игру»,
# «выключи композицию», «выключи системные звуки» спрашивали, выключить ли компьютер.
_DEV = r"(?:компьютер(?:а|у|ом)?\b|комп(?:а|у)?\b|пк\b|ноут(?:бук\w*)?\b|систему\b|windows|винд(?:у|ows)?\b)"
_RE_CANCEL = re.compile(r"\b(?:отмени\w*|не\s+(?:выключай|перезагружай)|стоп)\s+(?:\w+\s+)?(?:выключени\w*|перезагрузк\w*|завершени\w*)"
                        r"|\bне\s+(?:выключай|перезагружай)\s+" + _DEV)
_RE_OFF = re.compile(r"\b(?:выключи\w*|отключи\w*|заверши\s+работу|завершение\s+работы|выключение)\s+(?:мне\s+)?" + _DEV
                     + r"|\b" + _DEV + r"\s+(?:выключи\w*)")
_RE_RESTART = re.compile(r"\b(?:перезагрузи\w*|перезапусти\w*|перезагрузка|ребутни)\s+(?:мне\s+)?" + _DEV)

_NUM = {"одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5, "десять": 10,
        "пятнадцать": 15, "двадцать": 20, "тридцать": 30, "сорок": 40, "сорок пять": 45, "пятьдесят": 50}


def parse_delay(lower, now=None):
    """Секунды до выключения: «через 30 минут», «через час», «через полчаса», «в 23:30». Нет — 0."""
    if re.search(r"через\s+полчаса", lower):
        return 1800
    if re.search(r"через\s+полтора\s+часа", lower):
        return 5400
    m = re.search(r"через\s+(\d+|[а-я]+(?:\s+[а-я]+)?)?\s*(минут\w*|мин\b|час\w*|секунд\w*|сек\b)", lower)
    if m:
        n_raw = (m.group(1) or "").strip()
        n = int(n_raw) if n_raw.isdigit() else _NUM.get(n_raw, _NUM.get(n_raw.split()[-1], 1) if n_raw else 1)
        unit = m.group(2)
        return n * (3600 if unit.startswith("час") else 1 if unit.startswith("сек") else 60)
    m = re.search(r"\bв\s+(\d{1,2})[:.](\d{2})\b", lower)
    if m:
        now = now or datetime.datetime.now()
        t = now.replace(hour=int(m.group(1)) % 24, minute=int(m.group(2)), second=0, microsecond=0)
        if t <= now:
            t += datetime.timedelta(days=1)
        return int((t - now).total_seconds())
    return 0


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
