"""Библиотека произведений: поиск (чистый, с тестами) и воспроизведение."""
import os
import re
import json
import hashlib
import difflib

from settings import LIBRARY_DIR, LIBRARY_INDEX

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def make_work_id(title):
    """'Бородино' → 'borodino'. Раньше кириллица выкидывалась и все русские названия становились 'work'."""
    t = "".join(_TRANSLIT.get(c, c) for c in (title or "").lower())
    slug = re.sub(r"[^a-z0-9]+", "_", t).strip("_")[:40].strip("_")
    if not slug:
        slug = "work_" + hashlib.md5((title or "").encode("utf-8")).hexdigest()[:8]
    return slug


def chunk_hash(text, mode):
    return hashlib.md5(f"{mode}|{text}".encode("utf-8")).hexdigest()[:8]


_library_cache = {"mtime": 0, "works": []}


def load_library(index_path=LIBRARY_INDEX):
    global _library_cache
    try:
        if not os.path.exists(index_path):
            return []
        mtime = os.path.getmtime(index_path)
        if _library_cache["mtime"] == mtime:
            return _library_cache["works"]
        with open(index_path, "r", encoding="utf-8") as f:
            works = json.load(f).get("works", [])
        _library_cache = {"mtime": mtime, "works": works}
        return works
    except Exception as e:
        print(f"⚠️ library load: {e}")
        return []


def _normalize_query(s):
    s = (s or "").lower().strip().replace("ё", "е")
    s = re.sub(r"[«»\"'`.,!?;:()]+", " ", s)
    for verb in ("рокси", "расскажи", "прочитай", "скажи", "озвучь",
                 "зачитай", "прочти", "мне", "пожалуйста", "пжлст"):
        s = re.sub(rf"\b{verb}\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _candidates(work):
    names = [work.get("title", ""), work.get("id", "")] + list(work.get("aliases", []))
    return [n.lower().replace("ё", "е") for n in names if n]


def find_work(query, works):
    """Сначала точное совпадение, потом САМОЕ ДЛИННОЕ вхождение (чтобы «лермонтов парус»
    не находило «Бородино» по алиасу «лермонтов»), потом fuzzy."""
    if not works:
        return None
    q = _normalize_query(query)
    if not q:
        return None
    best, best_len = None, 0
    for work in works:
        for c in _candidates(work):
            if c == q:
                return work
            if len(c) >= 4 and re.search(rf"(?<!\w){re.escape(c)}(?!\w)", q) and len(c) > best_len:
                best, best_len = work, len(c)
    if best:
        return best
    pairs = [(c, w) for w in works for c in _candidates(w)]
    m = difflib.get_close_matches(q, [c for c, _ in pairs], n=1, cutoff=0.78)
    if m:
        return next(w for c, w in pairs if c == m[0])
    return None


def find_work_in_library(query):
    return find_work(query, load_library())


def play_library_work(work):
    """Играет чанки по очереди. Прерывается по Num Lock / Pause (audio.stop_playback)."""
    import audio
    if not work:
        return "Не нашла в библиотеке."
    audio_files = work.get("audio_files", [])
    title = work.get("title", "произведение")
    if not audio_files:
        return f"'{title}' есть в каталоге, но аудио не сгенерировано (запусти build_library.py)."
    print(f"📚 Библиотека: {title} ({len(audio_files)} файлов)")
    audio.reset_stop()
    for i, rel in enumerate(audio_files, 1):
        full = os.path.join(LIBRARY_DIR, rel)
        if not os.path.exists(full):
            print(f"⚠️ Нет файла: {full}")
            continue
        try:
            if not audio.play_file(full):
                return f"Остановила '{title}'."
            print(f"   ✓ {i}/{len(audio_files)}")
        except Exception as e:
            print(f"❌ Воспроизведение {full}: {e}")
    return f"Рассказала '{title}'."
