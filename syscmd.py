"""Компьютер: блокировка, сон, состояние, что запущено, принудительное закрытие программ.

«заблокируй компьютер», «переведи в сон», «как дела у компа» / «загрузка процессора» / «сколько места на диске»,
«что запущено», «заверши хром» / «убей процесс телеграм».
Разбор (parse) — чистые функции с тестами; выполнение — только на Windows.
"""
import os
import re
import time

# ---------------------------------------------------------------- разбор
_RE_LOCK = re.compile(r"^\s*(?:рокси\W+)?(?:заблокируй|заблокировать|блокируй|залочь)(?:\s+(?:мне\s+)?(?:компьютер|комп|пк|экран|ноутбук|ноут|систему|винду))?\s*[.!]?\s*$")
_RE_SLEEP = re.compile(r"\b(?:переведи|отправь|усыпи|уложи)\s+(?:\w+\s+)?(?:в\s+)?(?:сон|спящ\w*(?:\s+режим)?|спать)\b|"
                       r"\bспящ\w*\s+режим\b|\bусыпи\s+(?:компьютер|комп|пк|ноутбук|ноут)\b|^\s*(?:рокси\W+)?(?:в\s+)?сон\s*[.!]?\s*$")
_RE_STATUS = re.compile(
    r"\bкак\s+(?:дела|себя\s+чувствует|поживает)\s+(?:у\s+)?(?:компа|компьютера|пк|ноута|ноутбука|системы)\b|"
    r"\bсостояни\w*\s+(?:компа|компьютера|пк|ноута|ноутбука|системы)\b|"
    r"\bзагрузк\w*\s+(?:процессора|цп|cpu|компа|компьютера|пк|системы|памяти)\b|"
    r"\bсколько\s+(?:свободной\s+)?(?:памяти|оперативки|оперативной\s+памяти|места)\b|"
    r"\b(?:свободн\w*\s+мест\w*|мест\w*\s+на\s+диск\w*)\b|"
    r"\b(?:заряд\w*|батаре\w*|аккумулятор\w*)\b(?!.*\b(?:телефон\w*|наушник\w*|мыш\w*)\b)|"
    r"\b(?:статус|нагрузк\w*)\s+(?:пк|компа|компьютера|системы)\b|^\s*(?:рокси\W+)?(?:статус|состояние)\s*[.!?]?\s*$")
_RE_PROCS = re.compile(r"\bчто\s+(?:сейчас\s+|у\s+меня\s+)?(?:запущено|открыто|работает)\b|"
                       r"\bкакие\s+(?:программы|окна|приложения)\s+(?:сейчас\s+)?(?:открыты|запущены|работают)\b|"
                       r"\bсписок\s+(?:программ|окон|приложений|процессов)\b")
_RE_KILL = re.compile(r"^\s*(?:рокси\W+)?(?:заверши|убей|закрой\s+принудительно|принудительно\s+закрой)"
                      r"\s+(?:процесс\s+|программу\s+|приложение\s+)?(.+?)\s*[.!]?\s*$")


def parse(lower):
    """→ ('lock',) | ('sleep',) | ('status',) | ('procs',) | ('kill', имя) | None"""
    s = (lower or "").strip().lower()
    if _RE_LOCK.search(s):
        return ("lock",)
    if _RE_SLEEP.search(s):
        return ("sleep",)
    if _RE_PROCS.search(s):
        return ("procs",)
    if _RE_STATUS.search(s):
        return ("status",)
    m = _RE_KILL.match(s)
    if m:
        name = m.group(1).strip()
        if name and not re.match(r"^(?:таймер|таймеры|музыку|музыка|запись|работу|разговор|все|всё)\b", name):
            return ("kill", name)
    return None


# Как зовут программы вслух → имя процесса
PROC_ALIASES = {
    "хром": "chrome.exe", "chrome": "chrome.exe", "гугл хром": "chrome.exe", "браузер": "chrome.exe",
    "эдж": "msedge.exe", "edge": "msedge.exe", "яндекс": "browser.exe", "яндекс браузер": "browser.exe",
    "файрфокс": "firefox.exe", "firefox": "firefox.exe", "опера": "opera.exe",
    "телеграм": "telegram.exe", "телеграмм": "telegram.exe", "telegram": "telegram.exe", "тг": "telegram.exe",
    "дискорд": "discord.exe", "discord": "discord.exe", "стим": "steam.exe", "steam": "steam.exe",
    "спотифай": "spotify.exe", "spotify": "spotify.exe", "пайчарм": "pycharm64.exe", "pycharm": "pycharm64.exe",
    "вс код": "code.exe", "vs code": "code.exe", "vscode": "code.exe", "ворд": "winword.exe", "word": "winword.exe",
    "эксель": "excel.exe", "excel": "excel.exe", "блокнот": "notepad.exe", "notepad": "notepad.exe",
    "обс": "obs64.exe", "obs": "obs64.exe", "зум": "zoom.exe", "zoom": "zoom.exe", "скайп": "skype.exe",
    "калькулятор": "calculatorapp.exe", "паинт": "mspaint.exe", "проигрыватель": "wmplayer.exe",
    "вотсап": "whatsapp.exe", "whatsapp": "whatsapp.exe", "эпик": "epicgameslauncher.exe",
}
# Это не закрываем никогда: без них Windows падает или выходит из учётки
PROTECTED = {"system", "system idle process", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
             "lsass.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "conhost.exe",
             "sihost.exe", "registry", "memory compression", "audiodg.exe", "ctfmon.exe", "searchhost.exe",
             "startmenuexperiencehost.exe", "shellexperiencehost.exe", "runtimebroker.exe", "lsaiso.exe"}


