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


# Подклетки: клетка делится на 3×3, цифры как на Num-клавиатуре (7 8 9 / 4 5 6 / 1 2 3). «Б4.5» — центр Б4,
# «Б4.7.3» — ещё глубже (подклетка подклетки).
_RE_CELL_PATH = re.compile(r"(?<![а-яёa-z0-9])([а-яёa-z]{1,3})\s*-?\s*(\d{1,2})((?:[.,]\s?[1-9])+)(?![\d])")


def find_cell_path(text):
    """→ (col, row, subs, start, end): subs — строка цифр «5» / «73» или '' (только клетка); None — клетки нет.
    «Б4.5», «б 4.7.3», «Б4» (без подклетки тоже подходит)."""
    for m in _RE_CELL_PATH.finditer(text.lower()):
        c = col_index(m.group(1))
        r = int(m.group(2)) - 1
        if c is not None and 0 <= r < ROWS:
            return c, r, re.sub(r"\D", "", m.group(3)), m.start(), m.end()
    fc = find_cell(text)
    return (fc[0], fc[1], "", fc[2], fc[3]) if fc else None


def region(col, row, subs=""):
    """Доли экрана (x0, y0, x1, y1) для клетки и цепочки подклеток."""
    x0, y0, x1, y1 = col / COLS, row / ROWS, (col + 1) / COLS, (row + 1) / ROWS
    for ch in subs:
        d = int(ch)
        sc, sr = (d - 1) % 3, 2 - (d - 1) // 3
        w, h = (x1 - x0) / 3, (y1 - y0) / 3
        x0, y0 = x0 + sc * w, y0 + sr * h
        x1, y1 = x0 + w, y0 + h
    return x0, y0, x1, y1


def region_name(col, row, subs=""):
    return cell_name(col, row) + "".join("." + ch for ch in subs)


def region_center(col, row, subs, width, height):
    x0, y0, x1, y1 = region(col, row, subs)
    return int((x0 + x1) / 2 * width + 1e-6), int((y0 + y1) / 2 * height + 1e-6)


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


def render_zoom(img, box, cursor=None, target_w=1280):
    """Увеличенный кусок экрана (box — доли x0,y0,x1,y1) с сеткой 3×3 и цифрами 1–9. cursor — (x, y) в пикселях img.
    → JPEG-байты."""
    from PIL import Image, ImageDraw
    img = img.convert("RGB")
    x0, y0, x1, y1 = (int(box[0] * img.width), int(box[1] * img.height), int(box[2] * img.width), int(box[3] * img.height))
    x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
    crop = img.crop((x0, y0, x1, y1))
    k = max(1.0, target_w / crop.width)
    k = min(k, 1600 / crop.width) if crop.width * k > 1600 else k
    big = crop.resize((max(1, int(crop.width * k)), max(1, int(crop.height * k))), Image.LANCZOS)
    w, h = big.size
    over = Image.new("RGBA", big.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for i in (1, 2):
        d.line([(round(i * w / 3), 0), (round(i * w / 3), h)], fill=(255, 255, 255, 170), width=2)
        d.line([(0, round(i * h / 3)), (w, round(i * h / 3))], fill=(255, 255, 255, 170), width=2)
        d.line([(round(i * w / 3) + 2, 0), (round(i * w / 3) + 2, h)], fill=(0, 0, 0, 110), width=1)
        d.line([(0, round(i * h / 3) + 2), (w, round(i * h / 3) + 2)], fill=(0, 0, 0, 110), width=1)
    f = _font(max(18, h // 14))
    for dgt in range(1, 10):                    # цифра — в углу подклетки, чтобы не закрывать то, что внутри
        sc, sr = (dgt - 1) % 3, 2 - (dgt - 1) // 3
        cx, cy = sc * w / 3 + 8, sr * h / 3 + 6
        box_t = d.textbbox((cx, cy), str(dgt), font=f, anchor="la")
        d.rectangle([box_t[0] - 4, box_t[1] - 2, box_t[2] + 4, box_t[3] + 2], fill=(0, 0, 0, 130))
        d.text((cx, cy), str(dgt), font=f, fill=(255, 230, 80, 255), anchor="la")
    big = Image.alpha_composite(big.convert("RGBA"), over).convert("RGB")
    if cursor and x0 <= cursor[0] < x1 and y0 <= cursor[1] < y1:
        dd = ImageDraw.Draw(big)
        px, py = (cursor[0] - x0) * k, (cursor[1] - y0) * k
        rad = max(10, w // 60)
        for width, color in ((5, (0, 0, 0)), (3, (255, 40, 40))):
            dd.ellipse([px - rad, py - rad, px + rad, py + rad], outline=color, width=width)
    buf = io.BytesIO()
    big.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def zoom_screenshot(col, row, subs=""):
    """Снимок экрана в полном разрешении, приближенная клетка/подклетка. → (jpeg_bytes, подпись)."""
    from PIL import Image
    import pyautogui
    from vision import _mss_grab
    shot = _mss_grab()
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    sw, sh = pyautogui.size()
    mx, my = pyautogui.position()
    kx, ky = img.width / max(1, sw), img.height / max(1, sh)
    cur = (mx * kx, my * ky) if 0 <= mx < sw and 0 <= my < sh else None
    name = region_name(col, row, subs)
    data = render_zoom(img, region(col, row, subs), cursor=cur)
    cap = (f"{name}, приближено. Клетка поделена на 9 частей (цифры как на Num-клавиатуре): "
           f"«кликни {name}.5», «курсор в {name}.7» или ещё ближе — «увеличь {name}.5».")
    return data, cap


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
