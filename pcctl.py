"""Мышь и яркость экрана по команде (голосом и из Telegram).

Мышь:   «перенеси мышку на 100 пикселей вниз», «подвинь курсор на 50 вправо и на 20 вверх»,
        «мышку в центр», «курсор в левый верхний угол», «кликни», «кликни правой», «двойной клик»,
        «прокрути вниз», «прокрути вверх на 10», «курсор в Б4» / «кликни Б4» (клетка сетки, см. screengrid),
        «зажми левую кнопку» … «отпусти» (перетаскивание).
Яркость: «яркость 50», «сделай ярче», «темнее на 20», «какая яркость».
Яркость: через screen_brightness_control (внешние мониторы по DDC/CI и ноутбуки), без него — WMI (только экран
ноутбука). Внешний монитор без DDC/CI яркость программно не меняет — так и скажем.
"""
import re
import subprocess

# ---------------------------------------------------------------- мышь
_MOUSE = r"(?:мыш\w*|курсор\w*|указател\w*)"
_MOVE_VERB = r"(?:перенеси|перемести|переместь|подвинь|сдвинь|передвинь|двинь|переведи|отведи|убери|поставь|наведи|опусти|подними)"
_RE_MOVE = re.compile(rf"\b{_MOVE_VERB}\s+(?:мне\s+)?{_MOUSE}|\b{_MOUSE}\s+{_MOVE_VERB}|\b{_MOUSE}\s+(?:в|на)\s+(?:центр|середин|угол|лев|прав|верх|низ)"
                      rf"|\b{_MOUSE}\s+(?:на\s+\d+\s*(?:пикс\w*|px|точ\w*)?\s+)?(?:вниз|ниже|вверх|выше|влево|налево|левее|вправо|направо|правее)\b")
_DIRS = {"вниз": (0, 1), "ниже": (0, 1), "вверх": (0, -1), "выше": (0, -1), "влево": (-1, 0), "налево": (-1, 0),
         "левее": (-1, 0), "вправо": (1, 0), "направо": (1, 0), "правее": (1, 0)}
_RE_STEP = re.compile(r"(?:на\s+(\d+)\s*(?:пикс\w*|px|точ\w*)?\s+)?(вниз|ниже|вверх|выше|влево|налево|левее|вправо|направо|правее)"
                      r"(?:\s+на\s+(\d+)\s*(?:пикс\w*|px|точ\w*)?)?")
# Клик: фраза целиком из «служебных» слов — глагол, кнопка, «мыши», «два раза», «клик».
# Если есть что-то ещё («нажми на кнопку отправить») — это не простой клик, а задача для агента по экрану.
_CLICK_VERB = re.compile(r"^(?:кликни|кликнуть|клацни|щёлкни|щелкни|щёлкнуть|щелкнуть|нажми|нажать|жми|тыкни|ткни|сделай|клик\w*|щелч\w*|щелчок)$")
_CLICK_BTN = [(re.compile(r"^(?:прав\w*|пкм)$"), "right"), (re.compile(r"^(?:лев\w*|лкм)$"), "left"),
              (re.compile(r"^(?:средн\w*|скм|колесик\w*|колёсик\w*)$"), "middle")]
_CLICK_FILLER = re.compile(r"^(?:на|кнопк\w*|мыш\w*|мышк\w*|один|раз|раза|разок|пожалуйста|рокси|по|ей|ею|им)$")
_CLICK_DOUBLE = re.compile(r"^(?:два|дважды|двойн\w*|дабл|2)$")


def _parse_click(lower):
    words = re.findall(r"[a-zа-яё0-9]+", lower)
    if not words:
        return None
    btn, n, hit, verb = None, 1, False, False
    for w in words:
        b = next((name for rx, name in _CLICK_BTN if rx.match(w)), None)
        if b:
            btn = b
            hit = hit or w in ("пкм", "лкм", "скм")
        elif _CLICK_VERB.match(w):
            verb = True
            hit = hit or w.startswith(("клик", "клац", "щёлк", "щелк", "щелч"))
        elif _CLICK_DOUBLE.match(w):
            n = 2
        elif _CLICK_FILLER.match(w):
            hit = hit or w.startswith("мыш")
        else:
            return None                               # посторонние слова — это не просто клик
    hit = hit or (btn is not None and verb)          # «нажми правой» — да; просто «левой» — нет
    if not hit:
        return None                                   # «нажми» без кнопки/мыши/клика — не наше
    return "click", btn or "left", n


