"""Пути, ключи, модели и конфиг. Никаких тяжёлых импортов — модуль можно грузить откуда угодно."""
import os
import json

# --- НАСТРОЙКИ ПУТЕЙ ---
# Раньше путь был захардкожен; теперь проект работает из любой папки.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPER_MODEL_PATH = os.path.join(PROJECT_DIR, "piper_voice", "ru_RU-irina-medium.onnx")
WHISPER_MODEL_PATH = os.path.join(PROJECT_DIR, "whisper_medium_model")
ESPEAK_DATA_PATH = os.path.join(PROJECT_DIR, ".venv", "Lib", "site-packages", "piper", "espeak-ng-data")
os.environ.setdefault("ESPEAK_DATA_PATH", ESPEAK_DATA_PATH)

TEMP_DIR = os.path.join(PROJECT_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)
LOG_DIR = os.path.join(PROJECT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "roxy.log")

LIBRARY_DIR = os.path.join(PROJECT_DIR, "library")
LIBRARY_TEXTS_DIR = os.path.join(LIBRARY_DIR, "texts")
LIBRARY_AUDIO_DIR = os.path.join(LIBRARY_DIR, "audio")
LIBRARY_INDEX = os.path.join(LIBRARY_DIR, "index.json")


# ==================== СЕКРЕТЫ (.env) ====================
# Ключи и токены НЕ лежат в коде. Они читаются из переменных окружения или из файла .env
# рядом с проектом (он в .gitignore). Шаблон без значений — .env.example.
# В os.environ значения из .env не кладём: так их не унаследуют запускаемые Рокси программы.
_DOTENV = {}


