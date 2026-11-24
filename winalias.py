"""Чистые (без Windows API) помощники поиска окон: псевдонимы программ и выбор подходящего окна.

Зачем: на Windows с нидерландским/английским интерфейсом у «Блокнота» в заголовке не «Блокнот», а «Kladblok»/«Notepad»,
поэтому «закрой блокнот» не находила окно. Ищем по процессу (notepad.exe) и по заголовку на нескольких языках.
"""

# русское имя → (подстроки заголовка в нижнем регистре, имя процесса или None)
APP_ALIASES = {
    "блокнот": (("блокнот", "notepad", "kladblok"), "notepad.exe"),
    "калькулятор": (("калькулятор", "calculator", "rekenmachine"), "calculatorapp.exe"),
    "проводник": (("проводник", "file explorer", "verkenner"), "explorer.exe"),
    "яндекс": (("yandex", "яндекс"), "browser.exe"),
    "хром": (("chrome", "хром"), "chrome.exe"),
    "эдж": (("edge", "эдж"), "msedge.exe"),
    "фаерфокс": (("firefox", "фаерфокс"), "firefox.exe"),
    "paint": (("paint",), "mspaint.exe"),
    "пейнт": (("paint", "пейнт"), "mspaint.exe"),
}
_ALIAS_FORMS = {"блокнота": "блокнот", "блокноте": "блокнот", "яндекса": "яндекс", "хрома": "хром",
                "яндекс браузер": "яндекс", "яндекс браузера": "яндекс", "yandex": "яндекс", "chrome": "хром",
                "edge": "эдж", "notepad": "блокнот", "kladblok": "блокнот", "firefox": "фаерфокс"}
MULTI_WINDOW_EXES = {"explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "notepad.exe", "mspaint.exe",
                     "calc.exe", "calculatorapp.exe", "code.exe", "pycharm64.exe"}
BROWSER_EXES = {"chrome.exe", "msedge.exe", "browser.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe"}


def _key(title):
    t = (title or "").strip().lower().strip(" .,!?«»\"'")
    return _ALIAS_FORMS.get(t, t)


def needles(title):
    """Какие подстроки заголовка искать: само название + его переводы."""
    t = (title or "").strip().lower()
    res = [t] if t else []
    k = _key(title)
    for n in APP_ALIASES.get(k, ((), None))[0]:
        if n not in res:
            res.append(n)
    return res


def process_for(title):
    """Имя процесса для известной программы («блокнот» → notepad.exe) или None."""
    return APP_ALIASES.get(_key(title), ((), None))[1]


def pick_window(cands, exe):
    """cands — список (заголовок, имя процесса, ширина, высота, свёрнуто). Возвращает индекс подходящего окна
    программы exe: с непустым заголовком, нормального размера (свёрнутое окно маленькое — его тоже берём)."""
    for i, (title, proc, w, h, minimized) in enumerate(cands):
        if proc != exe or not (title or "").strip():
            continue
        if minimized or (w > 50 and h > 50):
            return i
    return None