_RE_SCROLL = re.compile(r"\b(?:прокрути|пролистай|промотай|листни|скролл\w*)\s*(?:страницу\s*|мышкой\s*)?(вниз|вверх)(?:\s+на\s+(\d+))?")
DEFAULT_STEP = 100

# Голос (Vosk) часто отдаёт числа словами: «на триста пикселей» → «на 300 пикселей»
_UNITS_W = {"ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
            "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
            "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
            "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
            "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
            "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600,
            "семьсот": 700, "восемьсот": 800, "девятьсот": 900}
_THOUSAND = re.compile(r"^тысяч\w*$")


def words_to_digits(lower):
    """«на триста пятьдесят пикселей» → «на 350 пикселей». Одиночные «один/два/раз» не трогаем,
    если рядом нет других числительных: «два раза» должно остаться словами для двойного клика."""
    out, run = [], []

    def flush():
        if len(run) == 1 and run[0] in ("один", "одну", "одна", "два", "две"):
            out.append(run[0])
        elif run:
            total, cur = 0, 0
            for w in run:
                if _THOUSAND.match(w):
                    total, cur = total + (cur or 1) * 1000, 0
                else:
                    cur += _UNITS_W[w]
            out.append(str(total + cur))
        run.clear()

    for w in lower.split():
        if w in _UNITS_W or _THOUSAND.match(w):
            run.append(w)
        else:
            flush()
            out.append(w)
    flush()
    return " ".join(out)


_BARE_MOVE_VERB = re.compile(r"^(?:перенеси|перемести|подвинь|сдвинь|передвинь|двинь|сместь|смести|отведи|опусти|подними|веди)$")
_BARE_OK = re.compile(r"^(?:на|и|ещё|еще|рокси|мне|её|ее|его|\d+|пикс\w*|px|точ\w*|мыш\w*|курсор\w*|указател\w*|"
                      r"вниз|ниже|вверх|выше|влево|налево|левее|вправо|направо|правее)$")


_NODIR_VERB = re.compile(r"^(?:перенеси|перемести|подвинь|сдвинь|передвинь|двинь|сместь|смести)$")


def _bare_nodir(text):
    """«сдвинь на 100» — шаг есть, направления нет. Раньше это уходило в LLM-роутер и превращалось в «громкость 100»."""
    words = re.findall(r"[a-zа-яё0-9]+", text)
    if not any(_NODIR_VERB.match(w) for w in words) or not any(w.isdigit() for w in words):
        return False
    if any(w in _DIRS for w in words):
        return False
    return all(_NODIR_VERB.match(w) or _BARE_OK.match(w) for w in words)


def _bare_move(text):
    """«передвинь на 300 пикселей вниз» — без слова «мышку». Только если во фразе нет ничего постороннего
    (иначе «сдвинь окно влево» стало бы движением курсора)."""
    words = re.findall(r"[a-zа-яё0-9]+", text)
    if not words or not any(_BARE_MOVE_VERB.match(w) for w in words):
        return False
    if not any(w in _DIRS for w in words):
        return False
    return all(_BARE_MOVE_VERB.match(w) or _BARE_OK.match(w) for w in words)


_HOLD_VERB = re.compile(r"^(?:зажми|зажать|удерживай|держи|придержи|зажимай)$")
_RELEASE_VERB = re.compile(r"^(?:отпусти|отпустить|отпускай|отожми|разожми)$")


def _parse_hold(lower):
    """«зажми левую кнопку мыши» → ('down', 'left'); «отпусти» / «отпусти мышь» → ('up', 'all'|btn)."""
    words = re.findall(r"[a-zа-яё0-9]+", lower)
    if not words:
        return None
    kind, btn = None, None
    for w in words:
        b = next((name for rx, name in _CLICK_BTN if rx.match(w)), None)
        if b:
            btn = b
        elif _HOLD_VERB.match(w):
            kind = "down"
        elif _RELEASE_VERB.match(w):
            kind = "up"
        elif not _CLICK_FILLER.match(w):
            return None
    if kind == "down":
        return "down", btn or "left"
    if kind == "up":
        return "up", btn or "all"
    return None


_CELL_GOTO_OK = re.compile(r"^(?:курсор\w*|мыш\w*|мышк\w*|указател\w*|наведи|перемести|переведи|поставь|перенеси|передвинь|"
                           r"подвинь|сдвинь|двинь|веди|в|во|на|по|к|клетк\w*|ячейк\w*|рокси|туда|иди)$")


