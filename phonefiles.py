"""Файлы, скриншоты и буфер обмена между компьютером и телефоном (через чат управления в Telegram).

«пришли файл отчёт.docx», «скинь скриншот», «пришли сетку», «что в буфере», «положи в буфер: текст».
Файл, присланный в чат с телефона, Рокси сохраняет в «Загрузки» (это делает tg_remote).
Разбор — чистые функции (тесты); отправка — через ctx.send_file (Telegram).
"""
import os
import re

_SEND = r"(?:пришли|скинь|отправь|кинь|перешли|дай|скинуть|прислать|отправить)"
_TO_PHONE = r"(?:\s+(?:мне\s+)?(?:на\s+телефон|в\s+(?:телеграм\w*|тг|телегу|чат)))?"
_RE_SEND_FILE = re.compile(rf"^\s*(?:рокси\W+)?{_SEND}\s+(?:мне\s+)?(?:на\s+телефон\s+|в\s+(?:телеграм\w*|тг|телегу|чат)\s+)?"
                           r"(?:файл|документ|фото|фотку|картинку|видео|архив)\s+(.+?)"
                           r"(?:\s+(?:мне\s+)?(?:на\s+телефон|в\s+(?:телеграм\w*|тг|телегу|чат)))?\s*[.!]?\s*$")
_RE_SEND_SCREEN = re.compile(rf"^\s*(?:рокси\W+)?(?:(?:сделай\s+(?:и\s+)?)?{_SEND}\s+(?:мне\s+)?(?:на\s+телефон\s+)?"
                             r"(?:скрин\w*|снимок\s+экрана|экран)|/screen)\b(.*)$")
_RE_GRID = re.compile(rf"^\s*(?:рокси\W+)?(?:(?:покажи|{_SEND})\s+(?:мне\s+)?(?:экран\s+|скрин\w*\s+)?(?:с\s+)?сетк\w*|сетк[аиу]|/grid)\s*[.!]?\s*$")
_RE_CLIP_GET = re.compile(r"\bчто\s+(?:у\s+меня\s+|сейчас\s+|лежит\s+)?(?:в|во)\s+буфер\w*|\b(?:покажи|прочитай|пришли|скинь)\s+(?:мне\s+)?буфер\w*"
                          r"|\bчто\s+(?:я\s+)?скопировал\w*|\bсодержимое\s+буфера")
_RE_CLIP_SET = re.compile(r"^\s*(?:рокси\W+)?(?:положи|скопируй|запиши|кинь|помести|занеси|сохрани)\s+(?:в|во)\s+буфер(?:\s+обмена)?"
                          r"\s*[:,—-]?\s+(.+)$", re.S)

MAX_BYTES = 1500 * 1024 ** 2        # Telegram принимает до 2 ГБ; больше полутора — скорее ошибка

# Файлы, которые с компьютера не уходят никогда: ключи, пароли, сессии Telegram
_SECRET = re.compile(r"^\.env(\..*)?$|\.session(-journal)?$|^id_(?:rsa|ed25519|ecdsa|dsa)(\.pub)?$|\.kdbx?$|\.pem$|"
                     r"\.key$|\.pfx$|\.p12$|^login data|^cookies(-journal)?$|^web data$|^credentials?\.json$|"
                     r"^token\.json$|^secrets?\.(json|ya?ml|toml|txt)$", re.I)


def parse(text):
    """→ ('grid',) | ('screen',) | ('file', имя) | ('clip_get',) | ('clip_set', текст) | None"""
    t = (text or "").strip()
    low = t.lower()
    if _RE_GRID.search(low):
        return ("grid",)
    m = _RE_SEND_SCREEN.search(low)
    if m:
        rest = re.sub(r"\b(?:мне|сюда|на\s+телефон|в\s+(?:телеграм\w*|тг|телегу|чат)|экрана?|пожалуйста)\b|[.!,]", " ", m.group(1))
        if re.search(r"\bсетк", rest):
            return ("grid",)
        if not rest.strip():
            return ("screen",)                        # «скинь скриншот Васе» — это не нам

    m = _RE_CLIP_SET.match(t)
    if m and m.group(1).strip():
        return ("clip_set", m.group(1).strip())
    if _RE_CLIP_GET.search(low):
        return ("clip_get",)
    m = _RE_SEND_FILE.match(t)
    if m:
        return ("file", m.group(1).strip(" «»\"'"))
    return None


def is_secret(path, project_dir=None):
    base = os.path.basename(path)
    if _SECRET.search(base):
        return True
    if project_dir and base.lower() == "config.json" and \
            os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(project_dir)):
        return True                                   # в config.json Рокси — tg_api_hash
    parts = {p.lower() for p in re.split(r"[\\/]+", os.path.abspath(path))}
    return bool(parts & {".ssh", ".gnupg", ".aws", ".azure", ".kube"})


def unique_path(folder, name):
    """«фото.jpg» → «фото (1).jpg», если такой уже есть."""
    stem, ext = os.path.splitext(name)
    p, i = os.path.join(folder, name), 1
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem} ({i}){ext}")
        i += 1
    return p


def clipboard_get():
    """→ ('text', str) | ('image', PIL.Image) | ('files', [пути]) | ('empty', None)"""
    try:
        from PIL import ImageGrab
        im = ImageGrab.grabclipboard()
        if isinstance(im, list):
            return "files", im
        if im is not None:
            return "image", im
    except Exception:
        pass
    try:
        import pyperclip
        s = pyperclip.paste() or ""
        if s.strip():
            return "text", s
    except Exception:
        pass
    return "empty", None
