"""Безопасность путей и имён: чистые функции без Windows-зависимостей (их легко тестировать)."""
import os
import re

_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_filename(name):
    """Имя файла/папки из голоса: без путей, '..', запрещённых символов Windows и зарезервированных имён."""
    name = _BAD_NAME_CHARS.sub("", str(name or "")).strip(" .")
    if name in ("", ".", ".."):
        return ""
    if name.split(".")[0].lower() in _RESERVED_NAMES:
        return ""
    return name[:120]


# Программы и скрипты: найденные по ПОХОЖЕМУ имени, без вопроса не запускаются («открой документ» ≠ «документ.exe»)
EXECUTABLE_EXTS = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr", ".lnk"}
# Сами программы и скрипты (без ярлыков): «документ» → «документ.exe» запускаем только после «Запустить?»,
# а «телеграм» → «Телеграм.lnk» — сразу
PROGRAM_EXTS = EXECUTABLE_EXTS - {".lnk"}


def is_executable(path):
    return os.path.splitext(str(path or ""))[1].lower() in EXECUTABLE_EXTS


def is_program(path):
    return os.path.splitext(str(path or ""))[1].lower() in PROGRAM_EXTS


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(p)))).rstrip("\\/")


def protected_roots(project_dir=None, extra=()):
    """Папки, которые Рокси не удаляет и не очищает даже после «да»: профиль пользователя, его стандартные
    папки, корни дисков, системные каталоги и сам проект."""
    home = os.path.expanduser("~")
    roots = {home}
    for sub in ("Desktop", "Рабочий стол", "Documents", "Документы", "Downloads", "Загрузки", "Pictures",
                "Изображения", "Music", "Музыка", "Videos", "Видео", "OneDrive", "AppData"):
        roots.add(os.path.join(home, sub))
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemDrive", "USERPROFILE"):
        v = os.environ.get(var)
        if v:
            roots.add(v if var != "SystemDrive" else v + "\\")
    if project_dir:
        roots.add(project_dir)
    roots.update(extra)
    return {_norm(r) for r in roots if r}


def is_protected_path(path, project_dir=None, extra=()):
    """True, если path — защищённая папка ИЛИ её родитель (нельзя удалить то, внутри чего лежит защищённое),
    либо корень диска. Файлы ВНУТРИ Документов/Рабочего стола удалять можно — это и есть обычная работа."""
    if not path:
        return True
    p = _norm(path)
    if os.path.dirname(p) == p or re.fullmatch(r"[a-z]:", p, re.IGNORECASE):    # корень диска
        return True
    for root in protected_roots(project_dir, extra):
        if p == root or root.startswith(p + os.sep):
            return True
    return False


def _clear_allowed_exact(extra_allowed=()):
    """Папки, содержимое которых МОЖНО очищать голосом (после «да»): Загрузки и Рабочий стол — это обычный сценарий."""
    home = os.path.expanduser("~")
    allowed = {os.path.join(home, "Downloads"), os.path.join(home, "Загрузки"),
               os.path.join(home, "Desktop"), os.path.join(home, "Рабочий стол")}
    allowed.update(extra_allowed)
    return {_norm(a) for a in allowed if a}


def is_clear_forbidden(path, project_dir=None, extra=(), allow=()):
    """Очистка содержимого строже обычного удаления файла, но мягче удаления самой папки:
    Загрузки/Рабочий стол чистить можно, а Документы, Изображения, профиль, диск, Windows и проект — нет."""
    if _norm(path) in _clear_allowed_exact(allow):
        return False
    return is_protected_path(path, project_dir, extra)
