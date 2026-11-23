"""Подготовка текста к озвучке. Чистые функции без зависимостей — покрыты тестами."""
import re

# Единицы измерения — применяются ДО правила со слешем.
UNIT_RULES = [
    (r"P\.S\.", "пэ эс"),
    (r"км\s*/\s*ч(?:ас)?\b", "километров в час"),
    (r"км\s*/\s*с\b", "километров в секунду"),
    (r"м\s*/\s*с\b", "метров в секунду"),
    (r"м\s*/\s*ч\b", "метров в час"),
    (r"km\s*/\s*h\b", "километров в час"),
    (r"m\s*/\s*s\b", "метров в секунду"),
    (r"(?<=\d)\s*км/ч\b", " километров в час"),
]

WORD_RULES = [
    (r"\bsin\b", " синус "),
    (r"\bcos\b", " косинус "),
    (r"\btg\b", " тангенс "),
    (r"\bctg\b", " котангенс "),
    (r"\bч\b", "час"),
]

# Совместимость со старым именем
PRONUNCIATION_DICT = dict(UNIT_RULES + WORD_RULES)

EMOTIONS = ("neutral", "happy", "sad", "angry", "surprised", "shy", "smug")
_EMOTION_TAG = re.compile(r"\[\s*(" + "|".join(EMOTIONS) + r")\s*\]", re.IGNORECASE)


def extract_emotion(text):
    """'[happy] Привет!' -> ('happy', 'Привет!'). Тег может стоять где угодно, берётся первый."""
    if not text:
        return None, text or ""
    m = _EMOTION_TAG.search(text)
    emotion = m.group(1).lower() if m else None
    clean = _EMOTION_TAG.sub("", text)
    return emotion, re.sub(r"[ \t]{2,}", " ", clean).strip()


def _strip_markdown(text):
    text = re.sub(r"```.*?```", " Код вывела в консоль. ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*|__|~~", "", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    return text


def preprocess_text_for_tts(text):
    if not text or not isinstance(text, str):
        return ""
    _, text = extract_emotion(text)
    text = _strip_markdown(text)

    # 1. Даты 19-11-2026 / 19/11/2026 → 19.11.2026 (чтобы правила ниже их не трогали)
    text = re.sub(r"(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})(?!\d)", r"\1.\2.\3", text)

    # 2. Диапазоны годов: 1812-1815 → "с 1812 по 1815"
    def _year_range(m):
        a, b = m.group(1), m.group(2)
        if 1000 <= int(a) <= 2999 and 1000 <= int(b) <= 2999:
            return f"с {a} по {b}"
        return m.group(0)
    text = re.sub(r"(?<![\d.])(\d{4})\s*[-–—]\s*(\d{4})(?![\d.])", _year_range, text)

    # 3. Числовые диапазоны: "2-3 дня", "10 – 15 минут" → "от 2 до 3".
    #    Дефис без пробелов или тире — диапазон; " - " с пробелами между числами — минус (ниже).
    text = re.sub(r"(?<![\d.,\w-])(\d+)(?:-|\s*[–—]\s*)(\d+)(?![\d.,-])", r"от \1 до \2", text)

    # 4. Знак минуса
    text = re.sub(r"(?<=[\dA-Za-z)])\s+-\s+(?=[\dA-Za-z(])", " минус ", text)     # x - 5, 5 - 3
    text = re.sub(r"(?:^|(?<=[\s(=]))[-−](?=\d)", "минус ", text)                 # -7°C
    #    Дефис внутри слов (что-то, 5-й, COVID-19) не трогаем.

    # 5. Плюс
    text = re.sub(r"(?<=[\dA-Za-z)])\s*\+\s*(?=[\dA-Za-z(])", " плюс ", text)
    text = text.replace("+", " , ")

    # 6. Единицы, потом слеш
    for pattern, repl in UNIT_RULES:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", " на ", text)                         # 50/50
    text = re.sub(r"(?<![:/\w.])/(?![:/])|(?<=\s)/(?=\s)", " или ", text)        # да/нет (но не URL)
    text = re.sub(r"(?<=[А-Яа-яЁё])/(?=[А-Яа-яЁё])", " или ", text)

    # 7. Тире между словами, решётка, обратный слеш → паузы
    text = re.sub(r"\s[-–—]\s", " , ", text)
    text = text.replace("#", " , ").replace("\\", " , ").replace("*", " ")

    # 8. Слова
    for pattern, repl in WORD_RULES:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    text = re.sub(r"\s+,", ",", text)
    return re.sub(r"\s+", " ", text).strip()


# ==================== РАЗБИВКА ====================
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text):
    return [s for s in (p.strip() for p in _SENT_SPLIT.split(text or "")) if s]


def split_for_pipeline(text, first_max=160, fine=False, group_max=220):
    """Первое предложение отдельно (чтобы начать говорить быстрее), остальное:
    * fine=False — одним куском (для rvc-next.exe: каждый кусок = перезагрузка модели);
    * fine=True  — группами предложений до group_max символов (RVC-воркер, edge-tts):
      пока звучит одна группа, синтезируется следующая."""
    sents = split_sentences(text)
    if len(sents) <= 1:
        return [text.strip()] if text and text.strip() else []
    first = sents[0]
    if len(first) > first_max:
        return [text.strip()] if not fine else _group(sents, group_max)
    rest = sents[1:]
    if not fine:
        return [first, " ".join(rest).strip()]
    return [first] + _group(rest, group_max)


def _group(sents, limit):
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def split_into_chunks(text, max_len=300):
    """Режет текст на куски ~300 символов по границам предложений/запятых."""
    text = (text or "").strip()
    if not text:
        return []
    chunks, cur = [], ""
    for s in _SENT_SPLIT.split(text):
        if len(s) > max_len:
            if cur:
                chunks.append(cur)
                cur = ""
            buf = ""
            for p in re.split(r"(?<=[,;:])\s+", s):
                if len(buf) + len(p) + 1 <= max_len:
                    buf = (buf + " " + p).strip() if buf else p
                else:
                    if buf:
                        chunks.append(buf)
                    buf = p
            if buf:
                chunks.append(buf)
        elif len(cur) + len(s) + 1 <= max_len:
            cur = (cur + " " + s).strip() if cur else s
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks
