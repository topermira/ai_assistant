"""Японский и китайский текст — русскими буквами для озвучки.

Голос (Piper/RVC) не умеет читать иероглифы и кану: вместо слов звучало «japanese letter».
Перед озвучкой переводим их в русское звучание:
  * японский (есть кана, или иероглифы рядом с каной) — чтение через pykakasi, дальше по Поливанову
    (東京へ行きます → «Токё э икимасу»);
  * китайский (только иероглифы) — пиньинь через pypinyin, дальше по Палладию (你好 → «ни хао»).
Без библиотек кана всё равно читается (своя таблица), а иероглифы молча пропускаются — лучше тишина,
чем «japanese letter». Установить: pip install pykakasi pypinyin
"""
import re

_CJK = "぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ々〆〇"
_RE_RUN = re.compile(f"[{_CJK}　-〿！-／：-？～・ー]+")
_RE_KANA = re.compile("[぀-ヿㇰ-ㇿｦ-ﾟ]")
_RE_HAN = re.compile("[㐀-䶿一-鿿豈-﫿]")

_PUNCT = {"。": ". ", "、": ", ", "，": ", ", "！": "! ", "？": "? ", "：": ": ", "；": "; ", "・": " ",
          "「": " ", "」": " ", "『": " ", "』": " ", "（": " ", "）": " ", "【": " ", "】": " ", "〜": " ",
          "～": " ", "…": "… ", "　": " ", "〈": " ", "〉": " ", "《": " ", "》": " "}

# ---------- кана → ромадзи (своя таблица, если нет pykakasi) ----------
_H = ("あa いi うu えe おo かka きki くku けke こko さsa しshi すsu せse そso たta ちchi つtsu てte とto "
      "なna にni ぬnu ねne のno はha ひhi ふfu へhe ほho まma みmi むmu めme もmo やya ゆyu よyo "
      "らra りri るru れre ろro わwa をo んn がga ぎgi ぐgu げge ごgo ざza じji ずzu ぜze ぞzo "
      "だda ぢji づzu でde どdo ばba びbi ぶbu べbe ぼbo ぱpa ぴpi ぷpu ぺpe ぽpo "
      "ぁa ぃi ぅu ぇe ぉo ゃya ゅyu ょyo ゎwa ゔvu")
_KANA = {}
for item in _H.split():
    _KANA[item[0]] = item[1:]
    _KANA[chr(ord(item[0]) + 0x60)] = item[1:]          # та же буква катаканой
_KANA.update({"ヴ": "vu", "ヵ": "ka", "ヶ": "ke"})
_YOON = {"ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ャ": "ya", "ュ": "yu", "ョ": "yo"}


def _kana_romaji(s):
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if ch in "っッ":                                   # удвоение следующей согласной
            r = _KANA.get(nxt, "")
            out.append(r[0] if r and r[0] not in "aiueon" else "")
            i += 1
            continue
        if ch == "ー":                                      # долгота — повторить гласную
            if out and out[-1]:
                out.append(out[-1][-1])
            i += 1
            continue
        r = _KANA.get(ch)
        if r is None:
            out.append(ch)
            i += 1
            continue
        if nxt in _YOON and r.endswith("i") and len(r) > 1:   # きゃ → kya, しゃ → sha
            base = r[:-1]
            y = _YOON[nxt]
            out.append(base + (y[1:] if base in ("sh", "ch", "j") else y))
            i += 2
            continue
        out.append(r)
        i += 1
    return "".join(out)


