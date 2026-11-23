"""Клавиши, которые работают при ЛЮБОЙ раскладке.

Проблема: pyautogui при импорте берёт коды клавиш из текущей раскладки (VkKeyScanA). В русской раскладке
латинских букв нет — VkKeyScanA возвращает -1, и pyautogui.hotkey("ctrl", "v") жмёт Ctrl+Alt+Shift+«клавишу 255»
вместо Ctrl+V. Отсюда «напечатала, но текста нет — нажми Ctrl+V» и неработающие Ctrl+C/N/W/A, Win+D.

Решение:
  * fix_pyautogui() — буквам и цифрам ставим настоящие виртуальные коды (VK_A…VK_Z, VK_0…VK_9). Программы
    понимают горячие клавиши по этим кодам, поэтому Ctrl+V работает и в русской, и в английской раскладке.
    Знакам препинания, которых нет в текущей раскладке, — коды американской раскладки.
  * type_unicode(text) — набор ТЕКСТА символами Юникода (SendInput + KEYEVENTF_UNICODE): буквы приходят
    в программу как есть, раскладка не важна, буфер обмена не трогается.
"""
import os
import ctypes

_US = {
    " ": 0x20, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "!": 0x131, "@": 0x132, "#": 0x133, "$": 0x134, "%": 0x135, "^": 0x136, "&": 0x137, "*": 0x138,
    "(": 0x139, ")": 0x130, "_": 0x1BD, "+": 0x1BB, "{": 0x1DB, "}": 0x1DD, "|": 0x1DC, ":": 0x1BA,
    '"': 0x1DE, "<": 0x1BC, ">": 0x1BE, "?": 0x1BF, "~": 0x1C0,
}


def layout_free_mapping(mapping):
    """Правит словарь «символ → (модификаторы << 8) | VK» на месте. Отдельно от pyautogui — чтобы проверять тестами."""
    for i in range(26):
        vk = 0x41 + i
        mapping[chr(0x61 + i)] = vk                 # a..z
        mapping[chr(0x41 + i)] = 0x100 | vk         # A..Z = Shift + буква
    for i in range(10):
        mapping[str(i)] = 0x30 + i
    for ch, code in _US.items():
        cur = mapping.get(ch)
        if cur is None or cur < 0 or cur == 0xFFFF:  # символа нет в текущей раскладке
            mapping[ch] = code
    return mapping


_fixed = False


def fix_pyautogui():
    global _fixed
    if _fixed or os.name != "nt":
        return
    try:
        import pyautogui._pyautogui_win as pw
        layout_free_mapping(pw.keyboardMapping)
        _fixed = True
    except Exception as e:
        print(f"⚠️ kbfix: {e}")


# ==================== набор текста символами Юникода ====================
if os.name == "nt":
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _MI(ctypes.Structure):      # нужна только чтобы union был правильного размера
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", _KI), ("mi", _MI)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _SendInput = ctypes.windll.user32.SendInput
    _SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)


def _units(text):
    """Код-юниты UTF-16 (эмодзи — двумя половинками). \\n и \\t — настоящими клавишами Enter/Tab."""
    out = []
    for ch in str(text).replace("\r\n", "\n"):
        if ch == "\n":
            out.append(("vk", 0x0D))
        elif ch == "\t":
            out.append(("vk", 0x09))
        else:
            b = ch.encode("utf-16-le")
            for k in range(0, len(b), 2):
                out.append(("u", b[k] | (b[k + 1] << 8)))
    return out


def type_unicode(text, interval=0.0):
    """Печатает текст в активное окно. True — отправлено."""
    if os.name != "nt" or not text:
        return False
    import time
    KEYUP, UNICODE = 0x0002, 0x0004
    for kind, code in _units(text):
        arr = (_INPUT * 2)()
        for n, up in enumerate((0, KEYUP)):
            arr[n].type = 1                                   # INPUT_KEYBOARD
            if kind == "vk":
                arr[n].u.ki = _KI(code, 0, up, 0, 0)
            else:
                arr[n].u.ki = _KI(0, code, UNICODE | up, 0, 0)
        if _SendInput(2, arr, ctypes.sizeof(_INPUT)) != 2:
            return False
        if interval:
            time.sleep(interval)
    return True


fix_pyautogui()