def _load_dotenv(path):
    """Минимальный разбор .env: KEY=VALUE, комментарии #, значения можно брать в кавычки."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                elif " #" in val:
                    val = val.split(" #", 1)[0].rstrip()
                if key:
                    _DOTENV[key] = val
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"⚠️ Не удалось прочитать .env: {e}")


_load_dotenv(os.path.join(PROJECT_DIR, ".env"))


def get_secret(name, default=""):
    """Сначала настоящая переменная окружения, потом .env."""
    return (os.environ.get(name) or _DOTENV.get(name) or default).strip()


def mask_secret(value, keep=4):
    """Для логов: 'sk-o…' вместо всего ключа."""
    value = str(value or "")
    return (value[:keep] + "…") if len(value) > keep else ("***" if value else "")


RVC_EXE = get_secret("ROXY_RVC_EXE") or os.path.join(
    os.path.expanduser("~"), "rvc_tool", "venv", "Scripts", "rvc-next.exe")
RVC_VOICE_NAME = "roxy_e660_s4620"
RVC_PITCH = "8"
RVC_PYTHON = os.path.join(os.path.dirname(RVC_EXE), "python.exe")   # python из venv rvc_tool
MOANS_DIR = os.path.join(PROJECT_DIR, "sounds", "moans")
CONFIG_FILE = os.path.join(PROJECT_DIR, "config.json")
HISTORY_FILE = os.path.join(PROJECT_DIR, "memory.json")
TG_SESSION_FILE = os.path.join(PROJECT_DIR, "roxy_session")
VOSK_MODEL_PATH = os.path.join(PROJECT_DIR, "vosk_model_ru")
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

LIVE2D_SCRIPT = os.path.join(PROJECT_DIR, "live2d_window.py")
LIVE2D_PORT = 8765
LIVE2D_URL = f"ws://127.0.0.1:{LIVE2D_PORT}"

# ==================== API ====================
OPENROUTER_API_KEY = get_secret("OPENROUTER_API_KEY")
GROQ_API_KEY = get_secret("GROQ_API_KEY")
TG_API_ID = get_secret("TG_API_ID")
TG_API_HASH = get_secret("TG_API_HASH")

# Какие обязательные ключи не заданы (main.py покажет понятное предупреждение).
MISSING_SECRETS = [n for n, v in (("OPENROUTER_API_KEY", OPENROUTER_API_KEY),
                                  ("GROQ_API_KEY", GROQ_API_KEY)) if not v]

HTTP_TIMEOUT = 15.0
CONNECT_TIMEOUT = 5.0

VISION_MODELS = [
    "qwen/qwen3.8-27b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
]
ROUTER_MODEL = "openai/gpt-oss-20b"   # tool-calling роутер для фраз, которые не поймали регулярки
FAST_MODEL = "openai/gpt-oss-20b"
CODE_MODEL = "openai/gpt-oss-120b"

# ==================== ПРИЛОЖЕНИЯ ====================
APP_DICT = {
    "блокнот": "notepad.exe", "калькулятор": "calc.exe", "проводник": "explorer.exe",
    "паинт": "mspaint.exe", "paint": "mspaint.exe", "консоль": "cmd.exe",
    "командную строку": "cmd.exe", "терминал": "wt.exe", "браузер": "start chrome",
    "хром": "start chrome", "яндекс": "start browser", "телеграм": "start tg://",
    "дискорд": "start discord://",
}

# ==================== ПАМЯТЬ ====================
MAX_CONTEXT_MESSAGES = 20      # сколько последних сообщений уходит в LLM
MAX_STORED_MESSAGES = 40       # после этого старые сжимаются в summary


# ==================== КОНФИГ ====================
# Эти ключи не пишем в config.json, если они уже заданы в .env.
_SECRET_CONFIG_KEYS = ("tg_api_id", "tg_api_hash")


def _default_config():
    return {
        "voice_mode": "anime",
        "confirm_dangerous": True,     # спрашивать "да/нет" перед удалением, отправкой в TG и т.д.
        "use_llm_router": True,        # tool-calling роутер вместо qwen 0.5b
        "rvc_worker": True,            # держать RVC загруженным в фоне (быстрее аниме-голос)
        "preload_whisper": True,       # грузить локальный Whisper заранее (при плохом интернете не ждать 10+ c)
        "pet_autonomy": True,          # Live2D сама гуляет, прыгает и садится на окна
        "send_screenshots_to_cloud": False,   # разрешить отправку скриншотов в облако без вопроса
    }


def load_config():
    defaults = _default_config()
    if not os.path.exists(CONFIG_FILE):
        return defaults
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**defaults, **json.load(f)}
    except Exception as e:
        # раньше любая ошибка молча сбрасывала настройки; теперь сохраняем битый файл и говорим об этом
        print(f"⚠️ config.json повреждён ({e}); использую значения по умолчанию")
        try:
            os.replace(CONFIG_FILE, CONFIG_FILE + ".broken")
            print("   битый файл сохранён как config.json.broken")
        except OSError:
            pass
        return defaults


def save_config(cfg):
    """Атомарная запись: сначала во временный файл, потом замена — обрыв посреди записи не портит config.json."""
    data = dict(cfg)
    if TG_API_ID and TG_API_HASH:
        for k in _SECRET_CONFIG_KEYS:
            data.pop(k, None)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, CONFIG_FILE)


def get_tg_credentials(cfg=None):
    """(api_id:int, api_hash:str). Приоритет: .env/окружение, затем старый config.json (для обратной совместимости)."""
    cfg = cfg if cfg is not None else config
    api_id = TG_API_ID or str(cfg.get("tg_api_id") or "")
    api_hash = TG_API_HASH or str(cfg.get("tg_api_hash") or "")
    try:
        api_id = int(api_id)
    except (TypeError, ValueError):
        api_id = 0
    return api_id, api_hash.strip()


def _windows_desktop():
    """Реальный рабочий стол Windows (учитывает перенаправление в OneDrive)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        # CSIDL_DESKTOPDIRECTORY = 0x10
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return None


def get_desktop_path():
    user_home = os.path.expanduser("~")
    candidates = [
        get_secret("ROXY_DESKTOP_PATH"),
        _windows_desktop(),
        os.path.join(user_home, "Desktop"),
        os.path.join(user_home, "Рабочий стол"),
        os.path.join(user_home, "OneDrive", "Desktop"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return os.path.join(user_home, "Desktop")


DESKTOP_PATH = get_desktop_path()
config = load_config()
