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


class TimerManager:
    """Таймеры и напоминания. С storage_path переживают перезапуск: «Рокси, пока» больше не теряет напоминания."""

    def __init__(self, on_fire, storage_path=None):
        self._on_fire = on_fire      # on_fire(message)
        self._timers = {}            # tid -> (threading.Timer, due_monotonic, due_wall, message)
        self._lock = threading.Lock()
        self._next_id = 1
        self._path = storage_path

    # ---------- хранение ----------
    def _save(self):
        """Вызывать под self._lock. Запись атомарная: сначала во временный файл, потом замена."""
        if not self._path:
            return
        try:
            data = [{"at": due_wall, "message": msg} for _, _, due_wall, msg in self._timers.values()]
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            print(f"⚠️ timers: не смогла сохранить: {e}")

    def _schedule(self, seconds, message, due_wall):
        tid = self._next_id
        self._next_id += 1
        t = threading.Timer(max(0.0, seconds), self._fire, args=(tid,))
        t.daemon = True
        self._timers[tid] = (t, time.monotonic() + seconds, due_wall, message)
        t.start()
        return tid

    def restore(self, max_overdue=6 * 3600, startup_delay=4.0):
        """Поднять сохранённые таймеры. Просроченные не больше чем на max_overdue секунд сработают сразу
        (с пометкой), совсем старые выбрасываются. Возвращает (восстановлено, просрочено_сработает)."""
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
                    self._schedule(left, msg, at)
                    pending += 1
                elif -left <= max_overdue:
                    self._schedule(startup_delay + overdue, "Пока меня не было, сработало: " + msg, now)
                    overdue += 1
            self._save()
        return pending, overdue

    # ---------- работа ----------
    def add(self, seconds, message):
        with self._lock:
            tid = self._schedule(seconds, message, time.time() + seconds)
            self._save()
            return tid

    def _fire(self, tid):
        with self._lock:
            item = self._timers.pop(tid, None)
            if item:
                self._save()
        if item:
            try:
                self._on_fire(item[3])
            except Exception as e:
                print(f"⚠️ timer: {e}")

    def cancel_all(self):
        """Отмена по просьбе пользователя: таймеры исчезают И из файла."""
        with self._lock:
            n = len(self._timers)
            for t, _, _, _ in self._timers.values():
                t.cancel()
            self._timers.clear()
            self._save()
        return n

    def stop(self):
        """Остановить потоки при выходе, НЕ трогая сохранённый файл — таймеры поднимутся при следующем запуске."""
        with self._lock:
            for t, _, _, _ in self._timers.values():
                t.cancel()
            self._timers.clear()

    def describe(self):
        with self._lock:
            items = sorted(self._timers.values(), key=lambda x: x[1])
        now = time.monotonic()
        return [(format_duration(max(0, end - now)), msg) for _, end, _, msg in items]
