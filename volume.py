"""Громкость Windows (общая, «Динамики») и что сейчас играет.

Через Core Audio (IAudioEndpointVolume) на comtypes — точный уровень 0–100 % и отдельно «выключить/включить звук».
Если COM недоступен — запасной путь мультимедиа-клавишами (шаг Windows ≈ 2 %).
"""
import ctypes
import re

try:
    from ctypes import HRESULT, POINTER, c_float, c_uint, c_void_p      # HRESULT есть только в Windows
    from ctypes.wintypes import BOOL, DWORD
    import comtypes
    from comtypes import COMMETHOD, GUID, IUnknown
    _HAVE_COM = True
except Exception:                       # не Windows / нет comtypes — работаем клавишами
    _HAVE_COM = False

if _HAVE_COM:
    class IAudioEndpointVolume(IUnknown):
        _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify", (["in"], c_void_p, "pNotify")),
            COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint), "pnChannelCount")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel", (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar", (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel", (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar", (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel", (["in"], c_uint, "n"), (["in"], c_float, "db"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar", (["in"], c_uint, "n"), (["in"], c_float, "lvl"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel", (["in"], c_uint, "n"), (["out"], POINTER(c_float), "db")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar", (["in"], c_uint, "n"), (["out"], POINTER(c_float), "lvl")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMute")),
            COMMETHOD([], HRESULT, "GetVolumeStepInfo", (["out"], POINTER(c_uint), "step"), (["out"], POINTER(c_uint), "count")),
            COMMETHOD([], HRESULT, "VolumeStepUp", (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "VolumeStepDown", (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "QueryHardwareSupport", (["out"], POINTER(DWORD), "mask")),
            COMMETHOD([], HRESULT, "GetVolumeRange", (["out"], POINTER(c_float), "mn"), (["out"], POINTER(c_float), "mx"),
                      (["out"], POINTER(c_float), "inc")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "ctx"),
                      (["in"], c_void_p, "params"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_uint, "flow"), (["in"], DWORD, "mask"),
                      (["out"], POINTER(c_void_p), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_uint, "flow"), (["in"], c_uint, "role"),
                      (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        ]

    _CLSID_ENUM = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


def _endpoint():
    """Интерфейс громкости устройства вывода по умолчанию (или None)."""
    if not _HAVE_COM:
        return None
    try:
        try:
            comtypes.CoInitialize()             # действия выполняются в разных потоках
        except OSError:
            pass
        enum = comtypes.CoCreateInstance(_CLSID_ENUM, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
        dev = enum.GetDefaultAudioEndpoint(0, 1)          # eRender, eMultimedia
        unk = dev.Activate(ctypes.byref(IAudioEndpointVolume._iid_), comtypes.CLSCTX_ALL, None)
        return unk.QueryInterface(IAudioEndpointVolume)
    except Exception as e:
        print(f"⚠ громкость через Core Audio недоступна: {e}")
        return None


def get_volume():
    """(уровень 0–100, выключен ли звук) или None."""
    ep = _endpoint()
    if ep is None:
        return None
    try:
        return round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())
    except Exception as e:
        print(f"⚠ get_volume: {e}")
        return None


def _keys(action, times):
    from winutil import media_key
    return media_key(action, times)


def set_volume(level):
    level = max(0, min(100, int(round(level))))
    ep = _endpoint()
    if ep is not None:
        try:
            ep.SetMasterVolumeLevelScalar(level / 100.0, None)
            if level > 0 and ep.GetMute():
                ep.SetMute(False, None)
            return level
        except Exception as e:
            print(f"⚠ set_volume: {e}")
    # запасной путь: в ноль клавишами, потом вверх (шаг Windows — 2 %)
    if _keys("volume_down", 50) and (level == 0 or _keys("volume_up", level // 2)):
        return level
    return None


def change_volume(delta):
    cur = get_volume()
    if cur is None:
        return set_volume_relative_keys(delta)
    return set_volume(cur[0] + delta)


def set_volume_relative_keys(delta):
    n = max(1, abs(int(delta)) // 2)
    return "" if _keys("volume_up" if delta > 0 else "volume_down", n) else None


def set_mute(on):
    ep = _endpoint()
    if ep is not None:
        try:
            ep.SetMute(bool(on), None)
            return True
        except Exception as e:
            print(f"⚠ set_mute: {e}")
    return _keys("mute", 1)                 # без COM — только переключение


# ---------- разбор фразы ----------
_UNITS = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
          "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
          "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
          "восемнадцать": 18, "девятнадцать": 19}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
         "восемьдесят": 80, "девяносто": 90, "сто": 100}
_RE_NUM = re.compile(r"\b(\d{1,3})\s*(?:%|процент\w*)?")


def parse_number(text):
    """Первое число 0–100 в тексте: цифрами («30», «30%») или словами («тридцать пять»)."""
    m = _RE_NUM.search(text)
    if m:
        return min(100, int(m.group(1)))
    words = re.findall(r"[а-яё]+", text.lower())
    for i, w in enumerate(words):
        if w in _TENS:
            v = _TENS[w]
            if v < 100 and i + 1 < len(words) and words[i + 1] in _UNITS and _UNITS[words[i + 1]] < 10:
                v += _UNITS[words[i + 1]]
            return v
        if w in _UNITS:
            return _UNITS[w]
    if re.search(r"\bполовин\w*", text):
        return 50
    return None


_RE_MAX = re.compile(r"\bна\s+(?:максимум|полную|всю)\b|\bмаксимальн\w+\s+громкост")
_RE_MIN = re.compile(r"\bна\s+минимум\b|\bминимальн\w+\s+громкост")
_RE_LITTLE = re.compile(r"\b(?:чуть|чуточку|немного|немножко|слегка|капельку)\b")
_RE_MUCH = re.compile(r"\b(?:намного|гораздо|сильно|сильнее|значительно|очень)\b")

# «громкость 30», «поставь звук на 40», «сделай громкость пятьдесят процентов», «громкость на максимум»
RE_VOLUME_SET = re.compile(r"\b(?:громкость\w*|звук\w*)\b.*?(?:\d|процент|максимум|минимум|полную|половин|"
                           + "|".join(list(_TENS) + [u for u in _UNITS if len(u) > 3]) + r")")
RE_VOLUME_QUERY = re.compile(r"\b(?:какая|какой|сколько)\s+(?:сейчас\s+)?(?:громкост\w*|звук\w*)|\bгромкость\s+сейчас\b")
RE_UNMUTE = re.compile(r"\b(?:включи|верни)\s+звук\b")
RE_MUTE = re.compile(r"\b(?:выключи|отключи)\s+звук\b|\bбез\s+звука\b|\bзамуть\b|\bзаглуши\b")
RE_NOW_PLAYING = re.compile(r"\bчто\s+(?:сейчас\s+)?(?:играет|звучит|за\s+(?:песн\w*|трек|музык\w*))\b"
                            r"|\bкакой\s+(?:сейчас\s+)?(?:трек|песн\w*)\b|\bкак\s+называется\s+(?:песн\w*|трек)\b")
# «громче/тише» — только командой в начале фразы («сделай чуть потише», «ещё громче на 20») или со словом
# «звук/громкость»: иначе болтовня вроде «тише едешь — дальше будешь» крутила бы громкость
_PRE = r"^(?:(?:сделай|ну|давай|пожалуйста|ещё|еще)\s+)*(?:(?:чуть|чуточку|немного|немножко|слегка|намного|гораздо|сильно|очень)\s+)?"
RE_LOUDER = re.compile(_PRE + r"(?:по)?громче\b|\b(?:прибавь|увеличь|подними)\s+(?:\w+\s+)?(?:звук|громкость)|\bзвук\w*\s+(?:по)?громче\b")
RE_QUIETER = re.compile(_PRE + r"(?:по)?тише\b|\b(?:убавь|уменьши|понизь)\s+(?:\w+\s+)?(?:звук|громкость)|\bзвук\w*\s+(?:по)?тише\b")


def parse_volume_command(lower):
    """→ ('set', N) | ('change', ±N) | ('query', None) | ('mute', bool) | ('playing', None) | None"""
    if RE_NOW_PLAYING.search(lower):
        return "playing", None
    if RE_VOLUME_QUERY.search(lower):
        return "query", None
    if RE_UNMUTE.search(lower):
        return "mute", False
    if RE_MUTE.search(lower):
        return "mute", True
    louder, quieter = RE_LOUDER.search(lower), RE_QUIETER.search(lower)
    if louder or quieter:
        n = parse_number(lower)
        step = n if n is not None else (5 if _RE_LITTLE.search(lower) else 25 if _RE_MUCH.search(lower) else 10)
        return "change", (step if louder else -step)
    if RE_VOLUME_SET.search(lower):
        if _RE_MAX.search(lower):
            return "set", 100
        if _RE_MIN.search(lower):
            return "set", 5
        n = parse_number(lower)
        if n is not None:
            return "set", n
    return None


# ---------- что играет ----------
_PLAYER_HINTS = (
    (re.compile(r"^(.+?)\s+-\s+YouTube(?:\s+Music)?\b", re.I), "YouTube"),
    (re.compile(r"^(.+?)\s+[|—-]\s+(?:Яндекс\s+Музыка|Yandex\s+Music)", re.I), "Яндекс Музыка"),
    (re.compile(r"^(.+?)\s+[|—-]\s+(?:VK\s+Музыка|ВКонтакте|VK)\b", re.I), "VK"),
    (re.compile(r"^(.+?)\s+[|—-]\s+SoundCloud", re.I), "SoundCloud"),
)


def now_playing(titles=None):
    """Название трека по заголовкам окон: Spotify (заголовок «Исполнитель - Трек») или вкладка браузера с плеером."""
    if titles is None:
        try:
            import pygetwindow as gw
            wins = gw.getAllWindows()
            titles = [(w.title or "").strip() for w in wins]
        except Exception:
            return None
    for t in titles:
        if not t:
            continue
        for rx, src in _PLAYER_HINTS:
            m = rx.search(t)
            if m:
                name = re.sub(r"^\(\d+\)\s*", "", m.group(1)).strip()      # «(3) Песня - YouTube» — счётчик уведомлений
                return f"{name} ({src})"
    try:                                     # Spotify: окно процесса Spotify.exe, заголовок «Исполнитель - Трек»
        import psutil
        import win32gui, win32process
        found = []

        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h)
            if " - " not in t:
                return
            try:
                pid = win32process.GetWindowThreadProcessId(h)[1]
                if psutil.Process(pid).name().lower() == "spotify.exe":
                    found.append(t)
            except Exception:
                pass
        win32gui.EnumWindows(cb, None)
        if found:
            return f"{found[0]} (Spotify)"
    except Exception:
        pass
    return None