def _parse_cell(text):
    """«Б4», «курсор в Б4» → ('cell', col, row, '', 0); «кликни Б4», «нажми правой в Б4» → с кликом."""
    import screengrid
    fc = screengrid.find_cell(text)
    if not fc:
        return None
    col, row, a, b = fc
    rest = (text[:a] + " " + text[b:]).strip()
    words = re.findall(r"[a-zа-яё0-9]+", rest)
    if all(_CELL_GOTO_OK.match(w) for w in words):
        return "cell", col, row, "", 0
    words = [w for w in words if w not in ("в", "во", "по", "к", "клетке", "клетку", "ячейке", "ячейку")]
    c = _parse_click(" ".join(words))
    if c:
        return "cell", col, row, c[1], c[2]
    return None


def parse_mouse(lower):
    """→ ('move', dx, dy) | ('goto', 'center'|'tl'|'tr'|'bl'|'br') | ('click', button, n) | ('scroll', clicks)
    | ('cell', col, row, button|'', n) | ('down', button) | ('up', button|'all') | None"""
    m = _RE_SCROLL.search(lower)
    if m:
        n = int(m.group(2) or 5)
        return "scroll", (-n if m.group(1) == "вниз" else n)
    h = _parse_hold(lower)
    if h:
        return h
    c = _parse_click(lower)
    if c:
        return c
    c = _parse_cell(words_to_digits(lower))
    if c:
        return c
    lower = words_to_digits(lower)
    if _bare_nodir(lower):
        return ("ask",)
    if not _RE_MOVE.search(lower) and not _bare_move(lower):
        return None
    if re.search(r"\b(?:в\s+)?(?:центр|середин)", lower):
        return "goto", "center"
    corner = re.search(r"(лев|прав)\w*\s+(верх|ниж)\w*\s+угол|(верх|ниж)\w*\s+(лев|прав)\w*\s+угол", lower)
    if corner:
        lr = corner.group(1) or corner.group(4)
        tb = corner.group(2) or corner.group(3)
        return "goto", ("t" if tb.startswith("верх") else "b") + ("l" if lr == "лев" else "r")
    dx = dy = 0
    found = False
    for n1, d, n2 in _RE_STEP.findall(lower):
        n = int(n1 or n2 or DEFAULT_STEP)
        ux, uy = _DIRS[d]
        dx += ux * n
        dy += uy * n
        found = True
    return ("move", dx, dy) if found else None


_HELD = set()
AUTO_RELEASE_SEC = 120
_release_timer = [None]


def release(btn="all"):
    import pyautogui
    for b in (("left", "right", "middle") if btn == "all" else (btn,)):
        try:
            pyautogui.mouseUp(button=b)
        except Exception:
            pass
        _HELD.discard(b)
    t = _release_timer[0]
    if t and not _HELD:
        t.cancel()
        _release_timer[0] = None


def _arm_auto_release():
    """Забытая зажатая кнопка ломает всю работу мышью — отпускаем сами через 2 минуты."""
    import threading
    t = _release_timer[0]
    if t:
        t.cancel()
    t = threading.Timer(AUTO_RELEASE_SEC, lambda: _HELD and (print("🖱 Отпускаю зажатую кнопку (таймаут)."), release()))
    t.daemon = True
    t.start()
    _release_timer[0] = t


