"""Нажатие клавиш и сочетаний по команде (голосом и из Telegram).

«нажми Enter», «нажми эскейп», «нажми Ctrl+C», «нажми контрол шифт т», «нажми Alt Tab», «нажми F5»,
«нажми стрелку вниз 3 раза», «нажми пробел», «Ctrl+V» (сочетание можно и без «нажми»), «enter».

Разбор строгий: каждое слово после «нажми» должно быть клавишей или служебным словом. Иначе это не наша
команда («нажми на кнопку отправить» — задача для агента по экрану, «нажми правой» — клик мышью).
"""
import re

MODIFIERS = ("ctrl", "alt", "shift", "win")

# Имена клавиш → имя для pyautogui
_NAMES = {
    "enter": "enter", "энтер": "enter", "ентер": "enter", "интер": "enter", "ввод": "enter", "return": "enter",
    "esc": "esc", "escape": "esc", "эскейп": "esc", "эскейк": "esc", "искейп": "esc", "эскейв": "esc", "эск": "esc",
    "tab": "tab", "таб": "tab", "табуляция": "tab", "табуляцию": "tab",
    "space": "space", "пробел": "space", "спейс": "space",
    "backspace": "backspace", "бэкспейс": "backspace", "бекспейс": "backspace", "бэкспэйс": "backspace",
    "delete": "delete", "del": "delete", "делит": "delete", "дилит": "delete", "делете": "delete",
    "home": "home", "хоум": "home", "end": "end", "энд": "end",
    "pageup": "pageup", "pgup": "pageup", "pagedown": "pagedown", "pgdn": "pagedown",
    "insert": "insert", "инсерт": "insert", "printscreen": "printscreen", "capslock": "capslock",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "ctrl": "ctrl", "control": "ctrl", "контрол": "ctrl", "контрл": "ctrl", "ктрл": "ctrl", "кантрол": "ctrl",
    "alt": "alt", "альт": "alt", "shift": "shift", "шифт": "shift",
    "win": "win", "windows": "win", "вин": "win", "виндовс": "win", "виндоус": "win",
    # буквы, как их произносят в сочетаниях: «контрол си»
    "си": "c", "вэ": "v", "ви": "v", "икс": "x", "зэт": "z", "зет": "z", "эй": "a", "эс": "s", "эф": "f",
    "ти": "t", "эн": "n", "пи": "p", "эр": "r", "эл": "l", "эль": "l", "ди": "d",
}
# Кириллица в сочетаниях: сначала буквы, похожие на латиницу («ctrl+с»), потом — по раскладке (Ф → A)
_CYR = {"с": "c", "а": "a", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y", "к": "k", "м": "m", "т": "t",
        "ф": "a", "ы": "s", "ч": "x", "я": "z", "з": "p", "ш": "i", "д": "l", "л": "k", "п": "g", "ь": "m",
        "й": "q", "ц": "w", "щ": "o"}
_ARROW = {"вверх": "up", "вниз": "down", "влево": "left", "вправо": "right", "налево": "left", "направо": "right"}
_VERB = re.compile(r"^(?:нажми|нажимай|нажать|жми|тыкни|клавиша|клавишу|кнопка|комбинация|сочетание)$")
_FILLER = re.compile(r"^(?:на|клавиш\w*|кнопк\w*|сочетани\w*|комбинаци\w*|рокси|мне|пожалуйста|плюс|потом|затем)$")
_TIMES = re.compile(r"^раз(?:а|ок)?$")
# одной клавишей без «нажми» понимаем только такие — «enter», «ввод»; стрелки и буквы — только с «нажми»
_BARE_OK = {"enter", "esc", "tab", "space", "backspace", "delete", "home", "end", "pageup", "pagedown"}

