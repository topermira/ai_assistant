"""Определение намерения по фразе. Только re/difflib — модуль тестируется без Windows.

Порядок проверок важен, он зафиксирован тестами в tests/test_intents.py.
LLM-части (анализ кода, роутер) передаются снаружи как функции.
"""
import re

import keys as keyboard_keys
import mediacap
import pcctl
import phonefiles
import power
import scenarios
import syscmd

from volume import parse_volume_command

# ==================== БАЗОВЫЕ ====================
_RE_WAKE_PREFIX = re.compile(r"^\s*рокс\w{0,3}\b[\s,.!?]*", re.IGNORECASE)   # Рокси, Рокса, Роксен…

_RE_STOP = re.compile(
    r"^\s*(?:рокси[,\s]+)?"
    r"(?:стоп|остановись|выключись|выход|пока|до\s+свидания|отключись|"
    r"заверши\s+работу|завершение\s+работы|спать|до\s+встречи)"
    r"(?:\s+пожалуйста)?\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# \b в конце обязателен: без него «нетфликс открой» и «нетбук включи» ловились как «нет…» и уходили в болталку.
_RE_CHAT_SHORT = re.compile(r"^(?:где|нет|почему|как так|зачем|что такое|объясни)\b")
# Если в короткой фразе есть явный глагол-команда, это не болтовня («где мои файлы, открой папку»).
_RE_CHAT_SHORT_COMMAND = re.compile(
    r"\b(?:открой|открыть|включи|выключи|запусти|удали|создай|закрой|сверни|разверни|переименуй|найди|"
    r"поставь|заведи|напомни|напиши)\b")
# Эти фразы — команды экрана, хотя начинаются с «объясни»/«что за»: проверяются раньше болталки.
_SCREEN_PHRASES = ("посмотри на экран", "что на экране", "объясни ошибку", "что за ошибка")
# «глянь на экран, что думаешь», «что тут за ошибка», «прокомментируй экран», «что ты видишь у меня на экране»,
# «переведи, что на экране», «помоги с тем, что на экране», «что здесь написано»
_RE_SCREEN = re.compile(
    r"\b(?:посмотри|глянь|взгляни|погляди|смотри)\s+(?:\w+\s+){0,2}(?:на\s+)?(?:экран|монитор|окно)\w*"
    r"|\bна\s+(?:моём\s+|моем\s+)?экране\b|\bэкран\w*\s+(?:покажу|видишь)"
    r"|\b(?:прокомментируй|оцени|опиши)\s+(?:\w+\s+)?(?:экран|окно|что\s+(?:тут|здесь|на\s+экране))"
    r"|\bчто\s+(?:тут|здесь)\s+(?:за\s+ошибк\w*|написано|не\s+так)|\bобъясни\s+(?:эту\s+)?ошибк\w*"
    r"|\bчто\s+за\s+ошибк\w*|\bчто\s+ты\s+(?:тут\s+|здесь\s+)?видишь\s*(?:[?.!]|$|тут|здесь|у\s+меня)")
# Раньше хватало слова "включи" ("включи быстро музыку" → быстрый режим). Теперь нужно "режим"/"голос".
_RE_FAST_MODE = re.compile(r"\b(?:быстр\w*|ускор\w*)\s+(?:режим|голос)|\b(?:режим|голос)\w*\s+(?:побыстрее|быстр\w*)")
_RE_ANIME_MODE = re.compile(r"\bаниме\w*\s+(?:режим|голос)|\b(?:режим|голос)\w*\s+аниме|\bверни\s+рокси\b|\bаниме[-\s]голос")
# Раньше "[зс]т[оа]н" ловило "установи", "Астана", "остановись", "Константин". Теперь только целые слова.
_RE_MOAN = re.compile(r"\b(?:п[оа]|з[аы])?ст[оа]н(?:ай|ай-ка|и|ать|ешь)?\b")
_RE_PURR = re.compile(r"помурчи|мурчи|мурлыч")
_RE_WEATHER = re.compile(r"\bпогод[аыуе]", re.IGNORECASE)
# Время и дата — с часов компьютера, а не из LLM (она выдумывала)
_RE_TIME = re.compile(r"\bкоторый\s+(?:сейчас\s+)?час\b|\bсколько\s+(?:сейчас\s+)?(?:времени|время)\b|"
                      r"\b(?:скажи|подскажи)\s+(?:мне\s+)?(?:который\s+час|время)\b|\b(?:текущее\s+время|время\s+сейчас)\b")
_RE_DATE = re.compile(r"\bкакое\s+(?:сегодня\s+)?число\b|\bкакая\s+(?:сегодня\s+)?дата\b|"
                      r"\bкакой\s+(?:сегодня\s+)?день(?:\s+недели)?\b|\bчто\s+сегодня\s+за\s+день\b|\bсегодняшн\w+\s+дат\w+\b")

# ==================== НОВОЕ: таймеры, медиа ====================
_RE_TIMER = re.compile(r"\b(?:поставь|заведи|включи|запусти)?\s*таймер\b", re.IGNORECASE)
_RE_REMIND = re.compile(r"\bнапомни\b", re.IGNORECASE)
_RE_TIMER_CANCEL = re.compile(r"\b(?:отмени|убери|сбрось|выключи)\s+(?:все\s+)?(?:таймер\w*|напоминани\w*)", re.IGNORECASE)
_RE_TIMER_LIST = re.compile(r"\b(?:какие|сколько)\s+(?:у\s+меня\s+)?(?:таймер\w*|напоминани\w*)|\bсколько\s+осталось\b", re.IGNORECASE)

MEDIA_COMMANDS = [
    (re.compile(r"^(?:сделай\s+)?(?:погромче|громче)\b|\bприбавь\s+(?:звук|громкость)|\bувеличь\s+громкость"), "volume_up"),
    (re.compile(r"^(?:сделай\s+)?(?:потише|тише)\b|\bубавь\s+(?:звук|громкость)|\bуменьши\s+громкость"), "volume_down"),
    (re.compile(r"\b(?:выключи|отключи)\s+звук\b|\bбез\s+звука\b|\bзамуть\b|\bвключи\s+звук\b"), "mute"),
    (re.compile(r"^(?:поставь\s+на\s+)?пауз[ауы]\b|\bпродолжи\s+(?:воспроизведение|музыку|видео)\b|\bсними\s+с\s+паузы\b"), "play_pause"),
    (re.compile(r"\bследующ\w+\s+(?:трек|песн\w*|видео)\b|\bпереключи\s+(?:трек|песню)\b"), "next"),
    (re.compile(r"\bпредыдущ\w+\s+(?:трек|песн\w*|видео)\b|\bпрошл\w+\s+(?:трек|песн\w*)\b"), "prev"),
]

_FOLDER_KW = re.compile(
    r"(?:\bпапк|\bкаталог|\bдиректори|\bрабочий\s+стол|\bдокумент[ыа]|\bзагрузк|\bскачивани"
    r"|\bмузык[ауие]|\bвидео\b|\bкартинк|\bизображени|\bдомашн|\bпрофиль)",
    re.IGNORECASE
)
_RE_EXPLICIT_FOLDER = re.compile(r'в\s+папке.*?[«"]([^»"]+)[»"]', re.IGNORECASE)

_PROFANITY = {
    "блять", "бля", "блядь", "блят", "хуй", "хуя", "хуем", "хуё",
    "пизда", "пиздец", "ебать", "ебал", "ебало", "нахуй", "нахуя",
    "хер", "хрен", "херня", "сука", "сучка", "залупа", "мудак",
    "мудило", "уёбок", "ёб", "заебал", "заебала", "заебало",
}
_FOLDER_NOISE = {
    "пожалуйста", "плиз", "мне", "быстро", "сейчас", "ну", "давай",
    "открой", "открыть", "покажи", "запусти",
    "папку", "папки", "папкой", "папкам", "папках",
    "каталог", "каталоги", "каталогу", "каталоге",
    "директорию", "директория", "директории",
    "названием", "именем", "по", "имени", "эту", "ту",
    "на", "рабочем", "столе", "рабочего", "стола", "с",
}

def _clean_folder_query(raw):
    if not raw: return ""
    text = raw.strip()
    text = re.sub(r'[«»""''`]', ' ', text)
    text = re.sub(r'[.,!?;:]+', ' ', text)
    words = re.findall(r"[А-Яа-яЁёA-Za-z0-9_\-]+", text)
    return " ".join(w for w in words if w.lower() not in _PROFANITY and w.lower() not in _FOLDER_NOISE).strip()

# ==================== РЕГУЛЯРКИ ДЕЙСТВИЙ ====================
_RE_NEW_TAB = re.compile(r"(?:открой|создай)\s+(?:новую\s+вкладку|новый\s+файл)")
_RE_CREATE = re.compile(
    r"(?:созда[йюмёт]|сдела[йю]|запиш[иу]|создать|сделать|записать|создаю|создам)\s+.*?"
    r"(?:файл|папк|блокнот|текстовик|документ|txt|каталог|директори)"
    r"|(?:файл|папк|блокнот|текстовик|документ|каталог|директори).{0,30}?(?:с\s+(?:именем|названием)|назови)"
    r"|(?:в\s+(?:текущей|этой|открытой|данной)\s+(?:папке|директории|каталоге)).*?(?:файл|папк|блокнот|текстовик|документ|каталог|директори)"
    r"|^\s*(?:рокси[,\s]+)?(?:новую\s+)?(?:папку|файл|блокнот|текстовик|документ)\s+\S+.*?(?:в\s+(?:текущей|этой|открытой|данной)\s+(?:папке|директории|каталоге)|на\s+рабочем\s+столе)\s*$",
    re.IGNORECASE,
)
_RE_CLEAR_TEXT = re.compile(
    r"(?:удали|удалить|сотри|стереть|очисти|очистить|убери|убрать)\s+(?:(?:весь|всё|все|всё\s+содержимое|содержимое)\s+)?"
    r"(?:текст[а-яё]*|содержимое|содержание|записи|информацию)?\s*(?:из|в|с|из-за)\s+(?:(?:открытого|текущего|этого)\s+)?"
    r"(?:блокнот[а-яё]*|заметк[а-яё]*|записи)|(?:очисти|очистить|сотри|стереть|удали|удалить|убери|убрать)\s+(?:(?:весь|всё)\s+)?(?:блокнот|заметки)\s*$",
    re.IGNORECASE,
)
_RE_CLEAR_CONTEXT = re.compile(
    r"(?:удали|удалить|сотри|стереть|очисти|очистить|убери|убрать)\s+(?:всё|все|весь\s+текст|все\s+файлы|содержимое)\s+(?:из|с)\s+(?:этого|текущего|данного|открытого)\s+(?:окна|блокнота|заметки)\s*$",
    re.IGNORECASE,
)
_RE_CLEAR_CURRENT_FOLDER = re.compile(
    r"(?:удали|удалить|сотри|стереть|очисти|очистить|убери|убрать)\s+(?:всё|все|всё\s+содержимое|содержимое|весь\s+текст|все\s+файлы|всю\s+информацию)\s+(?:из|в)\s+(?:этой|текущей|открытой|данной)\s+(?:папки|директории|каталога)\s*$",
    re.IGNORECASE,
)
_RE_CLEAR_FOLDER = re.compile(
    r"(?:удали|удалить|сотри|стереть|очисти|очистить|убери|убрать)\s+(?:всё|все|всё\s+содержимое|содержимое|весь\s+текст|все\s+файлы|всю\s+информацию)\s+(?:из|в)\s+(?:папки|каталога|директории)\s+[«\"]?(.+?)[»\"]?\s*$",
    re.IGNORECASE,
)
_RE_DELETE_FILE = re.compile(
    r"(?:удали|удалить|сотри|стереть|снеси|убери|убрать)\s+(?:файл|папку|каталог|документ|директорию|заметку|блокнот)\s+[«\"]?(.+?)[»\"]?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_RE_SHOW_GUI = re.compile(r"\b(?:покажи|открой|выведи|включи|разверни|раскрой)\b.*\b(?:окн\w*\s+(?:в\s+)?вывод\w*|консоль|лог|вывод)\b", re.IGNORECASE)
_RE_HIDE_GUI = re.compile(r"\b(?:сверни|свернуть|скрой|закрой|убери|выключи)\b.*\b(?:окн\w*\s+(?:в\s+)?вывод\w*|консоль|лог|вывод)\b", re.IGNORECASE)
_RE_MAXIMIZE_CURSOR = re.compile(r"(?:разверни|раскрой|увеличь|максимизируй|на\s+полный|на\s+весь).*(?:курсор|мышь|наведен|вкладку)")
_RE_MAXIMIZE_FULL = re.compile(
    r"(?:разверн\w*|раскро\w*|раскры\w*|увелич\w*|максимизир\w*|откро\w*|откры\w*)\s+(?:(?:окно|окошко)\s+)?(?:(.+?)\s+)?(?:на|во)\s+(?:весь|полный|полное)\s+(?:экран|монитор|окно)",
    re.IGNORECASE,
)
_RE_MAXIMIZE_FULL_SHORT = re.compile(r"^\s*(?:рокси[,\s]+)?(?:во|на)\s+(?:весь|полный|полное)\s+(?:экран|монитор)\s*[.!?]?\s*$", re.IGNORECASE)
_RE_WINDOW = re.compile(
    r"(сверни|свернуть|разверни|развернуть|закрой|закрыть|переключись\s+на|активируй|активировать|верни|вернуть|восстанови|восстановить|подними|поднять)(?:\s+(?:(?:на|в)\s+)?(?:окно\s+)?(.+))?",
    re.IGNORECASE,
)
_RE_RENAME = re.compile(r"переименуй")
# «создай ArsPidoras.txt» — имя файла без слова «файл»
_RE_CREATE_BARE = re.compile(
    r"(?:созда\w*|сдела[йю]|заведи)\s+(?:мне\s+)?(?:(?:новый|новую|пустой|пустую)\s+)?(?:(?:файл|папку|документ|блокнот|текстовик)\s+)?"
    r"(\S+\.[A-Za-zА-Яа-яЁё0-9]{1,5})(?=[\s,.!?]|$)", re.IGNORECASE)


def create_name(user_text):
    """Имя файла/папки из «создай …» (пустая строка, если имени нет)."""
    nm = re.search(r"(?:с\s+(?:названием|именем)|назови|именем)\s+[«\"]?(.+?)[»\"]?\s*$", user_text, re.IGNORECASE)
    name = ""
    if nm:
        name = nm.group(1).strip()
        name = re.sub(r" (?:на рабочем столе|файл|папку|блокнот|текстовик|документ|тут|здесь)", "", name, flags=re.IGNORECASE).strip()
    if not name:
        # «создай файл Арс.txt», «создай папку проекты на рабочем столе», «файл список покупок в загрузках»
        m2 = re.search(r"(?:папку|файл|блокнот|текстовик|документ)\s+(.+?)(?=\s+(?:в|на)\s+|\s+(?:тут|здесь)\b|\s*$)",
                       user_text, re.IGNORECASE)
        if m2: name = m2.group(1).strip()
        if name.lower() in ("тут", "здесь"): name = ""
    if not name:
        m3 = _RE_CREATE_BARE.search(user_text)
        if m3: name = m3.group(1).strip()
    return name
_RE_OPEN = re.compile(r"(?:откр[оыа]й|отр[оыа]й|открыть|запуст[иь]|включ[иь]|вруби|старт)\s+")
_RE_TYPE_CMD = re.compile(r"(?:напиши|введи|напечатай|вставь|ставь|впиши|запиши|добавь)\s+", re.IGNORECASE)
_RE_CODE = re.compile(r"(?:код|скрипт|программ)")
_RE_HERE = re.compile(r"\b(?:тут|здесь|в\s+(?:[а-яё]+\s+){0,3}(?:папке|каталоге|директории)|в\s+эту\s+папку)\b", re.IGNORECASE)
_RE_CLOSE_TAB = re.compile(r"^\s*(?:рокси[,\s]+)?(?:закрой|закрыть)\s+(?:эту\s+|текущую\s+|данную\s+)?вкладку\s*[.!?]?\s*$", re.IGNORECASE)
_RE_MINIMIZE_ALL = re.compile(
    r"^\s*(?:рокси[,\s]+)?(?:(?:сверни|свернуть|минимизируй|скрой)\s+(?:всё|все|все\s+окна|всё\s+окно|все\s+задачи|всё\s+сразу|все\s+программы|все\s+приложения)|(?:покажи|открой|переключись)\s+(?:на\s+)?(?:рабочий\s+стол|раб\s+стол|раб\.?\s*стол|десктоп|desktop)|(?:сверни|свернуть|минимизируй)\s+всё)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_RE_CLOSE_ALL = re.compile(
    r"^\s*(?:рокси[,\s]+)?(?:закрой|закрыть|заверши|завершить|выключи|выруби)\s+(?:все\s+окна|все\s+программы|все\s+приложения|всё|все\s+задачи)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# ==================== TELEGRAM РЕГУЛЯРКИ ====================
_RE_TG_HINT = re.compile(r"\b(?:телеграм|телеграмм|телеграма|телеграмме|телег[уеа]|тг|telegram)\b", re.IGNORECASE)
_RE_TG_REPLY = re.compile(
    r"^\s*(?:рокси[,\s]+)?(?:ответь|напиши\s+ответ)\s+(?:(?:в\s+)?(?:телеграм\w*|телег[уеа]|тг|telegram)\s+)?([@А-Яа-яЁёA-Za-z0-9_\-\.]+)(?:\s+(?:в|на)\s+(?:телеграм\w*|телег[уеа]|тг|telegram))?\s*[,:]?\s+(.+)$",
    re.IGNORECASE,
)
_RE_TG_SEND = re.compile(
    r"^\s*(?:рокси[,\s]+)?(?:напиши|отправь|пошли|скинь|передай|скажи)\s+(?:(?:в\s+)?(?:телеграм\w*|телег[уеа]|тг|telegram)\s+)?([@А-Яа-яЁёA-Za-z0-9_\-\.]+)(?:\s+(?:в|на)\s+(?:телеграм\w*|телег[уеа]|тг|telegram))?\s*[,:]?\s+(.+)$",
    re.IGNORECASE,
)
# Раньше [А-ЯЁA-Z] при re.IGNORECASE ловило любое слово: "напиши стих" → контакт "стих".
# Теперь имя ОБЯЗАНО начинаться с заглавной (Whisper пишет имена с большой), глагол — любой регистр.
_RE_TG_SEND_MAYBE = re.compile(
    r"^\s*(?i:рокси[,\s]+)?(?i:напиши|отправь|скажи|передай|пошли|скинь)\s+"
    r"([А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z][а-яёa-z]+)?)"
    r"\s*(?:[,.:]\s*|\s+(?i:то)\s*,?\s*(?i:что)\s+|\s+(?i:что)\s+|\s+)(.+)$"
)
_TG_MAYBE_STOPWORDS = {
    "сюда", "тут", "здесь", "в", "текст", "код", "письмо", "стих", "стихи", "стишок", "скрипт",
    "программу", "сообщение", "песню", "историю", "рассказ", "сказку", "мне", "что", "как", "сколько",
    "привет", "пожалуйста", "функцию", "класс", "эссе", "статью", "пост", "ответ", "шутку", "анекдот",
    "который", "когда", "где", "почему", "зачем", "кто", "время", "погоду", "новости", "рокси",
}
_RE_TG_READ = re.compile(
    r"^\s*(?:рокси[,\s]+)?(?:прочитай|прочти|покажи|что\s+нового|что\s+пишут)\s+(?:в\s+)?(?:телеграм\w*|телег[уеа]|тг|telegram)(?:\s+(?:у|от|с)?\s*([@А-Яа-яЁёA-Za-z0-9_\-\. ]+?))?\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# ==================== VISION РЕГУЛЯРКИ ====================
_RE_VISION_TRIGGER = re.compile(
    r"\b(?:кликни|кликнуть|щёлкни|нажми\s+на|нажать\s+на|заполни|заполнить|введи\s+в\s+поле|перетащи|перетащить|найди\s+на\s+экране|найти\s+на\s+экране|через\s+экран|используя\s+экран|визуально|сделай\s+через\s+экран|выполни\s+на\s+экране|используй\s+экран)\b",
    re.IGNORECASE,
)

_WINDOW_NOISE = (
    "окно", "это", "это окно", "эту", "эту папку", "эта папка", "это папку", "это папка", "эту папку.",
    "текущую папку", "текущая папка", "текущее окно", "открытую папку", "открытая папка", "открытое окно",
    "активное окно", "активный", "активное", "всё", "все", "всё окно", "его", "её", "ее", "ту", "тот",
    "папку", "папка", "эту вкладку", "вкладку", "вкладка", "вкладке", "текущую вкладку", "эту вкладку",
    "все окна", "все программы", "все приложения",
)
_WINDOW_NOISE_PREFIX = re.compile(r"^(?:текущ|это|активн|данн|открыт|все\s)", re.IGNORECASE)

# Явная просьба написать код — проверяется РАНЬШЕ "напиши <текст>"
_RE_CODE_REQUEST = re.compile(
    r"\b(?:напиши|сгенерируй|создай|сделай|набросай|накидай|запили|вставь|ставь|впиши|введи|добавь)\s+(?:мне\s+)?(?:\w+\s+){0,2}?"
    r"(?:код\w*|скрипт\w*|программ\w*|функци\w+|бот\w*)\b",
    re.IGNORECASE,
)
# «Напиши стих/рассказ» — это творческий текст для LLM, а не печать слова «стих» в Блокнот
_RE_CREATIVE = re.compile(
    r"\b(?:напиши|сочини|придумай|расскажи)\s+(?:мне\s+)?(?:\w+\s+)?(?:стих\w*|стишок|рассказ\w*|сказк\w*|"
    r"историю|песн\w*|сочинени\w*|эссе|шутк\w*|анекдот\w*|поздравлени\w*|тост)\b",
    re.IGNORECASE,
)
# Хоткеи, которые раньше перехватывал "запиши/вставь <текст>"
_PRIORITY_HOTKEYS = [
    (re.compile(r"^\s*(?:запиши\s+экран|запись\s+экрана)\s*[.!?]?\s*$"), "win+alt+r", "Запись экрана"),
    (re.compile(r"^\s*(?:вставь|вставить)\s+(?:содержимое|из\s+буфера(?:\s+обмена)?)\s*[.!?]?\s*$"), "ctrl+v", "Вставляю"),
]

# ==================== HOTKEY MAP ====================
_HOTKEY_MAP = [
    (r"\b(?:скопируй(?!\s+текст)|копируй|копировать)\b", "ctrl+c", "Копирую"),
    (r"\b(?:вырежь|вырезать)\b", "ctrl+x", "Вырезаю"),
    (r"\b(?:вставь|вставить)\s*$", "ctrl+v", "Вставляю"),
    (r"\b(?:вставь|вставить)\s+(?:содержимое|из\s+буфера|из\s+буфера\s+обмена)\s*$", "ctrl+v", "Вставляю"),
    (r"\b(?:отмени|отменить|undo|откати|откатись|верни\s+назад)\b", "ctrl+z", "Отменяю"),
    (r"\b(?:повтори|повторить|redo)\b", "ctrl+y", "Повторяю"),
    (r"\b(?:выдели\s+всё|выдели\s+все|выбрать\s+всё|выделить\s+всё|выдели\s+текст)\b", "ctrl+a", "Выделяю"),
    (r"^\s*(?:рокси[,\s]+)?(?:сохрани|сохранить|save)(?:\s+(?:файл|документ|всё))?\s*[.!?]?\s*$", "ctrl+s", "Сохраняю"),
    (r"\b(?:сохрани\s+как|save\s+as)\b", "ctrl+shift+s", "Сохраняю как"),
    (r"^\s*(?:рокси[,\s]+)?(?:найди|найти|поищи|поиск)\s*[.!?]?\s*$", "ctrl+f", "Поиск"),
    (r"\b(?:замени|заменить)\s*$", "ctrl+h", "Замена"),
    # «создай файл X» → ACTION_CREATE; «закрой Новый файл.txt» (окно/имя файла) — не Ctrl+N
    (r"^(?!.*\b(?:закр\w+|сверн\w+|разверн\w+|активир\w+|переключ\w+)\b)(?!.*\.\w{2,5}\b).*\b(?:новый\s+файл|новый\s+документ)\b", "ctrl+n", "Новый файл"),
    (r"\b(?:новая\s+вкладка|создай\s+вкладку|открой\s+новую\s+вкладку)\b", "ctrl+t", "Новая вкладка"),
    (r"\b(?:восстанови\s+вкладку|открой\s+закрытую\s+вкладку)\b", "ctrl+shift+t", "Восстанавливаю вкладку"),
    (r"\b(?:перезагрузи\s+страницу|обнови\s+страницу|обнови|перезагрузи)\b", "f5", "Обновляю"),
    (r"\b(?:распечатай|печать|печатай)\b", "ctrl+p", "Печать"),
    (r"\b(?:следующее\s+окно|смени\s+окно|переключи\s+окно|переключись\s+по\s+окнам)\b", "alt+tab", "Переключаю окно"),
    (r"\b(?:закрой\s+(?:программу|приложение|активное\s+приложение|текущую\s+программу))\b", "alt+f4", "Закрываю"),
    (r"\b(?:выполнить|открой\s+выполнить|win\+r)\b", "win+r", "Выполнить"),
    (r"\b(?:заблокируй|заблокируй\s+(?:пк|компьютер|экран)|lock)\b", "win+l", "Блокирую"),
    (r"\b(?:открой\s+настройки|открой\s+параметры|параметры\s+системы|настройки\s+системы)\b", "win+i", "Настройки"),
    (r"\b(?:буфер\s+обмена)\b", "win+v", "Буфер обмена"),
    (r"\b(?:эмодзи|смайлики|emoji)\b", "win+.", "Эмодзи"),
    (r"\b(?:полный\s+экран)\b", "f11", "Полный экран"),
    (r"\b(?:переименуй\s+(?:файл|папку))\b", "f2", "Переименовать"),
    (r"\b(?:скриншот|скрин|снимок\s+экрана|сделай\s+скрин)\b", "win+shift+s", "Скриншот"),
    (r"\b(?:запиши\s+экран|запись\s+экрана)\b", "win+alt+r", "Запись экрана"),
    (r"\bctrl\+c\b", "ctrl+c", "Ctrl+C"), (r"\bctrl\+v\b", "ctrl+v", "Ctrl+V"),
    (r"\bctrl\+x\b", "ctrl+x", "Ctrl+X"), (r"\bctrl\+a\b", "ctrl+a", "Ctrl+A"),
    (r"\bctrl\+z\b", "ctrl+z", "Ctrl+Z"), (r"\bctrl\+y\b", "ctrl+y", "Ctrl+Y"),
    (r"\bctrl\+s\b", "ctrl+s", "Ctrl+S"), (r"\bctrl\+f\b", "ctrl+f", "Ctrl+F"),
    (r"\bctrl\+t\b", "ctrl+t", "Ctrl+T"), (r"\bctrl\+w\b", "ctrl+w", "Ctrl+W"),
    (r"\bctrl\+n\b", "ctrl+n", "Ctrl+N"), (r"\bctrl\+p\b", "ctrl+p", "Ctrl+P"),
    (r"\balt\+tab\b", "alt+tab", "Alt+Tab"), (r"\balt\+f4\b", "alt+f4", "Alt+F4"),
    (r"\bwin\+d\b", "win+d", "Win+D"), (r"\bwin\+e\b", "win+e", "Win+E"),
    (r"\bwin\+r\b", "win+r", "Win+R"), (r"\bwin\+l\b", "win+l", "Win+L"),
    (r"\bwin\+i\b", "win+i", "Win+I"), (r"\bwin\+v\b", "win+v", "Win+V"),
]

def _match_hotkey(text):
    for pattern, keys, desc in _HOTKEY_MAP:
        if re.search(pattern, text, re.IGNORECASE): return keys, desc
    return None, None

_SILENT_INTENTS = {
    "[ACTION_OPEN]", "[ACTION_WINDOW]", "[ACTION_MAXIMIZE_FULL]", "[ACTION_MAXIMIZE_CURSOR]",
    "[ACTION_NEW_TAB]", "[ACTION_SHOW_GUI]", "[ACTION_HIDE_GUI]", "[ACTION_CLOSE_TAB]",
    "[ACTION_MINIMIZE_ALL]", "[ACTION_CLOSE_ALL]", "[ACTION_HOTKEY]", "[ACTION_VISION]",
    "[ACTION_LIBRARY]", "[ACTION_MEDIA]", "[ACTION_PET]",
}

_RE_TO_NOTEPAD = re.compile(r"блокнот|notepad|заметк|текстовик")


def type_target(lower):
    """Куда печатать по «напиши …»: 'notepad', если в фразе есть Блокнот/заметки, иначе 'cursor' — в активное окно."""
    return "notepad" if _RE_TO_NOTEPAD.search(lower or "") else "cursor"


ACTION_INTENTS = {
    "[ACTION_POWER]", "[ACTION_BRIGHTNESS]", "[ACTION_CAPTURE]", "[ACTION_PLAN]", "[ACTION_MOUSE]", "[ACTION_KEYS]", "[ACTION_SYS]", "[ACTION_PHONE]",
    "[ACTION_OPEN]", "[ACTION_TYPE]", "[ACTION_CREATE]", "[ACTION_NEW_TAB]", "[ACTION_CODE_FILE]",
    "[ACTION_WINDOW]", "[ACTION_RENAME]", "[ACTION_MAXIMIZE_CURSOR]", "[ACTION_MAXIMIZE_FULL]",
    "[ACTION_SHOW_GUI]", "[ACTION_HIDE_GUI]", "[ACTION_CLEAR_TEXT]", "[ACTION_CLEAR_CONTEXT]",
    "[ACTION_DELETE_FILE]", "[ACTION_CLEAR_FOLDER]", "[ACTION_CLOSE_TAB]", "[ACTION_MINIMIZE_ALL]",
    "[ACTION_CLOSE_ALL]", "[ACTION_HOTKEY]", "[ACTION_VISION]", "[ACTION_TG_SEND]", "[ACTION_TG_REPLY]",
    "[ACTION_TG_READ]", "[ACTION_TG_SEND_MAYBE]", "[ACTION_TG_HELP]", "[ACTION_LIBRARY]",
    "[ACTION_TIMER]", "[ACTION_REMIND]", "[ACTION_TIMER_CANCEL]", "[ACTION_TIMER_LIST]", "[ACTION_MEDIA]", "[ACTION_VOLUME]",
    "[ACTION_PET]", "[ACTION_TIME]",
}


# ==================== LIVE2D: ПОВЕДЕНИЕ ====================
PET_COMMANDS = [
    (re.compile(r"\b(?:сяд\w*|сяди\w*|сад\w*|залезь|залезай|запрыгни|забирайся|заберись|прыгни)\s+(?:на|сверху\s+на)\s+(?:окн\w*|вкладк\w*|программ\w*)\b|\bсядь\s+сверху\b"), "perch"),
    (re.compile(r"\bсальт\w*\b|\bфлип\w*\b|\bкувыр\w*\s+(?:в\s+воздухе|назад)\b"), "flip"),
    (re.compile(r"\bстойк\w*\s+на\s+рук\w*|\bвстань\s+на\s+руки\b|\bпостой\s+на\s+руках\b|\bна\s+руках\s+постой\b"), "handstand"),
    (re.compile(r"\b(?:сделай|покажи|крутани|прокрути|пройдись)\s+(?:\w+\s+)?колес\w*\b|\bколесом\b|^\W*(?:рокси\W+)?колесо\W*$"), "cartwheel"),
    (re.compile(r"\b(?:под|от|за|по)?прыгн\w*\b|\bпопрыгай\b|\bсделай\s+прыжок\b"), "jump"),
    (re.compile(r"\b(?:хватит|перестань|прекрати|не)\s+(?:\w+\s+)?(?:танцевать|танцуй|плясать|пляши)\b|\bстоп\s+танц\w*"), "dance_stop"),
    (re.compile(r"\b(?:потанцуй|станцуй|танцуй|подвигайся|попляши|спляши)\b.*\b(?:под\s+(?:музык\w*|песн\w*|трек\w*|эт\w+|ритм\w*|бит\w*)|в\s+такт)"), "dance_music"),
    (re.compile(r"\b(?:потанцуй|станцуй|танцуй|подвигайся)\b"), "dance"),
    (re.compile(r"\b(?:покрутись|крутись|покружись|кружись|вращайся|повернись\s+вокруг\s+себя|сделай\s+оборот)\b"), "spin"),
    (re.compile(r"\b(?:поклонись|сделай\s+поклон|поприветствуй)\b"), "bow"),
    (re.compile(r"\b(?:наколдуй|поколдуй|колдуй|сделай|покажи|вызови|сотвори|создай|запусти|устрой|призови)\s+(?:\w+\s+)?(?:л[её]д\w*|ледян\w*|снежин\w*|снег\w*|сосульк\w*|кристалл\w*)\b|\bзаморозь\b|\bледян\w+\s+(?:заклинани\w*|магию|магия)\b"), "magic_ice"),
    (re.compile(r"\b(?:наколдуй|поколдуй|колдуй|сделай|покажи|вызови|сотвори|создай|запусти|устрой|призови)\s+(?:\w+\s+)?(?:дожд\w*|тучк\w*|тучу|облак\w*)\b"), "magic_rain"),
    (re.compile(r"\b(?:наколдуй|поколдуй|колдуй|сделай|покажи|вызови|сотвори|создай|запусти|устрой|призови)\s+(?:\w+\s+)?фонтан\w*\b"), "magic_fountain"),
    (re.compile(r"\b(?:наколдуй|поколдуй|колдуй|сделай|покажи|вызови|сотвори|создай|запусти|устрой|призови)\s+(?:\w+\s+)?(?:водн\w+|водян\w+)?\s*шар\w*\b"), "magic_orb"),
    (re.compile(r"\b(?:обопрись|облокотись)\s+на\s+посох\b|\bдостань\s+посох\b"), "staff_lean"),
    (re.compile(r"\b(?:покрути|крути|повращай)\s+посох\w*\b"), "staff_twirl"),
    (re.compile(r"\b(?:на|по)?колдуй\b|\bпокажи\s+(?:\w+\s+)?(?:магию|волшебство|заклинани\w*|фокус)\b"
                r"|\b(?:сотвори|сделай|используй|примени)\s+(?:\w+\s+)?(?:магию|заклинани\w*)\b|\bмагию\s+покажи\b"), "magic"),
    (re.compile(r"\b(?:потянись)\b"), "stretch"),
    (re.compile(r"\b(?:зевни|позевай)\b"), "yawn"),
    (re.compile(r"\b(?:обопрись|облокотись|прислонись)\b"), "lean"),
    (re.compile(r"\b(?:оглянись|осмотрись|оглядись|посмотри\s+(?:по\s+сторонам|вокруг))\b"), "look_around"),
    (re.compile(r"\b(?:слезь|спустись|спрыгни)\b"), "floor"),
    (re.compile(r"\b(?:иди|подойди|беги)\s+(?:сюда|ко\s+мне)\b"), "come"),
    (re.compile(r"\b(?:погуляй|походи|прогуляйся|пройдись)\b"), "walk"),
    (re.compile(r"\b(?:гуляй|двигайся|ходи)\s+(?:сама|где\s+хочешь|свободно)\b|\bможешь\s+гулять\b"), "autonomy_on"),
    (re.compile(r"\b(?:стой\s+(?:на\s+месте|смирно)|не\s+двигайся|сиди\s+смирно|хватит\s+(?:гулять|ходить|прыгать)|не\s+гуляй)\b"), "autonomy_off"),
    (re.compile(r"\b(?:толкни|подвинь|сдвинь|отодвинь|пихни|подтолкни)\s+(?:\w+\s+)?окн\w*"), "push"),
    (re.compile(r"\bспрячься\b|\bпоиграй\s+(?:со\s+мной\s+)?в\s+прятки\b|\bспрячь\s+себя\b"), "hide"),
    (re.compile(r"\b(?:выгляни|выглядывай|выглянь)\b|\bвысунься\b"), "peek"),
    (re.compile(r"\b(?:кивни)\b"), "nod"),
    (re.compile(r"\b(?:помотай|покачай)\s+головой\b"), "shake"),
]


def match_pet(lower):
    for rx, name in PET_COMMANDS:
        if rx.search(lower):
            return name
    return None


# ==================== ДА / НЕТ ====================
# Отдельное «не» — тоже отказ: «не удаляй», «не отправляй», «давай не будем» раньше давали «да» (по «удаляй»/«давай»).
_RE_NO = re.compile(r"\b(?:нет|не|неа|нее|не\s+надо|не\s+нужно|не\s+стоит|отмен\w*|стоп|стой|ни\s+в\s+коем|no|"
                    r"потом|подожди|погоди|щас)\b", re.IGNORECASE)
# Без «удали»/«отправь»: повтор исходной команды («Рокси, удали текст…») — не согласие.
_RE_YES = re.compile(r"\b(?:да|ага|угу|давай|подтверждаю|конечно|ок|окей|okay|yes|верно|точно|"
                     r"отправляй|удаляй|делай|можно|разрешаю|го)\b", re.IGNORECASE)


def parse_yes_no(text):
    """True / False / None (не понятно). "Нет" побеждает: "да нет, не надо" → False."""
    if not text:
        return None
    t = text.lower().strip()
    if _RE_NO.search(t):
        return False
    if _RE_YES.search(t):
        return True
    return None


def strip_wake_word(text):
    return _RE_WAKE_PREFIX.sub("", text or "", count=1).strip()


def match_media(lower):
    for rx, action in MEDIA_COMMANDS:
        if rx.search(lower):
            return action
    return None


# ==================== ГЛАВНЫЙ РОУТЕР ====================
def get_intent(user_text, library_lookup=None, code_analyzer=None, llm_router=None):
    """Возвращает (intent, task).

    library_lookup(text) -> work|None
    code_analyzer(text) -> (intent, task)
    llm_router(text) -> (intent, task)|None — вызывается, только если ничего не сработало.
    """
    text = strip_wake_word(user_text)
    if not text:
        return "[CHAT]", ""
    if _RE_STOP.match(text): return "[ACTION_STOP]", ""
    lower = text.lower()
    sp = scenarios.parse(text)                   # расписания и сценарии — ДО power: «в 23:00 выключи компьютер» не должно выключать сейчас
    if sp: return "[ACTION_PLAN]", sp[0]
    pw = power.parse(lower)                      # до «выключи звук» и «перезагрузи» (= F5)
    if pw: return "[ACTION_POWER]", f"{pw[0]}|{pw[1]}"
    br = pcctl.parse_brightness(lower)
    if br: return "[ACTION_BRIGHTNESS]", f"{br[0]}|{br[1]}"
    sc = syscmd.parse(lower)                     # блокировка, сон, состояние ПК, что запущено, «заверши хром»
    if sc: return "[ACTION_SYS]", "|".join(sc)
    pf = phonefiles.parse(text)                  # файлы/скриншоты/буфер ↔ телефон
    if pf: return "[ACTION_PHONE]", "|".join(pf)
    md = mediacap.parse(lower)                   # камера и микрофон: «сними с веб-камеры», «что ты слышишь», «разреши камеру»
    if md: return "[ACTION_CAPTURE]", "|".join(str(x) for x in md)
    ms = pcctl.parse_mouse(lower)                # до vision-агента: «кликни» без цели — просто клик
    if ms: return "[ACTION_MOUSE]", "|".join(str(x) for x in ms)
    ks = keyboard_keys.parse_keys(lower)                  # «нажми Enter», «Ctrl+C» — после мыши («нажми правой» — клик)
    if ks: return "[ACTION_KEYS]", f"{ks[0]}|{ks[1]}"
    if any(c in lower for c in _SCREEN_PHRASES) or _RE_SCREEN.search(lower): return "[ACTION_SCREEN]", text
    if _RE_CHAT_SHORT.match(lower) and len(lower) < 40 and not _RE_CHAT_SHORT_COMMAND.search(lower):
        return "[CHAT]", ""

    # === БИБЛИОТЕКА ===
    if library_lookup and re.match(r"^\s*(?:расскажи|прочитай|скажи|озвучь|зачитай|прочти)\b", lower):
        w = library_lookup(text)
        if w:
            return "[ACTION_LIBRARY]", w.get("id", "")

    if _RE_FAST_MODE.search(lower): return "[MODE_FAST]", ""
    if _RE_ANIME_MODE.search(lower): return "[MODE_ANIME]", ""

    # === ТАЙМЕРЫ / МЕДИА ===
    if _RE_TIMER_CANCEL.search(lower): return "[ACTION_TIMER_CANCEL]", ""
    if _RE_TIMER_LIST.search(lower) and ("таймер" in lower or "напомин" in lower or "осталось" in lower):
        return "[ACTION_TIMER_LIST]", ""
    if _RE_REMIND.search(lower): return "[ACTION_REMIND]", ""
    if _RE_TIMER.search(lower): return "[ACTION_TIMER]", ""
    vol = parse_volume_command(lower)            # точная громкость, «включи/выключи звук», «что играет»
    if vol: return "[ACTION_VOLUME]", f"{vol[0]}|{'' if vol[1] is None else int(vol[1])}"
    media = match_media(lower)
    if media: return "[ACTION_MEDIA]", media
    pet = match_pet(lower)
    if pet: return "[ACTION_PET]", pet

    if _RE_MOAN.search(lower): return "[ACTION_MOAN]", ""
    if _RE_PURR.search(lower): return "[ACTION_PURR]", ""
    if _RE_WEATHER.search(lower): return "[ACTION_WEATHER]", ""
    t_hit, d_hit = bool(_RE_TIME.search(lower)), bool(_RE_DATE.search(lower))
    if t_hit or d_hit:
        return "[ACTION_TIME]", ("both" if t_hit and d_hit else "date" if d_hit else "time")

    if _RE_CLEAR_CONTEXT.search(lower): return "[ACTION_CLEAR_CONTEXT]", ""
    if _RE_CLEAR_TEXT.search(lower): return "[ACTION_CLEAR_TEXT]", ""
    if _RE_CLEAR_CURRENT_FOLDER.search(lower): return "[ACTION_CLEAR_FOLDER]", ""
    if _RE_CLEAR_FOLDER.search(lower): return "[ACTION_CLEAR_FOLDER]", ""
    if _RE_DELETE_FILE.search(lower): return "[ACTION_DELETE_FILE]", ""

    if _RE_CLOSE_TAB.match(text): return "[ACTION_CLOSE_TAB]", ""
    if _RE_CLOSE_ALL.match(text): return "[ACTION_CLOSE_ALL]", ""
    if _RE_MINIMIZE_ALL.match(text): return "[ACTION_MINIMIZE_ALL]", ""

    if _RE_SHOW_GUI.search(lower): return "[ACTION_SHOW_GUI]", ""
    if _RE_HIDE_GUI.search(lower): return "[ACTION_HIDE_GUI]", ""
    if _RE_MAXIMIZE_CURSOR.search(lower): return "[ACTION_MAXIMIZE_CURSOR]", ""
    if _RE_MAXIMIZE_FULL.search(text): return "[ACTION_MAXIMIZE_FULL]", ""
    if _RE_MAXIMIZE_FULL_SHORT.match(text): return "[ACTION_MAXIMIZE_FULL]", ""

    # === TELEGRAM ===
    tg_hint = bool(_RE_TG_HINT.search(lower))
    has_at = "@" in text
    m = _RE_TG_REPLY.match(text)
    if m and (tg_hint or has_at or m.group(1).startswith("@")): return "[ACTION_TG_REPLY]", f"{m.group(1)}|{m.group(2).strip()}"
    m = _RE_TG_SEND.match(text)
    if m and (tg_hint or has_at or m.group(1).startswith("@")): return "[ACTION_TG_SEND]", f"{m.group(1)}|{m.group(2).strip()}"
    m = _RE_TG_READ.match(text)
    if m: return "[ACTION_TG_READ]", (m.group(1) or "").strip()

    # === КОД (до "напиши <текст>" и до TG_SEND_MAYBE) ===
    if _RE_CODE_REQUEST.search(lower) and not re.search(r"\b(?:файл|папк)", lower):
        if code_analyzer:
            intent, task = code_analyzer(text)
            if intent == "[ACTION_CODE_FILE]":
                return intent, task
        return "[ACTION_CODE_FILE]", ""

    m = _RE_TG_SEND_MAYBE.match(text)
    if m:
        contact = m.group(1).strip()
        if contact.split()[0].lower() not in _TG_MAYBE_STOPWORDS:
            return "[ACTION_TG_SEND_MAYBE]", f"{contact}|{m.group(2).strip()}"
    if tg_hint: return "[ACTION_TG_HELP]", ""

    if _RE_CREATIVE.search(lower): return "[TEXT]", ""

    for rx, keys, desc in _PRIORITY_HOTKEYS:
        if rx.search(lower): return "[ACTION_HOTKEY]", f"{keys}|{desc}"

    if _RE_TYPE_CMD.search(lower) and re.search(r"(?:напиши|введи|напечатай|вставь|ставь|впиши|запиши|добавь)\s+\S", lower): return "[ACTION_TYPE]", ""
    hk_keys, hk_desc = _match_hotkey(lower)
    if hk_keys: return "[ACTION_HOTKEY]", f"{hk_keys}|{hk_desc}"
    if re.match(r"^\s*(?:откр[оыа]й|открыть|запуст[иь]|покажи|включ[иь]|вруби|старт)\b", lower): return "[ACTION_OPEN]", ""
    if any(c in lower for c in ["прочитай выделенное", "что я выделил", "прочитай текст"]): return "[ACTION_READ]", ""
    if any(c in lower for c in ["посмотри на экран", "что на экране", "объясни ошибку", "что за ошибка"]): return "[ACTION_SCREEN]", text
    if _RE_NEW_TAB.search(lower): return "[ACTION_NEW_TAB]", ""
    if _RE_CREATE.search(lower): return "[ACTION_CREATE]", ""
    if _RE_WINDOW.search(lower): return "[ACTION_WINDOW]", ""
    if _RE_RENAME.search(lower): return "[ACTION_RENAME]", ""
    if _RE_OPEN.search(lower): return "[ACTION_OPEN]", ""
    if _RE_CODE.search(lower) and code_analyzer:
        intent, task = code_analyzer(text)
        if intent == "[ACTION_CODE_FILE]":
            return intent, task
    if _RE_TYPE_CMD.search(lower): return "[ACTION_TYPE]", ""
    if _RE_VISION_TRIGGER.search(text): return "[ACTION_VISION]", text

    if llm_router:
        routed = llm_router(text)
        if routed:
            return routed
    return "[CHAT]", ""
