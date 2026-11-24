"""Сетка поверх скриншота для управления мышью с телефона.

Рокси присылает снимок экрана с сеткой: столбцы — буквы (А, Б, В…), строки — цифры (1, 2, 3…).
Пишешь «Б4» / «курсор в Б4» / «кликни Б4» / «нажми правой в Б4» — курсор прыгает в центр этой клетки.
Красное кольцо на снимке — где курсор сейчас.
"""
import io
import re

COLS, ROWS = 16, 9
LETTERS = "АБВГДЕЖИКЛМНПРСТ"                           # без З и О (на снимке путаются с 3 и 0), Ё и Й
_LATIN_LOOKALIKE = {"a": "А", "b": "В", "e": "Е", "k": "К", "m": "М", "h": "Н", "p": "Р", "c": "С", "t": "Т"}
_SPOKEN = {"а": "А", "бэ": "Б", "бе": "Б", "вэ": "В", "ве": "В", "гэ": "Г", "ге": "Г", "дэ": "Д", "де": "Д",
           "е": "Е", "жэ": "Ж", "же": "Ж", "и": "И", "ка": "К", "эль": "Л", "эл": "Л",
           "эм": "М", "эн": "Н", "пэ": "П", "пе": "П", "эр": "Р", "эс": "С", "тэ": "Т", "те": "Т"}
_RE_CELL = re.compile(r"(?<![а-яёa-z0-9])([а-яёa-z]{1,3})\s*-?\s*(\d{1,2})(?![\d])")


def col_index(token):
    t = token.lower()
    letter = _SPOKEN.get(t) if len(t) > 1 else (t.upper() if t.upper() in LETTERS else _LATIN_LOOKALIKE.get(t))
    if letter and letter in LETTERS:
        return LETTERS.index(letter)
    return None


def find_cell(text):
    """→ (col, row, start, end) первой клетки в тексте (row с 0) или None. «б4», «Б-4», «бэ 4», «B4» (лат.)."""
    for m in _RE_CELL.finditer(text.lower()):
        c = col_index(m.group(1))
        r = int(m.group(2)) - 1
        if c is not None and 0 <= r < ROWS:
            return c, r, m.start(), m.end()
    return None


def cell_name(col, row):
    return f"{LETTERS[col]}{row + 1}"


def cell_center(col, row, width, height):
    return int((col + 0.5) * width / COLS), int((row + 0.5) * height / ROWS)


def cell_of(x, y, width, height):
    col = min(COLS - 1, max(0, int(x * COLS / max(1, width))))
    row = min(ROWS - 1, max(0, int(y * ROWS / max(1, height))))
    return col, row


def _font(size):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render(img, cursor=None, grid=True, max_width=1600):
    """img — PIL.Image экрана; cursor — (x, y) в пикселях img. → JPEG-байты, уменьшенные до max_width."""
    from PIL import Image, ImageDraw
    img = img.convert("RGB")
    if img.width > max_width:
        k = max_width / img.width
        img = img.resize((max_width, int(img.height * k)), Image.LANCZOS)
        if cursor:
            cursor = (cursor[0] * k, cursor[1] * k)
    w, h = img.size
    if grid:
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        for c in range(1, COLS):
            x = round(c * w / COLS)
            d.line([(x, 0), (x, h)], fill=(255, 255, 255, 120), width=1)
            d.line([(x + 1, 0), (x + 1, h)], fill=(0, 0, 0, 90), width=1)
        for r in range(1, ROWS):
            y = round(r * h / ROWS)
            d.line([(0, y), (w, y)], fill=(255, 255, 255, 120), width=1)
            d.line([(0, y + 1), (w, y + 1)], fill=(0, 0, 0, 90), width=1)
        f = _font(max(11, int(h / ROWS / 5)))
        for c in range(COLS):
            for r in range(ROWS):
                x0, y0 = c * w / COLS + 3, r * h / ROWS + 2
                label = cell_name(c, r)
                box = d.textbbox((x0, y0), label, font=f)
                d.rectangle([box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1], fill=(0, 0, 0, 125))
                d.text((x0, y0), label, font=f, fill=(255, 230, 80, 255))
        img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
    if cursor:
        d = ImageDraw.Draw(img)
        x, y = cursor
        rad = max(10, w // 90)
        for width, color in ((5, (0, 0, 0)), (3, (255, 40, 40))):
            d.ellipse([x - rad, y - rad, x + rad, y + rad], outline=color, width=width)
            d.line([(x - rad * 1.8, y), (x - rad * 0.5, y)], fill=color, width=width)
            d.line([(x + rad * 0.5, y), (x + rad * 1.8, y)], fill=color, width=width)
            d.line([(x, y - rad * 1.8), (x, y - rad * 0.5)], fill=color, width=width)
            d.line([(x, y + rad * 0.5), (x, y + rad * 1.8)], fill=color, width=width)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def screenshot(grid=True):
    """Снимок основного экрана с курсором (и сеткой). → (jpeg_bytes, подпись)."""
    from PIL import Image
    import pyautogui
    from vision import _mss_grab
    shot = _mss_grab()
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    sw, sh = pyautogui.size()                           # координаты мыши могут быть в других единицах (DPI)
    mx, my = pyautogui.position()
    kx, ky = img.width / max(1, sw), img.height / max(1, sh)
    inside = 0 <= mx < sw and 0 <= my < sh
    data = render(img, cursor=(mx * kx, my * ky) if inside else None, grid=grid)
    if inside:
        col, row = cell_of(mx, my, sw, sh)
        cap = f"Курсор в {cell_name(col, row)} ({mx}, {my})."
    else:
        cap = "Курсор на другом мониторе."
    if grid:
        cap += " Пиши, например, «Б4» или «кликни Б4»."
    return data, cap
