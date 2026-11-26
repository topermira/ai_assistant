"""Расписания и сценарии.

Расписание — команда, которая сама срабатывает в нужное время:
  «каждый день в 8:30 скажи погоду», «по будням в 9 напомни про планёрку», «в 23:00 сверни все окна»,
  «каждый понедельник в 10:00 запусти сценарий работа», «завтра в 7 утра напомни выпить воду».
  «какое расписание» — список, «удали расписание 2» / «удали все расписания» — удалить.
Сценарий — именованная цепочка команд:
  «создай сценарий работа: открой pycharm, громкость 20, открой яндекс»,
  «запусти сценарий работа», «какие сценарии», «что в сценарии работа», «удали сценарий работа».

Разбор фраз — чистые функции (есть тесты), хранение — два JSON-файла рядом с программой.
Если Рокси была выключена в нужное время, пропущенное повторяющееся задание не выполняется (ждёт следующего раза),
разовое выполняется, если опоздание не больше 10 минут.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

MAX_STEPS = 20
MAX_PLANS = 50
GRACE_SEC = 600
REMIND_LATE_SEC = 6 * 3600     # напоминание, проспанное ПК, ещё скажем — с пометкой «опоздала на …» (команды — нет)

_DAY_STEMS = [("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3), ("пятниц", 4), ("суббот", 5), ("воскресень", 6)]
_DAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_DAY_NAMES_ON = ["по понедельникам", "по вторникам", "по средам", "по четвергам", "по пятницам", "по субботам", "по воскресеньям"]


# ---------------------------------------------------------------- время
_ONES = {"ноль": 0, "один": 1, "одна": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
         "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
         "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}


def _read_num(tokens, i, limit):
    """Слова-числа с позиции i: «двадцать два» → (22, i+2). None, если числа нет или оно больше limit."""
    if i >= len(tokens):
        return None
    w = tokens[i]
    if w.isdigit():
        v = int(w)
        return (v, i + 1) if v <= limit else None
    if w in _TENS:
        v = _TENS[w]
        if i + 1 < len(tokens) and tokens[i + 1] in _ONES and 1 <= _ONES[tokens[i + 1]] <= 9:
            v += _ONES[tokens[i + 1]]
            return (v, i + 2) if v <= limit else None
        return (v, i + 1) if v <= limit else None
    if w == "ноль" and i + 1 < len(tokens) and tokens[i + 1] in _ONES and 1 <= _ONES[tokens[i + 1]] <= 9:
        return _ONES[tokens[i + 1]], i + 2                      # «ноль пять» → 5
    if w in _ONES:
        v = _ONES[w]
        return (v, i + 1) if v <= limit else None
    return None


_PART = {"утра": "am", "утром": "am", "дня": "pm", "днём": "pm", "днем": "pm", "вечера": "pm", "вечером": "pm", "ночи": "night", "ночью": "night"}


def parse_clock(lower):
    """Первое «в 8», «в 8:30», «в 20.15», «в восемь тридцать», «в 8 утра», «в полдень» → (hh, mm, start, end, part, explicit) или None.
    part: 'am' / 'pm' / 'night' / None — как сказано; пересчёт в 24 часа делает apply_part.
    explicit — время названо однозначно (минуты, «утра/вечера», полдень): «в 3 раза» — это не время."""
    for m in re.finditer(r"(?<![\wа-яё])(?:в|во|к)\s+(?:пол(?:день|ночь))(?![\wа-яё])", lower):
        return (12 if "полд" in m.group(0) else 0), 0, m.start(), m.end(), None, True
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[0-9]+(?:[:.][0-9]{2})?|[а-яё]+", lower)]
    words = [t[0] for t in toks]
    for i, w in enumerate(words):
        if w not in ("в", "во"):
            continue
        j = i + 1
        if j >= len(words):
            continue
        hh = mm = None
        explicit = False
        m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", words[j])
        if m:
            hh, mm, k = int(m.group(1)), int(m.group(2)), j + 1
            explicit = True
        else:
            r = _read_num(words, j, 24)
            if not r:
                continue
            hh, k = r
            if k < len(words) and words[k] in ("час", "часа", "часов"):
                k += 1
            m2 = re.fullmatch(r"(\d{2})", words[k]) if k < len(words) else None
            if m2 and int(m2.group(1)) <= 59:
                mm, k, explicit = int(m2.group(1)), k + 1, True
            else:
                r2 = _read_num(words, k, 59)
                if r2:
                    mm, k, explicit = r2[0], r2[1], True
                else:
                    mm = 0
            if words[j].isdigit() and len(words[j]) > 2:
                continue
        if hh > 24 or mm > 59:
            continue
        part = None
        if k < len(words) and words[k] in _PART:
            part = _PART[words[k]]
            k += 1
            explicit = True
        return hh % 24, mm, toks[i][1], toks[k - 1][2], part, explicit
    return None


def apply_part(hh, part, evening_hint=False):
    if part == "pm" and hh < 12:
        return hh + 12
    if part == "am" and hh == 12:
        return 0
    if part == "night":
        if hh == 12:
            return 0
        if 9 <= hh < 12:
            return hh + 12
    if part is None and evening_hint and 1 <= hh < 12:
        return hh + 12
    return hh


# ---------------------------------------------------------------- расписание: разбор фразы
_RE_EVERY = re.compile(r"\bкажд(?:ый|ое|ую|ые)\s+(день|утро|вечер|ночь|будн\w*|понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)")
_RE_DAILY = re.compile(r"\b(?:ежедневно|каждодневно|в\s+любой\s+день)\b")
_RE_WEEKDAYS = re.compile(r"\b(?:по\s+будням|в\s+будни|по\s+рабочим\s+дням|в\s+рабочие\s+дни)\b")
_RE_WEEKENDS = re.compile(r"\b(?:по\s+выходным|в\s+выходные|в\s+выходные\s+дни)\b")
_RE_PO_DAYS = re.compile(r"\bпо\s+((?:(?:понедельник|вторник|сред|четверг|пятниц|суббот|воскресень)\w*(?:\s*(?:,|и)\s*)?)+)")
_RE_ONCE_DAY = re.compile(r"\b(?:в|во)\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)\b")
_RE_DATEWORD = re.compile(r"\b(сегодня|завтра|послезавтра)\b")
_RE_LEAD = re.compile(r"^\s*(?:рокси\W+)?(?:запланируй|запиши\s+в\s+расписание|добавь\s+в\s+расписание|добавь\s+расписание|поставь\s+в\s+расписание|"
                      r"поставь\s+расписание|создай\s+расписание|создай\s+задание)\b")
_RE_REMIND = re.compile(r"\bнапомни(?:ть)?(?:\s+мне)?\b")
# Действие по расписанию должно начинаться с команды: «в 23:00 сверни все окна», «каждый день в 8:30 скажи погоду»,
# «в 23:00 громкость 10». Раньше расписанием становилась любая фраза со временем — и «в 8 утра я проснулся…» тоже.
_RE_CMD_START = re.compile(
    r"^(?:(?:пожалуйста|ну|давай|плиз)\s+)*"
    r"(?:скажи|расскажи|напомни|открой|запусти|включи|выключи|отключи|сверни|разверни|закрой|заблокируй|переведи|"
    r"усыпи|перезагрузи|заверши|поставь|сделай|проверь|покажи|прочитай|прочти|зачитай|озвучь|напиши|отправь|"
    r"пришли|скинь|создай|удали|убери|очисти|найди|сыграй|поиграй|спой|пожелай|разбуди|запиши|нажми|кликни|"
    r"убавь|прибавь|увеличь|уменьши|верни|продолжи|отмени|"
    r"громкость|яркость|громче|тише|погромче|потише|пауза|стоп|звук|сценарий)\b")


def _day_of(word):
    for stem, n in _DAY_STEMS:
        if word.startswith(stem):
            return n
    return None


def _find_recurrence(lower):
    """→ (days|None, [(start,end)...], evening_hint, found). days — номера 0..6 (пн=0)."""
    spans = []
    m = _RE_PO_DAYS.search(lower)
    if m:
        days = sorted({_day_of(w) for w in re.findall(r"[а-яё]+", m.group(1)) if _day_of(w) is not None})
        if days:
            return days, [m.span()], False, True
    m = _RE_WEEKDAYS.search(lower)
    if m:
        return [0, 1, 2, 3, 4], [m.span()], False, True
    m = _RE_WEEKENDS.search(lower)
    if m:
        return [5, 6], [m.span()], False, True
    m = _RE_DAILY.search(lower)
    if m:
        return list(range(7)), [m.span()], False, True
    m = _RE_EVERY.search(lower)
    if m:
        w = m.group(1)
        spans = [m.span()]
        if w.startswith("будн"):
            return [0, 1, 2, 3, 4], spans, False, True
        d = _day_of(w)
        if d is not None:
            return [d], spans, False, True
        return list(range(7)), spans, w == "вечер", True
    return None, [], False, False


def _days_text(days):
    if days is None:
        return ""
    if days == list(range(7)):
        return "каждый день"
    if days == [0, 1, 2, 3, 4]:
        return "по будням"
    if days == [5, 6]:
        return "по выходным"
    return " и ".join(_DAY_NAMES_ON[d] for d in days)


def parse_plan(text, now=None):
    """Фраза → dict(days, date, time, kind, text) | ('ask',) если не хватает времени/действия | None, если это не расписание.
    days — список дней недели (пн=0) для повторяющихся, date — 'YYYY-MM-DD' для разовых.
    Действие (то, что осталось без времени и дней) должно начинаться с команды (_RE_CMD_START); исключения —
    «напомни …» и явное «запланируй …». «В 8 утра я проснулся…» — рассказ, а не расписание."""
    lower = (text or "").lower()
    now = now or datetime.now()
    days, rec_spans, evening, recurring = _find_recurrence(lower)
    clock = parse_clock(lower)
    date_m = _RE_DATEWORD.search(lower)
    once_day = _RE_ONCE_DAY.search(lower)
    lead = _RE_LEAD.match(lower)
    remind = _RE_REMIND.search(lower)
    starts_with_time = bool(clock) and clock[5] and not re.sub(r"^(?:рокси\W+)?", "", lower[:clock[2]]).strip()
    if not clock:
        if recurring and re.search(r"\bкаждый\s+день|\bежедневно|\bпо\s+будням|\bкаждый\s+(?:понедельник|вторник|четверг|пятницу)", lower) \
                and re.search(r"\b(?:скажи|открой|включи|напомни|запусти|сверни|сделай|проверь|покажи)\b", lower):
            return ("ask",)
        return None
    # без повторения и без даты — расписанием считаем только явную форму: «в 23:00 сверни…», «напомни в 18:00 …», «запланируй …»
    if not (recurring or date_m or once_day or lead or starts_with_time or remind):
        return None
    hh, mm, a, b, part, _explicit = clock
    hh = apply_part(hh, part, evening_hint=evening)
    spans = list(rec_spans) + [(a, b)]
    spec_days, spec_date = None, None
    if recurring:
        spec_days = days
    else:
        base = now.date()
        if date_m:
            off = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[date_m.group(1)]
            spec_date = base + timedelta(days=off)
            spans.append(date_m.span())
        elif once_day:
            target = {"понедельник": 0, "вторник": 1, "среду": 2, "четверг": 3, "пятницу": 4, "субботу": 5, "воскресенье": 6}[once_day.group(1)]
            delta = (target - base.weekday()) % 7
            if delta == 0 and (hh, mm) <= (now.hour, now.minute):
                delta = 7
            spec_date = base + timedelta(days=delta)
            spans.append(once_day.span())
        else:
            spec_date = base if (hh, mm) > (now.hour, now.minute) else base + timedelta(days=1)
    rest = lower
    for s, e in sorted(spans, reverse=True):
        rest = rest[:s] + " " + rest[e:]
    kind = "cmd"
    if remind:
        kind = "remind"
        rest = _RE_REMIND.sub(" ", rest, count=1)
    rest = _RE_LEAD.sub(" ", rest, count=1)
    rest = re.sub(r"\bрокси\b", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.!?:;—-")
    rest = re.sub(r"^(?:и|а|то|чтобы|чтоб|что|мне|меня)\s+", "", rest).strip(" ,.!?:;—-")
    if not rest:
        return ("ask",)
    if kind == "cmd" and not lead and not _RE_CMD_START.match(rest):
        return None
    # оригинальный регистр действия — из исходного текста невозможен после замен; команды нечувствительны к регистру
    return {"days": spec_days, "date": spec_date.isoformat() if spec_date else None,
            "time": f"{hh:02d}:{mm:02d}", "kind": kind, "text": rest}


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _late_text(minutes):
    """150 → «2 часа 30 минут», 21 → «21 минуту» (после «опоздала на»)."""
    h, m = divmod(int(minutes), 60)
    parts = [f"{h} {_plural(h, 'час', 'часа', 'часов')}"] if h else []
    if m or not h:
        parts.append(f"{m} {_plural(m, 'минуту', 'минуты', 'минут')}")
    return " ".join(parts)


def reminder_phrase(text, late_min=None):
    """«Напоминаю: …». late_min — на сколько минут опоздала (ПК спал или был выключен) — тогда «Опоздала на …» впереди."""
    t = (text or "").strip().rstrip(".")
    phrase = f"Напоминаю {t}." if re.match(r"^(?:про|о|об|насчёт|насчет)\s", t) else f"Напоминаю: {t}."
    return f"Опоздала на {_late_text(late_min)}. {phrase}" if late_min else phrase


def describe_plan(p):
    when = _days_text(p.get("days")) if p.get("days") is not None else _date_text(p.get("date"))
    what = ("напомнить: " if p.get("kind") == "remind" else "") + p.get("text", "")
    return f"{when} в {p.get('time', '??:??')} — {what}"


def _date_text(iso, today=None):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return "разово"
    today = today or datetime.now().date()
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return f"{d.day:02d}.{d.month:02d} ({_DAY_NAMES[d.weekday()]})"


# ---------------------------------------------------------------- сценарии: разбор фраз
_RE_SCEN_SAVE = re.compile(r"^\s*(?:рокси\W+)?(?:создай|запомни|сохрани|добавь|заведи|сделай|запиши)\s+(?:новый\s+)?сценарий\s+(.+)$", re.S)
_RE_SCEN_RUN = re.compile(r"^\s*(?:рокси\W+)?(?:запусти|выполни|включи|активируй|давай|начни|стартуй|запускай)\s+сценари[йюя]\s+(.+?)\s*$", re.S)
# Без глагола — только «сценарий <одно слово>» («сценарий работы»): «сценарий фильма классный» — разговор, а не запуск
_RE_SCEN_RUN_BARE = re.compile(r"^\s*(?:рокси\W+)?сценари[йюя]\s+(\w+)\s*[.!]?\s*$")
_RE_SCEN_SHOW = re.compile(r"^\s*(?:рокси\W+)?(?:что\s+(?:в|есть\s+в)|покажи|прочитай|расскажи\s+про|из\s+чего\s+состоит)\s+сценари[йяюи]\w*\s+(.+?)\s*$")
_RE_SCEN_LIST = re.compile(r"^\s*(?:рокси\W+)?(?:какие\s+(?:у\s+тебя\s+|у\s+меня\s+|есть\s+)?сценари\w+(?:\s+есть)?|(?:покажи|скажи|перечисли|список)\s+(?:мои\s+|все\s+)?сценари\w+|мои\s+сценари\w+)\s*[?.!]*\s*$")
_RE_SCEN_DEL = re.compile(r"^\s*(?:рокси\W+)?(?:удали|убери|забудь|сотри|выбрось)\s+сценари[йюя]\s+(.+?)\s*$")
_SCEN_DELIMS = [":", "—", " - ", " это ", " состоит из ", " включает ", " включает в себя ", " делает ", " чтобы ", " а именно "]
_STEP_SPLIT = re.compile(r"\s*(?:;|,|\n|\s+(?:а\s+)?потом\s+|\s+затем\s+|\s+после\s+этого\s+|\s+и\s+затем\s+)\s*")
_RE_PLAN_LIST = re.compile(r"^\s*(?:рокси\W+)?(?:(?:покажи|скажи|дай|открой|прочитай)\s+(?:мне\s+)?(?:мо[ёе]\s+|все\s+)?(?:расписани\w+|запланированн\w+|планы|задани\w+\s+по\s+расписанию)|"
                           r"какое\s+(?:у\s+меня\s+)?расписани\w+|что\s+(?:у\s+меня\s+)?запланировано|какие\s+(?:у\s+меня\s+)?(?:расписания|запланированные\s+задания|планы)|мо[её]\s+расписани\w+|список\s+расписани\w+)\s*[?.!]*\s*$")
_RE_PLAN_DEL = re.compile(r"^\s*(?:рокси\W+)?(?:удали|отмени|убери|сотри|выключи)\s+(все\s+)?(?:расписани\w+|запланированн\w+(?:\s+задани\w+)?|задани\w+\s+по\s+расписанию)(?:\s+(?:номер\s+|№\s*|#)?(\d+))?\s*[.!]*\s*$")


def split_steps(rest):
    steps = [s.strip(" .!?«»\"'") for s in _STEP_SPLIT.split(rest or "")]
    return [s for s in steps if s][:MAX_STEPS]


def clean_name(name):
    return re.sub(r"\s+", " ", (name or "").strip(" .!?«»\"':,;").lower().replace("ё", "е"))


def parse(text, now=None):
    """→ ('plan_add', spec) | ('plan_ask',) | ('plan_list',) | ('plan_del', n|'all')
       | ('scen_save', name, [steps]) | ('scen_ask',) | ('scen_run', name) | ('scen_show', name) | ('scen_list',) | ('scen_del', name) | None"""
    raw = (text or "").strip()
    lower = raw.lower()
    m = _RE_PLAN_LIST.match(lower)
    if m:
        return ("plan_list",)
    m = _RE_PLAN_DEL.match(lower)
    if m:
        return ("plan_del", "all" if m.group(1) else (int(m.group(2)) if m.group(2) else "ask"))
    if _RE_SCEN_LIST.match(lower):
        return ("scen_list",)
    m = _RE_SCEN_DEL.match(lower)
    if m:
        return ("scen_del", clean_name(m.group(1)))
    m = _RE_SCEN_SAVE.match(raw)
    if m:
        body = m.group(1)
        cut = None
        for d in _SCEN_DELIMS:
            i = body.lower().find(d)
            if i > 0 and (cut is None or i < cut[0]):
                cut = (i, len(d))
        if cut is None:
            return ("scen_ask",)
        name, steps = clean_name(body[:cut[0]]), split_steps(body[cut[0] + cut[1]:])
        if not name or not steps:
            return ("scen_ask",)
        return ("scen_save", name, steps)
    m = _RE_SCEN_SHOW.match(lower)
    if m:
        return ("scen_show", clean_name(m.group(1)))
    m = _RE_SCEN_RUN.match(lower) or _RE_SCEN_RUN_BARE.match(lower)
    if m:
        return ("scen_run", clean_name(m.group(1)))
    plan = parse_plan(raw, now)
    if plan is None:
        return None
    if isinstance(plan, tuple):
        return ("plan_ask",)
    return ("plan_add", plan)


# ---------------------------------------------------------------- хранение
def _stem_eq(a, b):
    if a == b:
        return True
    if len(a) < 3 or len(b) < 3 or abs(len(a) - len(b)) > 3:
        return False
    k = max(3, min(len(a), len(b)) - 2)
    return a[:k] == b[:k]


class Planner:
    def __init__(self, scenarios_path=None, plans_path=None):
        self._sp, self._pp = scenarios_path, plans_path
        self._lock = threading.Lock()
        self.scenarios = {}      # имя → [шаги]
        self.plans = []          # dict: id, days|None, date|None, time, kind, text, last
        self._next_id = 1
        self._thread = None
        self._stop = threading.Event()
        self.on_due = None
        self._load()

    # ---- файлы
    @staticmethod
    def _read(path, default):
        if not path or not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ {os.path.basename(path)}: не смогла прочитать: {e}")
            return default

    @staticmethod
    def _write(path, data):
        if not path:
            return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception as e:
            print(f"⚠️ {os.path.basename(path)}: не смогла сохранить: {e}")

    def _load(self):
        sc = self._read(self._sp, {})
        if isinstance(sc, dict):
            self.scenarios = {clean_name(k): [str(s) for s in v][:MAX_STEPS] for k, v in sc.items() if isinstance(v, list) and v}
        pl = self._read(self._pp, [])
        for p in pl if isinstance(pl, list) else []:
            try:
                if not re.fullmatch(r"\d\d:\d\d", p["time"]) or not str(p["text"]).strip():
                    continue
                self.plans.append({"id": self._next_id, "days": p.get("days"), "date": p.get("date"), "time": p["time"],
                                   "kind": "remind" if p.get("kind") == "remind" else "cmd", "text": str(p["text"]),
                                   "last": p.get("last")})
                self._next_id += 1
            except (KeyError, TypeError):
                continue

    def _save_plans(self):
        self._write(self._pp, [{k: v for k, v in p.items() if k != "id"} for p in self.plans])

    def _save_scen(self):
        self._write(self._sp, self.scenarios)

    # ---- сценарии
    def save_scenario(self, name, steps):
        with self._lock:
            existed = name in self.scenarios
            self.scenarios[name] = list(steps)[:MAX_STEPS]
            self._save_scen()
        return existed

    def find_scenario(self, name):
        """→ (имя, шаги) | (None, [похожие имена])"""
        name = clean_name(name)
        with self._lock:
            if name in self.scenarios:
                return name, list(self.scenarios[name])
            words = name.split()
            hits = [n for n in self.scenarios if len(n.split()) == len(words) and all(_stem_eq(a, b) for a, b in zip(words, n.split()))]
            if len(hits) == 1:
                return hits[0], list(self.scenarios[hits[0]])
            return None, hits or list(self.scenarios)[:6]

    def delete_scenario(self, name):
        real, _ = self.find_scenario(name)
        if not real:
            return None
        with self._lock:
            self.scenarios.pop(real, None)
            self._save_scen()
        return real

    # ---- расписания
    def _sorted(self):
        return sorted(self.plans, key=lambda p: (p["time"], p["id"]))

    def add_plan(self, spec):
        with self._lock:
            if len(self.plans) >= MAX_PLANS:
                return None
            dup = [p for p in self.plans if (p["days"], p["date"], p["time"], p["kind"], p["text"]) ==
                   (spec["days"], spec["date"], spec["time"], spec["kind"], spec["text"])]
            if dup:
                return dup[0]["id"]
            p = dict(spec, id=self._next_id, last=None)
            self._next_id += 1
            self.plans.append(p)
            self._save_plans()
            return p["id"]

    def list_plans(self):
        with self._lock:
            return [describe_plan(p) for p in self._sorted()]

    def delete_plan(self, n):
        """n — номер из списка (с 1) или 'all'. → сколько удалено."""
        with self._lock:
            if n == "all":
                k = len(self.plans)
                self.plans.clear()
            else:
                items = self._sorted()
                if not (1 <= n <= len(items)):
                    return 0
                self.plans.remove(items[n - 1])
                k = 1
            self._save_plans()
            return k

    def due(self, now=None):
        """Задания, которым пора сработать. Помечает их выполненными, разовые удаляет, протухшие разовые тоже.
        Команда срабатывает с опозданием не больше GRACE_SEC, иначе пропускается. Напоминание (kind="remind") — с
        опозданием до REMIND_LATE_SEC (ПК спал или был выключен); тогда в задании поле late_min (минуты опоздания)."""
        now = now or datetime.now()
        today = now.date().isoformat()
        out = []
        with self._lock:
            changed = False
            for p in list(self.plans):
                hh, mm = map(int, p["time"].split(":"))
                due_dt = datetime.combine(now.date(), datetime.min.time()).replace(hour=hh, minute=mm)
                once = p["days"] is None
                remind = p.get("kind") == "remind"
                if once:
                    try:
                        d = datetime.strptime(p["date"], "%Y-%m-%d").date()
                    except (TypeError, ValueError):
                        self.plans.remove(p)
                        changed = True
                        continue
                    if remind:
                        # от своей даты: напоминание на 23:30, а ПК проснулся в 00:40 — опоздание, а не «дата прошла»
                        due_dt = datetime.combine(d, datetime.min.time()).replace(hour=hh, minute=mm)
                        applies = True
                    elif d < now.date():
                        self.plans.remove(p)
                        changed = True
                        continue
                    else:
                        applies = d == now.date()
                else:
                    applies = now.weekday() in (p["days"] or [])
                if not applies:
                    continue
                late = (now - due_dt).total_seconds()
                if late < 0:
                    continue
                if late > (REMIND_LATE_SEC if remind else GRACE_SEC):
                    if once:
                        self.plans.remove(p)
                        changed = True
                    continue
                if p.get("last") == today:
                    continue
                p["last"] = today
                changed = True
                item = dict(p)
                if remind and late > GRACE_SEC:
                    item["late_min"] = int(late // 60)
                out.append(item)
                if once:
                    self.plans.remove(p)
            if changed:
                self._save_plans()
        return out

    # ---- фоновый поток
    def start(self, on_due):
        self.on_due = on_due
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="planner")
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(15.0):
            try:
                for p in self.due():
                    threading.Thread(target=self._fire, args=(p,), daemon=True, name="plan-run").start()
            except Exception as e:
                print(f"⚠️ расписание: {type(e).__name__}: {e}")

    def _fire(self, p):
        try:
            if self.on_due:
                self.on_due(p)
        except Exception as e:
            print(f"⚠️ расписание не выполнилось: {type(e).__name__}: {e}")

    def stop(self):
        self._stop.set()


_PLANNER = None


def get_planner():
    global _PLANNER
    if _PLANNER is None:
        try:
            from settings import PROJECT_DIR
        except Exception:
            PROJECT_DIR = os.getcwd()
        _PLANNER = Planner(os.path.join(PROJECT_DIR, "scenarios.json"), os.path.join(PROJECT_DIR, "schedules.json"))
    return _PLANNER
