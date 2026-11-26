"""Чистые (без Windows API) помощники поиска окон: псевдонимы программ и выбор подходящего окна.

Зачем: на Windows с нидерландским/английским интерфейсом у «Блокнота» в заголовке не «Блокнот», а «Kladblok»/«Notepad»,
поэтому «закрой блокнот» не находила окно. Ищем по процессу (notepad.exe) и по заголовку на нескольких языках.
"""
import re

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


# «открой новое окно яндекса»: у браузеров на Chromium/Firefox есть ключ «новое окно». Имя — по началу слова (яндекса, хрома…).
_NEW_WINDOW = [
    (("яндекс", "yandex"), 'start "" browser --new-window'),
    (("хром", "chrome"), 'start "" chrome --new-window'),
    (("эдж", "едж", "edge"), 'start "" msedge --new-window'),
    (("фаерфокс", "файрфокс", "фаерфокс", "firefox", "мозил"), 'start "" firefox -new-window'),
    (("браузер",), 'start "" chrome --new-window'),            # как «браузер» в APP_DICT
]
_RE_NEW_WINDOW = re.compile(r"\bнов(?:ое|ый|ую|ого|ом|ым)\s+окн\w*\b")


def split_new_window(raw):
    """«новое окно яндекса» / «яндекс в новом окне» → (True, 'яндекс'); без «нового окна» → (False, raw)."""
    m = _RE_NEW_WINDOW.search((raw or "").lower())
    if not m:
        return False, raw
    rest = (raw[:m.start()] + " " + raw[m.end():]).lower()
    rest = re.sub(r"\b(?:в|во|для|у|на|ещё|еще)\b", " ", rest)
    return True, re.sub(r"\s+", " ", rest).strip(" .,!?")


def new_window_command(name):
    """Команда запуска нового окна браузера по имени («яндекса», «хром») или None."""
    n = (name or "").lower().strip()
    for stems, cmd in _NEW_WINDOW:
        if any(n.startswith(s) for s in stems):
            return cmd
    return None


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
