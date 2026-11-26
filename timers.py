"""Таймеры и напоминания. Разбор длительности — чистая функция (есть тесты)."""
import os
import re
import json
import time
import threading

_NUM_WORDS = {
    "одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "полторы": 1.5, "полтора": 1.5,
}
_UNITS = [
    (r"сек\w*", 1),
    (r"мин\w*", 60),
    (r"час\w*", 3600),
]
_NUM = r"(\d+(?:[.,]\d+)?|(?:" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")(?:\s+(?:" + \
       "|".join(["один", "одну", "две", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]) + r"))?)"


def _to_number(s):
    s = s.strip().lower().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        pass
    total = 0
    for w in s.split():
        total += _NUM_WORDS.get(w, 0)
    return total or None


def parse_duration(text):
    """'на 5 минут' → 300, 'через полчаса' → 1800, 'на час и 10 минут' → 4200, 'минуту' → 60.
    Возвращает (секунды, остаток_текста) или (None, text)."""
    t = (text or "").lower()
    total = 0
    spans = []
    for m in re.finditer(r"\bполчаса\b", t):
        total += 1800
        spans.append(m.span())
    for m in re.finditer(r"\bполминуты\b", t):
        total += 30
        spans.append(m.span())
    for unit_rx, mult in _UNITS:
        for m in re.finditer(r"(?<![\w.,])" + _NUM + r"\s*" + unit_rx, t):
            n = _to_number(m.group(1))
            if n:
                total += n * mult
                spans.append(m.span())
    # "через минуту", "на час" — без числа
    for word, secs in (("минуту", 60), ("час", 3600), ("секунду", 1)):
        for m in re.finditer(r"\b(?:через|на)\s+(" + word + r")\b", t):
            if not any(a <= m.start(1) < b for a, b in spans):
                total += secs
                spans.append(m.span(1))
    if not total:
        return None, text
    rest = t
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    return int(total), rest


def parse_reminder(text):
    """'напомни через 10 минут выключить чайник' → (600, 'выключить чайник')."""
    secs, rest = parse_duration(text)
    if not secs:
        return None, ""
    rest = re.sub(r"^.*?\bнапомни(?:\s+мне)?\b", " ", rest)
    rest = re.sub(r"\b(?:через|на|и|что|чтобы|о\s+том\s+что|про)\b", " ", rest, count=3)
    rest = re.sub(r"\s+", " ", rest).strip(" ,.!?")
    return secs, rest


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_duration(secs):
    """Для озвучки: «1 час 5 минут», «2 минуты 30 секунд»."""
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h: parts.append(f"{h} {_plural(h, 'час', 'часа', 'часов')}")
    if m: parts.append(f"{m} {_plural(m, 'минута', 'минуты', 'минут')}")
    if s and not h: parts.append(f"{s} {_plural(s, 'секунда', 'секунды', 'секунд')}")
    return " ".join(parts) or "0 секунд"


POLL_SEC = 1.0                       # как часто фоновый поток сверяет часы
LATE_NOTE_SEC = 60                   # опоздание больше минуты (ПК спал) — говорим «Опоздала на …»
MAX_OVERDUE_SEC = 6 * 3600           # опоздание больше — уже не «опоздала», а «Пропустила напоминание: …»


def late_note(late_sec):
    """«Опоздала на 5 минут. » — округляем до минут; «1 минуту», а не «1 минута» (после «на»)."""
    mins = max(1, int(round(late_sec / 60.0)))
    return f"Опоздала на {format_duration(mins * 60).replace('минута', 'минуту')}. "


_RE_MSG_PREFIX = re.compile(r"^(?:(?:Пока меня не было, сработало|Пропустила напоминание|Напоминаю)\s*:\s*)+", re.IGNORECASE)


def missed_phrase(message):
    """Проспано больше MAX_OVERDUE_SEC: «Напоминаю: чайник.» → «Пропустила напоминание: чайник.»
    Свои пометки («Пока меня не было…», «Напоминаю: …», прежнее «Пропустила…») не дублируем."""
    t = _RE_MSG_PREFIX.sub("", (message or "").strip()).strip().rstrip(".!").strip()
    if not t or t.lower() == "напоминаю":
        return "Пропустила напоминание."
    return f"Пропустила напоминание: {t}."