# ---------- ромадзи → по-русски (Поливанов) ----------
_POL = {
    "kya": "кя", "kyu": "кю", "kyo": "кё", "sha": "ся", "shu": "сю", "sho": "сё", "she": "сэ", "shi": "си",
    "cha": "тя", "chu": "тю", "cho": "тё", "chi": "ти", "che": "тэ", "tsu": "цу", "nya": "ня", "nyu": "ню",
    "nyo": "нё", "hya": "хя", "hyu": "хю", "hyo": "хё", "mya": "мя", "myu": "мю", "myo": "мё", "rya": "ря",
    "ryu": "рю", "ryo": "рё", "gya": "гя", "gyu": "гю", "gyo": "гё", "bya": "бя", "byu": "бю", "byo": "бё",
    "pya": "пя", "pyu": "пю", "pyo": "пё", "ja": "дзя", "ju": "дзю", "jo": "дзё", "ji": "дзи", "je": "дзэ",
    "fu": "фу", "fa": "фа", "fi": "фи", "fe": "фэ", "fo": "фо", "ti": "ти", "di": "ди", "va": "ва", "vu": "ву",
    "ya": "я", "yu": "ю", "yo": "ё", "ye": "е", "wa": "ва", "wo": "о", "wi": "ви", "we": "вэ",
    "za": "дза", "zu": "дзу", "ze": "дзэ", "zo": "дзо", "zi": "дзи",
}
_CONS = {"k": "к", "s": "с", "t": "т", "n": "н", "h": "х", "m": "м", "r": "р", "g": "г", "d": "д", "b": "б",
         "p": "п", "w": "в", "f": "ф", "v": "в", "j": "дз", "z": "дз", "c": "т", "y": "й"}
_VOW = {"a": "а", "i": "и", "u": "у", "e": "э", "o": "о"}


def romaji_ru(r):
    r = r.lower()
    r = re.sub(r"ou(?=[^aiueo]|$)", "o", r)               # долгие: toukyou → tokyo → «Токё»
    r = re.sub(r"(?<=[^aiueo])uu", "u", r)
    out, i = [], 0
    while i < len(r):
        for n in (3, 2):
            seg = r[i:i + n]
            if seg in _POL:
                out.append(_POL[seg])
                i += n
                break
        else:
            ch = r[i]
            if ch in _VOW:
                # i после a/e/o/u — «й» (сэнсэй, кавай), остальное — гласные как есть (дэсу)
                out.append("й" if ch == "i" and i > 0 and r[i - 1] in "aeou" and not (i + 1 < len(r) and r[i + 1] in "aiueo") else _VOW[ch])
                i += 1
            elif ch in _CONS and i + 1 < len(r) and r[i + 1] in _VOW:
                out.append(_CONS[ch] + _VOW[r[i + 1]])
                i += 2
            elif ch == "n":
                out.append("н")
                i += 1
            elif ch in _CONS:                                # удвоенная согласная (kk, tt, tch)
                out.append(_CONS[ch])
                i += 1
            else:
                out.append(ch)
                i += 1
    return "".join(out)


# ---------- пиньинь → по-русски (Палладий) ----------
_INIT = [("zh", "чж"), ("ch", "ч"), ("sh", "ш"), ("b", "б"), ("p", "п"), ("m", "м"), ("f", "ф"), ("d", "д"),
         ("t", "т"), ("n", "н"), ("l", "л"), ("g", "г"), ("k", "к"), ("h", "х"), ("j", "цз"), ("q", "ц"),
         ("x", "с"), ("r", "ж"), ("z", "цз"), ("c", "ц"), ("s", "с")]
_FIN = {"a": "а", "o": "о", "e": "э", "ai": "ай", "ei": "эй", "ao": "ао", "ou": "оу", "an": "ань", "en": "энь",
        "ang": "ан", "eng": "эн", "ong": "ун", "er": "эр", "i": "и", "ia": "я", "ie": "е", "iao": "яо", "iu": "ю",
        "ian": "янь", "in": "инь", "iang": "ян", "ing": "ин", "iong": "юн", "u": "у", "ua": "уа", "uo": "о",
        "uai": "уай", "ui": "уй", "uan": "уань", "un": "унь", "uang": "уан", "v": "юй", "ve": "юэ",
        "van": "юань", "vn": "юнь", "ue": "юэ"}
_ZERO = {"yi": "и", "ya": "я", "ye": "е", "yao": "яо", "you": "ю", "yan": "янь", "yin": "инь", "yang": "ян",
         "ying": "ин", "yong": "юн", "yu": "юй", "yue": "юэ", "yuan": "юань", "yun": "юнь", "wu": "у", "wa": "ва",
         "wo": "во", "wai": "вай", "wei": "вэй", "wan": "вань", "wen": "вэнь", "wang": "ван", "weng": "вэн",
         "er": "эр"}