def resolve_proc(name):
    """«хром» → «chrome.exe»; неизвестное имя → name.exe (латиница) или None."""
    n = re.sub(r"\s+", " ", name.lower().strip(" «»\"'.,!"))
    if n in PROC_ALIASES:
        return PROC_ALIASES[n]
    if re.fullmatch(r"[a-z0-9_\-. ]+", n):
        n = n.replace(" ", "")
        return n if n.endswith(".exe") else n + ".exe"
    return None


# ---------------------------------------------------------------- выполнение (Windows)
def lock():
    import ctypes
    ok = bool(ctypes.windll.user32.LockWorkStation())
    return ("Заблокировала. Пока экран заблокирован, мышь и клавиши с телефона не работают."
            if ok else "Не получилось заблокировать.")


def sleep_later(delay=5.0):
    """Сон через несколько секунд — чтобы ответ успел уйти в Telegram. SetSuspendState(FALSE…) — именно сон,
    а не гибернация (через rundll32 Windows часто путает и уходит в гибернацию)."""
    import threading

    def go():
        time.sleep(delay)
        try:
            import ctypes
            ctypes.WinDLL("powrprof").SetSuspendState(False, False, False)
        except Exception as e:
            print(f"⚠️ Сон: {e}")
    threading.Thread(target=go, daemon=True, name="sleep").start()


def _gb(n):
    return f"{n / 1024 ** 3:.1f}".replace(".", ",")


def status():
    parts = []
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.6)
        vm = psutil.virtual_memory()
        parts.append(f"Процессор загружен на {cpu:.0f}%.")
        parts.append(f"Память: занято {_gb(vm.used)} из {_gb(vm.total)} ГБ ({vm.percent:.0f}%).")
        bat = psutil.sensors_battery()
        if bat is not None:
            plug = ", заряжается" if bat.power_plugged else ""
            parts.append(f"Батарея {bat.percent:.0f}%{plug}.")
        up = time.time() - psutil.boot_time()
        h, m = int(up // 3600), int(up % 3600 // 60)
        parts.append(f"Работает без перезагрузки {h} ч {m} мин." if h else f"Включён {m} мин назад.")
    except Exception as e:
        parts.append(f"Загрузку процессора и памяти узнать не вышло ({type(e).__name__}).")
    try:
        import shutil
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        du = shutil.disk_usage(drive)
        parts.append(f"На диске {drive[:2]} свободно {_gb(du.free)} из {_gb(du.total)} ГБ.")
    except Exception:
        pass
    return " ".join(parts)


def running_apps(limit=15):
    """Программы с видимыми окнами (а не сотни фоновых процессов): «Chrome (3 окна), Telegram, PyCharm»."""
    import pygetwindow as gw
    from winutil import _window_process_name
    counts = {}
    own = os.getpid()
    import ctypes
    from ctypes import wintypes
    for w in gw.getAllWindows():
        try:
            if not (w.title or "").strip() or not w.visible or w.width < 80 or w.height < 50:
                continue
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(w._hWnd), ctypes.byref(pid))
            if pid.value == own:
                continue
            exe = _window_process_name(w._hWnd) or "?"
            if exe in ("textinputhost.exe", "applicationframehost.exe", "shellexperiencehost.exe", "?"):
                continue
            counts[exe] = counts.get(exe, 0) + 1
        except Exception:
            continue
    if not counts:
        return "Открытых окон не вижу."
    nice = lambda name: name[:-4] if name.endswith(".exe") else name
    items = [f"{nice(e)} ({n} окна)" if 2 <= n <= 4 else f"{nice(e)} ({n} окон)" if n > 4 else nice(e)
             for e, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    more = f" и ещё {len(items) - limit}" if len(items) > limit else ""
    return "Открыто: " + ", ".join(items[:limit]) + more + "."


def find_procs(exe):
    import psutil
    own = {os.getpid(), os.getppid()}
    out = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if (p.info["name"] or "").lower() == exe and p.info["pid"] not in own:
                out.append(p)
        except Exception:
            continue
    return out


def kill(exe, procs):
    import psutil
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=3)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    gone2, alive2 = psutil.wait_procs(alive, timeout=2) if alive else ([], [])
    if alive2:
        return f"Закрыла не всё: {len(alive2)} процессов {exe} не поддались (может, нужны права администратора)."
    return f"Закрыла {exe[:-4] if exe.endswith('.exe') else exe}."