class TimerManager:
    """Таймеры и напоминания. С storage_path переживают перезапуск: «Рокси, пока» больше не теряет напоминания.

    Один фоновый поток раз в POLL_SEC сравнивает time.time() с абсолютным временем срабатывания (due_wall),
    как Planner в scenarios.py. Раньше на каждый таймер был threading.Timer с относительной задержкой — а в Windows
    время сна ПК в ней не учитывается: «через 10 минут» + сон на 5 минут срабатывало через 15. Поток живёт,
    только пока есть таймеры, и сам завершается, когда их не осталось."""

    def __init__(self, on_fire, storage_path=None):
        self._on_fire = on_fire      # on_fire(message)
        self._timers = {}            # tid -> (due_wall, message, note_late)
        self._lock = threading.Lock()
        self._next_id = 1
        self._path = storage_path
        self._thread = None
        self._stop_ev = threading.Event()

    # ---------- хранение ----------
    def _save(self):
        """Вызывать под self._lock. Запись атомарная: сначала во временный файл, потом замена."""
        if not self._path:
            return
        try:
            data = [{"at": due_wall, "message": msg} for due_wall, msg, _ in self._timers.values()]
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            print(f"⚠️ timers: не смогла сохранить: {e}")

    def _schedule(self, due_wall, message, note_late=True):
        """Вызывать под self._lock. Длинные таймеры больше не делятся на части: поток ждёт по POLL_SEC, поэтому
        «таймер на 2000 часов» не упирается в threading.TIMEOUT_MAX (~49,7 суток на Windows) и не даёт OverflowError."""
        tid = self._next_id
        self._next_id += 1
        self._timers[tid] = (float(due_wall), message, note_late)
        self._ensure_thread()
        return tid

    def _ensure_thread(self):
        """Вызывать под self._lock: один поток на менеджер, заводим, если его нет."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_ev = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop_ev,), daemon=True, name="timers")
        self._thread.start()

    def restore(self, max_overdue=MAX_OVERDUE_SEC, startup_delay=4.0):
        """Поднять сохранённые таймеры. Просроченные не больше чем на max_overdue секунд сработают сразу
        (с пометкой), более старые — «Пропустила напоминание: …». Возвращает (восстановлено, просрочено_сработает)."""
        if not self._path or not os.path.exists(self._path):
            return 0, 0
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"⚠️ timers: не смогла прочитать {self._path}: {e}")
            return 0, 0
        now = time.time()
        pending = overdue = 0
        with self._lock:
            for item in data if isinstance(data, list) else []:
                try:
                    at, msg = float(item["at"]), str(item["message"])
                except (KeyError, TypeError, ValueError):
                    continue
                left = at - now
                if left > 0:
                    self._schedule(at, msg)
                    pending += 1
                else:
                    # по одному в секунду, после прогрева голоса; «Опоздала на …» не добавляем — пометка уже своя.
                    # Совсем старые раньше выбрасывались молча — теперь хотя бы скажем, что пропустила.
                    text = "Пока меня не было, сработало: " + msg if -left <= max_overdue else missed_phrase(msg)
                    self._schedule(now + startup_delay + overdue, text, note_late=False)
                    overdue += 1
            self._save()
        return pending, overdue

    # ---------- работа ----------
    def add(self, seconds, message):
        with self._lock:
            tid = self._schedule(time.time() + seconds, message)
            self._save()
            return tid

    def _loop(self, stop_ev):
        while not stop_ev.wait(POLL_SEC):
            try:
                if not self._tick(stop_ev):
                    return
            except Exception as e:
                print(f"⚠️ timers: {type(e).__name__}: {e}")

    def _tick(self, stop_ev):
        """Один проход: снять под замком всё, чему пора, и вызвать on_fire уже без замка (иначе отмена
        или новый таймер из колбэка ждали бы озвучку). → False, когда потоку пора завершиться."""
        now = time.time()
        due = []
        with self._lock:
            if stop_ev.is_set():
                return False
            for tid, (due_wall, msg, note_late) in sorted(self._timers.items(), key=lambda kv: kv[1][0]):
                if due_wall > now:
                    continue
                del self._timers[tid]               # снят ДО колбэка: отмена во время срабатывания его не повторит
                late = now - due_wall
                if late > MAX_OVERDUE_SEC:                  # ПК спал/был выключен дольше 6 часов
                    due.append(missed_phrase(msg))
                elif note_late and late > LATE_NOTE_SEC:
                    due.append(late_note(late) + msg)
                else:
                    due.append(msg)
            changed = len(due) > 0 or not self._timers
            if changed:
                self._save()
            if not self._timers and not due:
                if self._thread is threading.current_thread():
                    self._thread = None             # таймеров нет — поток не держим; add() заведёт новый
                return False
        for message in due:
            if stop_ev.is_set():
                break
            try:
                self._on_fire(message)
            except Exception as e:
                print(f"⚠️ timer: {e}")
        return True

    def cancel_all(self):
        """Отмена по просьбе пользователя: таймеры исчезают И из файла."""
        with self._lock:
            n = len(self._timers)
            self._timers.clear()
            self._save()
        return n

    def stop(self, timeout=2.0):
        """Остановить поток при выходе, НЕ трогая сохранённый файл — таймеры поднимутся при следующем запуске."""
        with self._lock:
            self._timers.clear()
            self._stop_ev.set()
            t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    def describe(self):
        with self._lock:
            items = sorted(self._timers.values(), key=lambda x: x[0])
        now = time.time()
        return [(format_duration(max(0, due_wall - now)), msg) for due_wall, msg, _ in items]