def mouse(task):
    import pyautogui
    prev_failsafe = pyautogui.FAILSAFE
    pyautogui.FAILSAFE = False                  # угол экрана — законная цель, а не аварийный стоп
    try:
        kind = task[0]
        if kind == "ask":
            return "Куда сдвинуть курсор: вверх, вниз, влево или вправо? Скажи, например: «сдвинь на 100 вниз»."
        if kind == "move":
            _, dx, dy = task
            x, y = pyautogui.position()
            w, h = pyautogui.size()
            nx, ny = min(max(0, x + dx), w - 1), min(max(0, y + dy), h - 1)
            pyautogui.moveTo(nx, ny, duration=0.15)
            return f"Подвинула курсор: теперь {nx}, {ny}."
        if kind == "goto":
            w, h = pyautogui.size()
            pos = {"center": (w // 2, h // 2), "tl": (5, 5), "tr": (w - 6, 5), "bl": (5, h - 6), "br": (w - 6, h - 6)}[task[1]]
            pyautogui.moveTo(*pos, duration=0.15)
            return "Курсор в центре экрана." if task[1] == "center" else "Курсор в углу."
        if kind == "click":
            _, btn, n = task
            pyautogui.click(button=btn, clicks=n, interval=0.08)
            name = {"left": "левой", "right": "правой", "middle": "средней"}[btn]
            return f"Нажала {name} кнопкой мыши" + (" два раза." if n == 2 else ".")
        if kind == "scroll":
            pyautogui.scroll(int(task[1]) * 120)
            return "Прокрутила вниз." if task[1] < 0 else "Прокрутила вверх."
        if kind == "cell":
            import screengrid
            _, col, row, btn, n = task
            w, h = pyautogui.size()
            x, y = screengrid.cell_center(col, row, w, h)
            pyautogui.moveTo(x, y, duration=0.15)
            name = screengrid.cell_name(col, row)
            if not btn:
                return f"Курсор в {name}."
            pyautogui.click(button=btn, clicks=n, interval=0.08)
            bname = {"left": "левой", "right": "правой", "middle": "средней"}[btn]
            return f"Нажала {bname} кнопкой в {name}" + (" два раза." if n == 2 else ".")
        if kind == "down":
            pyautogui.mouseDown(button=task[1])
            _HELD.add(task[1])
            _arm_auto_release()
            bname = {"left": "левую", "right": "правую", "middle": "среднюю"}[task[1]]
            return (f"Зажала {bname} кнопку. Теперь двигай курсор — потащится. Скажи «отпусти», "
                    f"иначе отпущу сама через {AUTO_RELEASE_SEC // 60} минуты.")
        if kind == "up":
            release(task[1])
            return "Отпустила."
    finally:
        pyautogui.FAILSAFE = prev_failsafe
    return "Не поняла, что сделать с мышкой."


# ---------------------------------------------------------------- яркость
_RE_BRIGHT = re.compile(r"\bяркост\w*|\b(?:сделай|сделать|экран)\s+(?:\w+\s+)?(?:ярче|темнее|тусклее|светлее)\b|"
                        r"^\s*(?:рокси\W+)?(?:ярче|темнее|тусклее|светлее)\b")


def parse_brightness(lower):
    """→ ('set', n) | ('up', n) | ('down', n) | ('query', 0) | None"""
    if not _RE_BRIGHT.search(lower):
        return None
    lower = words_to_digits(lower)
    if re.search(r"\b(?:какая|сколько|скажи|текущ)\w*", lower) and not re.search(r"\d", lower):
        return "query", 0
    up = re.search(r"\b(?:ярче|светлее|прибавь|добавь|увеличь|повысь|подними|больше)\b", lower)
    down = re.search(r"\b(?:темнее|тусклее|убавь|уменьши|понизь|снизь|опусти|меньше)\b", lower)
    if re.search(r"\b(?:максимум|максимальн\w*|на\s+полную|100\s*%?)\b", lower) and not (up or down):
        return "set", 100
    if re.search(r"\bминимум|минимальн\w*", lower):
        return "set", 0
    num = re.search(r"(\d{1,3})\s*%?", lower)
    n = int(num.group(1)) if num else None
    to = re.search(r"\bдо\s+(\d{1,3})", lower)       # «прибавь яркость до 70» — это установка
    if to:
        return "set", min(100, int(to.group(1)))
    if up or down:
        return ("up" if up else "down"), min(100, n or 15)
    if n is not None:
        return "set", min(100, n)
    return "query", 0


def _sbc():
    try:
        import screen_brightness_control as sbc
        return sbc
    except Exception:
        return None


def _wmi_get():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"],
                       capture_output=True, text=True, timeout=15)
    v = (r.stdout or "").strip().splitlines()
    return int(v[0]) if v and v[0].strip().isdigit() else None


def _wmi_set(n):
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
                        f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={int(n)}}}"],
                       capture_output=True, text=True, timeout=15)
    return r.returncode == 0 and "error" not in (r.stderr or "").lower()


def get_brightness():
    sbc = _sbc()
    if sbc:
        try:
            v = sbc.get_brightness()
            return int(v[0] if isinstance(v, list) else v)
        except Exception:
            pass
    try:
        return _wmi_get()
    except Exception:
        return None


def set_brightness(n):
    n = max(0, min(100, int(n)))
    sbc = _sbc()
    if sbc:
        try:
            sbc.set_brightness(n)
            return True
        except Exception:
            pass
    try:
        return _wmi_set(n)
    except Exception:
        return False


_NO = ("Не получается менять яркость: экран не даёт её программно (у внешнего монитора должен быть включён "
       "DDC/CI в его меню). Поставь пакет: python -m pip install screen-brightness-control")


def brightness(task):
    kind, n = task
    cur = get_brightness()
    if kind == "query":
        return f"Яркость {cur}%." if cur is not None else _NO
    if kind == "set":
        target = n
    else:
        if cur is None:
            return _NO
        target = cur + n if kind == "up" else cur - n
    target = max(0, min(100, target))
    if not set_brightness(target):
        return _NO
    return f"Яркость {target}%."