def pinyin_ru(s):
    s = s.lower().replace("ü", "v")
    if not s.isalpha():
        return s
    if s in _ZERO:
        return _ZERO[s]
    for lat, ru in _INIT:
        if s.startswith(lat):
            fin = s[len(lat):]
            if lat in ("j", "q", "x") and fin.startswith("u"):    # ju = jü
                fin = "v" + fin[1:]
            if fin == "i" and lat in ("z", "c", "s"):
                return ru + "ы"
            if fin == "e" and lat in ("zh", "ch", "sh", "r", "z", "c", "s", "g", "k", "h", "d", "t", "n", "l", "m"):
                return ru + "э"
            if fin in _FIN:
                f = _FIN[fin]
                if lat in ("zh", "ch", "sh") and f.startswith(("я", "ю", "е")):
                    return ru + f
                return ru + f
            return s
    return _FIN.get(s, s)


# ---------- библиотеки ----------
_kks = None
_pyp = None


def _kakasi():
    global _kks
    if _kks is None:
        try:
            import pykakasi
            _kks = pykakasi.kakasi()
        except Exception:
            _kks = False
    return _kks or None


def _pinyin():
    global _pyp
    if _pyp is None:
        try:
            from pypinyin import lazy_pinyin, Style
            _pyp = lambda t: lazy_pinyin(t, style=Style.NORMAL, errors="ignore")
        except Exception:
            _pyp = False
    return _pyp or None


_PARTICLE = {"は": "wa", "へ": "e", "を": "o"}


def _japanese(run):
    kk = _kakasi()
    words = []
    if kk:
        for tok in kk.convert(run):
            orig, rom = tok.get("orig", ""), tok.get("hepburn", "")
            if orig in _PUNCT or orig.strip() in _PUNCT:
                words.append(_PUNCT.get(orig.strip(), " "))
                continue
            if orig in _PARTICLE:
                rom = _PARTICLE[orig]
            rom = re.sub(r"(konnichi|konban)ha$", r"\1wa", rom)
            if rom and rom.isascii():
                words.append(romaji_ru(rom))
    else:
        # без pykakasi: кана — своей таблицей, иероглифы пропускаем
        for part in re.split(f"([{''.join(_PUNCT)}])", run):
            if part in _PUNCT:
                words.append(_PUNCT[part])
            elif part:
                kana = _RE_HAN.sub(" ", part)
                words.extend(romaji_ru(_kana_romaji(w)) for w in kana.split())
    return _join(words)


def _chinese(run):
    py = _pinyin()
    words = []
    for part in re.split(f"([{''.join(_PUNCT)}])", run):
        if part in _PUNCT:
            words.append(_PUNCT[part])
        elif part and py:
            words.extend(pinyin_ru(s) for s in py(part) if s.strip())
    return _join(words)


def _join(words):
    s = " ".join(w for w in words if w)
    s = re.sub(r"\s+([.,!?:;…])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def has_cjk(text):
    return bool(text) and bool(_RE_KANA.search(text) or _RE_HAN.search(text))


def to_russian(text):
    """Японские/китайские куски текста → русское звучание; остальное не трогаем."""
    if not has_cjk(text):
        return text
    japanese = bool(_RE_KANA.search(text))                  # есть кана хоть где-то — значит японский
    def repl(m):
        run = m.group(0)
        if not (_RE_KANA.search(run) or _RE_HAN.search(run)):
            return "".join(_PUNCT.get(c, " ") for c in run)
        ru = _japanese(run) if japanese else _chinese(run)
        if ru and ru[0].isalpha():
            ru = ru[0].upper() + ru[1:]
        return f" {ru} " if ru else " "
    out = _RE_RUN.sub(repl, text)
    return re.sub(r"\s+([.,!?])", r"\1", re.sub(r"[ \t]+", " ", out)).strip()