_PHRASES = [
    (r"\bстрелк\w*\s+(вверх|вниз|влево|вправо|налево|направо)\b", lambda m: _ARROW[m.group(1)]),
    (r"\b(?:page|пейдж|пэйдж)\s*(?:up|ап)\b", "pageup"),
    (r"\b(?:page|пейдж|пэйдж)\s*(?:down|даун)\b", "pagedown"),
    (r"\b(?:print\s*screen|принт\s*скрин)\b", "printscreen"),
    (r"\b(?:caps\s*lock|капс\s*лок)\b", "capslock"),
    (r"\b(?:back\s*space)\b", "backspace"),
    (r"\b(?:эф|ф|f)\s*(1[0-2]|[1-9])\b", lambda m: "f" + m.group(1)),
    (r"\bдважды\b", "2 раза"),
    (r"\b(?:два|две)\s+раза\b", "2 раза"),
    (r"\b(?:три|четыре|пять)\s+раз\w*\b", lambda m: {"три": "3", "четыре": "4", "пять": "5"}[m.group(0).split()[0]] + " раза"),
]
MAX_TIMES = 20


def _key(tok, verb):
    if tok in _NAMES:
        return _NAMES[tok]
    if re.fullmatch(r"f(?:1[0-2]|[1-9])", tok):
        return tok
    if verb and tok in _ARROW:
        return _ARROW[tok]
    if len(tok) == 1 and tok.isascii() and tok.isalnum():
        return tok
    if len(tok) == 1 and tok in _CYR:
        return _CYR[tok]
    return None


def parse_keys(lower):
    """→ (keys, times): keys — «ctrl+c» (сочетание) или «down,down,enter» (по очереди); None — не про клавиши."""
    from pcctl import words_to_digits
    s = words_to_digits((lower or "").strip().lower().rstrip(".!?"))
    for rx, rep in _PHRASES:
        s = re.sub(rx, rep, s)
    tokens = [t for t in re.split(r"[\s+,]+", s) if t]
    if not tokens:
        return None
    verb = any(_VERB.match(t) for t in tokens)
    keys, times, expect_times = [], 1, False
    for i, t in enumerate(tokens):
        if _VERB.match(t) or _FILLER.match(t):
            continue
        if t.isdigit() and i + 1 < len(tokens) and _TIMES.match(tokens[i + 1]):
            times = max(1, min(MAX_TIMES, int(t)))
            expect_times = True
            continue
        if _TIMES.match(t) and expect_times:
            continue
        k = _key(t, verb)
        if k is None:
            if t == "и":
                continue
            return None
        keys.append(k)
    if not keys:
        return None
    mods = [k for k in keys if k in MODIFIERS]
    if mods:
        if len(keys) == len(mods) and len(mods) == 1 and not verb:
            return None                          # одно «шифт» без «нажми» — это не команда
        ordered = [m for m in MODIFIERS if m in mods] + [k for k in keys if k not in MODIFIERS]
        return "+".join(dict.fromkeys(ordered)), times
    if not verb:
        if len(keys) != 1 or keys[0] not in _BARE_OK and not re.fullmatch(r"f\d+", keys[0]):
            return None
    return ",".join(keys), times


def describe(keys):
    nice = {"enter": "Enter", "esc": "Esc", "tab": "Tab", "space": "Пробел", "backspace": "Backspace",
            "delete": "Delete", "up": "↑", "down": "↓", "left": "←", "right": "→", "ctrl": "Ctrl", "alt": "Alt",
            "shift": "Shift", "win": "Win", "pageup": "PageUp", "pagedown": "PageDown", "home": "Home", "end": "End"}
    part = lambda k: nice.get(k, k.upper())
    if "+" in keys:
        return "+".join(part(k) for k in keys.split("+"))
    return ", ".join(part(k) for k in keys.split(","))


BLOCKED = {"ctrl+alt+delete": "Ctrl+Alt+Delete Windows программам нажимать не даёт.",
           "ctrl+alt+del": "Ctrl+Alt+Delete Windows программам нажимать не даёт."}


def press(keys, times=1):
    """Нажимает. win+l — через LockWorkStation: имитацию Win+L Windows игнорирует."""
    import time
    from winutil import _press_hotkey
    if keys in BLOCKED:
        return False, BLOCKED[keys]
    for _ in range(max(1, min(MAX_TIMES, times))):
        if "+" in keys:
            if not _press_hotkey(keys):
                return False, f"Не получилось нажать {describe(keys)}."
        else:
            import pyautogui
            for k in keys.split(","):
                pyautogui.press(k)
                time.sleep(0.03)
        time.sleep(0.05)
    return True, f"Нажала {describe(keys)}" + (f" {times} раз." if times > 1 else ".")
