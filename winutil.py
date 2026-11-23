"""Windows: окна, запуск программ, папки/файлы, корзина, Проводник, Блокнот, медиаклавиши."""
import os
import re
import time
import ctypes
import shutil
import difflib
import struct
import warnings
import subprocess

warnings.filterwarnings("ignore", message="Revert to STA COM threading mode")

import pygetwindow as gw
import pyautogui
import kbfix  # noqa: F401 — Ctrl+V/C/N… работают и в русской раскладке
import pyperclip
import pywinauto

from settings import APP_DICT, DESKTOP_PATH

try:
    import win32com.client as _win32com
    _WIN32COM_OK = True
except ImportError:
    _win32com = None
    _WIN32COM_OK = False
    print("⚠️ pywin32 не установлен. Установите: python -m pip install pywin32")

try:
    import comtypes.client as _comtypes_client
    _COMTYPES_OK = True
except Exception:
    _COMTYPES_OK = False
_comtypes_broken = [False]

try:
    from send2trash import send2trash as _send2trash_impl
    _SEND2TRASH_OK = True
except ImportError:
    _send2trash_impl = None
    _SEND2TRASH_OK = False
    print("⚠️ send2trash не установлен — удаление файлов отключено. Установите: python -m pip install send2trash")

_user32 = ctypes.windll.user32

# ==================== ФОКУС ОКОН / ЗАПУСК ====================
def _force_foreground(hwnd):
    """Выводит окно на передний план и возвращает True, если оно ДЕЙСТВИТЕЛЬНО стало активным.

    Windows не даёт фоновому процессу забирать фокус (SetForegroundWindow молча не срабатывает,
    окно только мигает в панели задач) — тогда Ctrl+V улетал в активное окно, например в терминал.
    Обход: «нажать» Alt перед SetForegroundWindow + AttachThreadInput. Раньше ещё и
    ShowWindow(SW_RESTORE) вызывался всегда — и развёрнутые окна сворачивались в обычные."""
    if not hwnd:
        return False
    hwnd = int(hwnd)
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)            # SW_RESTORE — только для свёрнутых
        for _ in range(3):
            if _user32.GetForegroundWindow() == hwnd:
                return True
            fg = _user32.GetForegroundWindow()
            fg_thread = _user32.GetWindowThreadProcessId(fg, 0)
            cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            # Любой ввод от нашего процесса снимает запрет на смену фокуса. Раньше жали Alt —
            # но Alt активирует меню Блокнота, и Ctrl+V уходил в меню. 0xE8 — неназначенная клавиша.
            _user32.keybd_event(0xE8, 0, 0, 0)
            _user32.keybd_event(0xE8, 0, 0x0002, 0)
            attached = bool(fg_thread and fg_thread != cur_thread and
                            _user32.AttachThreadInput(cur_thread, fg_thread, True))
            try:
                _user32.BringWindowToTop(hwnd)
                _user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    _user32.AttachThreadInput(cur_thread, fg_thread, False)
            time.sleep(0.12)
        return _user32.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"⚠️ _force_foreground: {e}")
        return False

def _bring_to_front(title_part, timeout=3.0):
    if not title_part:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for w in gw.getAllWindows():
                if w.title and title_part.lower() in w.title.lower():
                    if w.isMinimized:
                        try: w.restore()
                        except Exception: pass
                    hwnd = getattr(w, "_hWnd", None)
                    if hwnd: _force_foreground(hwnd)
                    else:
                        try: w.activate()
                        except Exception: pass
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

# Заголовки окон известных программ. Раньше окно искалось через pywinauto connect(path=...):
# у нового Блокнота Windows 11 notepad.exe — лишь «запускалка», и поиск висел все 3-4 секунды.
_APP_TITLES = {
    "notepad.exe": ("блокнот", "notepad"),
    "calc.exe": ("калькулятор", "calculator"),
    "mspaint.exe": ("paint",),
    "explorer.exe": ("проводник", "file explorer", "главная", "home"),
    "cmd.exe": ("cmd.exe", "командная строка", "command prompt"),
    "wt.exe": ("terminal", "терминал", "powershell", "cmd.exe"),
}


def _focus_new_app(exe_name, timeout=3.0):
    if not exe_name or not exe_name.lower().endswith(".exe"):
        return False
    titles = _APP_TITLES.get(os.path.basename(exe_name).lower())
    deadline = time.time() + timeout
    if titles:
        while time.time() < deadline:
            try:
                for w in gw.getAllWindows():
                    t = (w.title or "").lower()
                    if t and any(k in t for k in titles):
                        try:
                            if w.isMinimized: w.restore()
                        except Exception: pass
                        _force_foreground(getattr(w, "_hWnd", None))
                        return True
            except Exception:
                pass
            time.sleep(0.15)
        return False
    deadline = time.time() + min(timeout, 2.0)
    while time.time() < deadline:
        try:
            app = pywinauto.Application(backend="uia").connect(path=exe_name, timeout=0.5)
            win = app.top_window()
            if win and win.exists():
                try: win.set_focus()
                except Exception: pass
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False

def _find_start_menu_shortcut(name):
    if not name: return None
    name_low = name.lower().replace(" ", "")
    search_dirs = [
        os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]
    candidates = []
    for base in search_dirs:
        if not os.path.isdir(base): continue
        for root, dirs, files in os.walk(base):
            for f in files:
                if not f.lower().endswith(".lnk"): continue
                stem = f[:-4].lower().replace(" ", "")
                if name_low in stem or stem in name_low:
                    candidates.append(os.path.join(root, f))
    if candidates:
        for c in candidates:
            stem = os.path.basename(c)[:-4].lower()
            if stem == name.lower(): return c
        return candidates[0]
    return None

def _find_exe_in_path(name):
    if not name: return None
    name_low = name.lower()
    name_low_exe = name_low if name_low.endswith(".exe") else name_low + ".exe"
    for exe in (name_low, name_low_exe):
        p = shutil.which(exe)
        if p: return p
    return None

def _launch_by_name(name):
    if not name: return False, "пустое имя"
    name_low = name.lower().strip()

    if name_low in APP_DICT:
        exe = APP_DICT[name_low]
        try:
            subprocess.Popen(exe, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if exe.endswith(".exe") and " " not in exe:
                _focus_new_app(exe, timeout=3.0)
            else:
                _bring_to_front(name, timeout=3.0)
            return True, f"APP_DICT ({exe})"
        except Exception as e:
            print(f"⚠️ APP_DICT launch: {e}")

    exe = _find_exe_in_path(name_low)
    if exe:
        try:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _focus_new_app(os.path.basename(exe), timeout=3.0)
            return True, f"which ({exe})"
        except Exception as e:
            print(f"⚠️ which launch: {e}")

    lnk = _find_start_menu_shortcut(name_low)
    if lnk:
        try:
            os.startfile(lnk)
            time.sleep(1.0)
            _bring_to_front(name, timeout=3.0)
            return True, f"StartMenu ({os.path.basename(lnk)})"
        except Exception as e:
            print(f"⚠️ StartMenu launch: {e}")
    return False, "не найдено"

def _press_win_d():
    VK_LWIN = 0x5B
    VK_D = 0x44
    KEYUP = 0x0002
    try:
        _user32.keybd_event(VK_LWIN, 0, 0, 0)
        time.sleep(0.02)
        _user32.keybd_event(VK_D, 0, 0, 0)
        time.sleep(0.02)
        _user32.keybd_event(VK_D, 0, KEYUP, 0)
        time.sleep(0.02)
        _user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
        return True
    except Exception as e:
        print(f"❌ Win+D: {e}")
        return False

def _press_win(vk):
    VK_LWIN = 0x5B
    KEYUP = 0x0002
    try:
        _user32.keybd_event(VK_LWIN, 0, 0, 0)
        time.sleep(0.02)
        _user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
        _user32.keybd_event(vk, 0, KEYUP, 0)
        time.sleep(0.02)
        _user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
        return True
    except Exception: return False


# ==================== ХОТКЕИ ====================
def _press_hotkey(keys):
    if keys == "win+d":
        if _press_win_d(): return True
    if keys == "win+r": return _press_win(0x52)
    if keys == "win+l": return _press_win(0x4C)
    if keys == "win+i": return _press_win(0x49)
    if keys == "win+v": return _press_win(0x56)
    if keys == "win+e":
        try:
            subprocess.Popen("explorer.exe", shell=True)
            return True
        except Exception: pass
    if keys == "win+.":
        try:
            _user32.keybd_event(0x5B, 0, 0, 0)
            pyautogui.press('.')
            _user32.keybd_event(0x5B, 0, 2, 0)
            return True
        except Exception: pass
    try:
        parts = [p.strip() for p in keys.split("+") if p.strip()]
        if len(parts) == 1: pyautogui.press(parts[0])
        else: pyautogui.hotkey(*parts)
        return True
    except Exception as e:
        print(f"❌ Hotkey {keys}: {e}")
        return False


# ==================== ПАПКИ И ФАЙЛЫ ====================
_KNOWN_FOLDER_GUIDS = {
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74DE8AB4}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Profile": "{5E6C858F-0E22-4760-9AFE-EA3317B67173}",
}

_LAT_TO_CYR = {
    'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е', 'z': 'з',
    'i': 'и', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п',
    'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'f': 'ф', 'h': 'х', 'y': 'ы',
    'c': 'ц', 'j': 'дж', 'q': 'к', 'w': 'в', 'x': 'кс',
}

def _translit_lat_to_cyr(s):
    return ''.join(_LAT_TO_CYR.get(c.lower(), c) for c in s) if s else s

def get_known_folder(name):
    guid_str = _KNOWN_FOLDER_GUIDS.get(name)
    if not guid_str: return None
    try:
        import uuid as _uuid
        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8),
            ]
        u = _uuid.UUID(guid_str)
        g = _GUID()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        for i, b in enumerate(u.bytes[8:]): g.Data4[i] = b
        path_ptr = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(g), 0, None, ctypes.byref(path_ptr)
        )
        if res == 0 and path_ptr.value:
            path = path_ptr.value
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return path
    except Exception as e:
        print(f"⚠️ get_known_folder({name}): {e}")
    return None

def _safe_known(name):
    try: return get_known_folder(name)
    except Exception: return None

def _find_folder(name):
    if not name: return None
    if os.path.isabs(name) and os.path.isdir(name): return name
    name_low = name.lower()
    sys_shortcuts = {
        "рабочий стол": DESKTOP_PATH, "стол": DESKTOP_PATH,
        "документы": _safe_known("Documents") or os.path.expanduser("~/Documents"),
        "мои документы": _safe_known("Documents") or os.path.expanduser("~/Documents"),
        "загрузки": _safe_known("Downloads") or os.path.expanduser("~/Downloads"),
        "скачивания": _safe_known("Downloads") or os.path.expanduser("~/Downloads"),
        "музыка": _safe_known("Music") or os.path.expanduser("~/Music"),
        "музыку": _safe_known("Music") or os.path.expanduser("~/Music"),
        "видео": _safe_known("Videos") or os.path.expanduser("~/Videos"),
        "картинки": _safe_known("Pictures") or os.path.expanduser("~/Pictures"),
        "изображения": _safe_known("Pictures") or os.path.expanduser("~/Pictures"),
        "домашняя": os.path.expanduser("~"), "домашнюю": os.path.expanduser("~"),
        "профиль": os.path.expanduser("~"),
    }
    if name_low in sys_shortcuts:
        p = sys_shortcuts[name_low]
        if p and os.path.isdir(p): return p
    for base in [
        DESKTOP_PATH, _safe_known("Documents") or os.path.expanduser("~/Documents"),
        _safe_known("Downloads") or os.path.expanduser("~/Downloads"), os.path.expanduser("~"),
    ]:
        if not base or not os.path.isdir(base): continue
        cand = os.path.join(base, name)
        if os.path.isdir(cand): return cand
        try:
            for entry in os.listdir(base):
                if entry.lower() == name_low:
                    full = os.path.join(base, entry)
                    if os.path.isdir(full): return full
        except OSError: continue
    return None

def _find_file(name):
    if not name: return None
    name = name.strip(" .,!?«»\"'")
    if not name: return None
    name = re.sub(r"\s+\.", ".", name)
    if os.path.isfile(name): return name
    if os.path.isabs(name) and os.path.isfile(name): return name
    name_low = name.lower()
    candidates = [name]
    if "." not in name:
        candidates.extend([name + ".txt", name + ".docx", name + ".py", name + ".md", name + ".rtf"])
    search_dirs = []
    try:
        cur = get_active_explorer_path()
        if cur and os.path.isdir(cur): search_dirs.append(cur)
    except Exception: pass
    search_dirs.extend([
        DESKTOP_PATH, _safe_known("Documents") or os.path.expanduser("~/Documents"),
        _safe_known("Downloads") or os.path.expanduser("~/Downloads"), os.path.expanduser("~"),
    ])
    for base in search_dirs:
        if not base or not os.path.isdir(base): continue
        for c in candidates:
            p = os.path.join(base, c)
            if os.path.isfile(p): return p
        try:
            for entry in os.listdir(base):
                if entry.lower() == name_low:
                    full = os.path.join(base, entry)
                    if os.path.isfile(full): return full
        except OSError: continue
    variants = [name_low]
    trans = _translit_lat_to_cyr(name).lower()
    if trans and trans != name_low: variants.append(trans)
    for v in list(variants):
        if not v.endswith("ь"): variants.append(v + "ь")
    all_files = {}
    for base in search_dirs:
        if not base or not os.path.isdir(base): continue
        try:
            for entry in os.listdir(base):
                full = os.path.join(base, entry)
                if os.path.isfile(full): all_files[entry.lower()] = full
        except OSError: continue
    if all_files:
        all_lower = list(all_files.keys())
        for v in variants:
            matches = difflib.get_close_matches(v, all_lower, n=1, cutoff=0.75)
            if matches:
                p = all_files[matches[0]]
                print(f"🔎 Fuzzy-нашла '{os.path.basename(p)}' вместо '{name}'")
                return p
    return None

def _open_path(path):
    try: os.startfile(path)
    except Exception as e:
        print(f"⚠️ os.startfile({path}): {e}")
        return False
    base = os.path.basename(str(path).rstrip("\\/"))
    if base and _bring_to_front(base, timeout=3.0):
        print(f"🔝 Окно '{base}' выведено на передний план.")
    return True

def _send_to_trash(path):
    """Только в корзину. Раньше без send2trash файлы удалялись НАВСЕГДА через shutil.rmtree."""
    if not path or not os.path.exists(path):
        return False
    if not _SEND2TRASH_OK:
        print("❌ send2trash не установлен — не удаляю (иначе удаление было бы безвозвратным).")
        return False
    try:
        _send2trash_impl(path)
        return True
    except Exception as e:
        print(f"❌ Ошибка send2trash: {e}")
        return False


def _clear_folder_contents(folder):
    if not folder or not os.path.isdir(folder): return 0, 0
    deleted = failed = 0
    try: entries = os.listdir(folder)
    except OSError as e:
        print(f"⚠️ Не могу прочитать папку: {e}")
        return 0, 0
    for entry in entries:
        full = os.path.join(folder, entry)
        if _send_to_trash(full): deleted += 1
        else: failed += 1
    return deleted, failed

def count_folder_items(folder):
    try:
        return len(os.listdir(folder))
    except OSError:
        return 0


# ==================== ПРОВОДНИК ====================
def _window_class(hwnd):
    try:
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(int(hwnd), buf, 256)
        return buf.value
    except Exception:
        return ""


def is_explorer_window(hwnd):
    return _window_class(hwnd) in ("CabinetWClass", "ExploreWClass")


def _list_explorer_windows():
    def collect(shell):
        res = []
        windows = shell.Windows()
        try: count = int(windows.Count)
        except Exception: count = 0
        for i in range(count):
            try:
                w = windows.Item(i)
                path = w.Document.Folder.Self.Path
                if path and os.path.isdir(path):
                    res.append({"hwnd": int(w.HWND), "path": path})
            except Exception:
                continue
        return res

    if _WIN32COM_OK:
        try:
            return collect(_win32com.Dispatch("Shell.Application"))
        except Exception as e:
            print(f"⚠️ win32com: {e}")
    if _COMTYPES_OK and not _comtypes_broken[0]:
        try:
            try: _comtypes_client.GetModule("shell32.dll")
            except Exception: pass
            return collect(_comtypes_client.CreateObject("Shell.Application"))
        except Exception as e:
            print(f"⚠️ comtypes: {e}")
            _comtypes_broken[0] = True
    return []


def get_active_explorer_path(require_active=False):
    """require_active=True — только если Проводник СЕЙЧАС активное окно (для удаления/очистки).
    Раньше при неактивном Проводнике возвращалось «последнее открытое окно» — и очистка
    могла уйти в случайную папку."""
    active = gw.getActiveWindow()
    active_hwnd = int(getattr(active, "_hWnd", 0) or 0) if active else 0
    windows = _list_explorer_windows()
    for w in windows:
        if active_hwnd and w["hwnd"] == active_hwnd:
            print(f"📂 Активное окно Проводника: {w['path']}")
            return w["path"]
    if require_active:
        return None
    if windows:
        print(f"📂 Открытое окно Проводника: {windows[-1]['path']}")
        return windows[-1]["path"]
    # Ctrl+L / Ctrl+C — только в настоящем Проводнике (раньше жалось в любое активное окно)
    if not active_hwnd or not is_explorer_window(active_hwnd):
        return None
    old_clip = ""
    try: old_clip = pyperclip.paste() or ""
    except Exception: pass
    try:
        pyautogui.hotkey("ctrl", "l"); time.sleep(0.3)
        pyautogui.hotkey("ctrl", "c"); time.sleep(0.3)
        pyautogui.press("escape")
        path = (pyperclip.paste() or "").strip()
        if path and os.path.isdir(path):
            return path
    except Exception as e:
        print(f"⚠️ Fallback Ctrl+L: {e}")
    finally:
        set_clipboard_private(old_clip)
    return None


# ==================== БЛОКНОТ ====================
_K32 = None


def _window_process_name(hwnd):
    """Имя exe-файла, которому принадлежит окно (notepad.exe), или None, если узнать не удалось."""
    global _K32
    try:
        from ctypes import wintypes
        if _K32 is None:
            # свой экземпляр kernel32: argtypes не должны менять глобальный ctypes.windll для остального кода
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
            _K32 = k
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        h = _K32.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if _K32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            _K32.CloseHandle(h)
    except Exception:
        pass
    return None


def _find_notepad_window():
    """Окно Блокнота. Раньше подходило ЛЮБОЕ окно со словом «блокнот»/«notepad» в заголовке
    (вкладка браузера «Блокнот — Google Keep», Notepad++ и т.д.) и Рокси печатала не туда.
    Теперь предпочитаем окно процесса notepad.exe; если процесс определить не вышло — как раньше, по заголовку."""
    fallback = None
    for w in gw.getAllWindows():
        t = (w.title or "").lower()
        if "блокнот" in t or "notepad" in t:
            proc = _window_process_name(getattr(w, "_hWnd", 0))
            if proc is None:
                fallback = fallback or w
            elif proc == "notepad.exe":
                return w
    return fallback

def _click_into_notepad_text(win):
    try:
        app = pywinauto.Application(backend="uia").connect(handle=win._hWnd, timeout=2)
        window = app.window(handle=win._hWnd)
        for ct in ("Document", "Edit", "TextEditor"):
            try:
                ctrl = window.child_window(control_type=ct)
                if ctrl.exists():
                    rect = ctrl.rectangle()
                    pyautogui.click(rect.left + (rect.right - rect.left) // 2, rect.top + (rect.bottom - rect.top) // 2)
                    time.sleep(0.15)
                    return True
            except Exception: continue
    except Exception: pass
    try:
        pyautogui.click(win.left + win.width // 2, win.top + int(win.height * 0.6))
        time.sleep(0.15)
        return True
    except Exception: return False

def _clear_notepad():
    win = _find_notepad_window()
    if not win: return False, "Блокнот не найден"
    try:
        if win.isMinimized: win.restore()
        _force_foreground(win._hWnd)
        time.sleep(0.25)
    except Exception: pass
    _click_into_notepad_text(win)
    try:
        app = pywinauto.Application(backend="uia").connect(handle=win._hWnd, timeout=2)
        window = app.window(handle=win._hWnd)
        for ct in ("Document", "Edit", "TextEditor"):
            try:
                ctrl = window.child_window(control_type=ct)
                if ctrl.exists():
                    ctrl.iface_value.SetValue("")
                    return True, f"UIA {ct}"
            except Exception: continue
    except Exception: pass
    try:
        _force_foreground(win._hWnd)
        _click_into_notepad_text(win)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.15)
        pyautogui.press('delete')
        time.sleep(0.2)
        return True, "Ctrl+A+Del"
    except Exception: return False, "все методы провалились"

def _notepad_edit(win):
    """UIA-элемент текста АКТИВНОЙ вкладки Блокнота или None.
    Раньше брался «первый Document»: при нескольких вкладках это могла быть не та вкладка
    (или поиск падал из-за неоднозначности и находилось другое поле)."""
    try:
        app = pywinauto.Application(backend="uia").connect(handle=win._hWnd, timeout=2)
        window = app.window(handle=win._hWnd)
        docs = []
        for ct in ("Document", "Edit"):
            try:
                docs = [d for d in window.descendants(control_type=ct) if d.is_visible()]
            except Exception:
                docs = []
            if docs:
                break
        if not docs:
            return None
        for d in docs:
            try:
                if d.has_keyboard_focus():
                    return d
            except Exception:
                pass
        return max(docs, key=lambda d: d.rectangle().width() * d.rectangle().height())   # самое большое поле = текст
    except Exception:
        return None


def _notepad_text(win):
    """Текущий текст Блокнота или None, если не удалось прочитать."""
    ctrl = _notepad_edit(win)
    if ctrl is None:
        return None
    try:
        return str(ctrl.iface_value.CurrentValue or "")
    except Exception:
        return None


def _open_new_notepad(timeout=4.0):
    subprocess.Popen("notepad.exe")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        a = gw.getActiveWindow()
        t = (getattr(a, "title", "") or "").lower()
        if a and ("блокнот" in t or "notepad" in t):
            return a
    return _find_notepad_window()


def set_text_if_empty(win, text):
    """Пишет текст через UIA, ТОЛЬКО если активная вкладка пустая. Проверяет результат."""
    if win is None or _notepad_text(win) != "":
        return False
    ctrl = _notepad_edit(win)
    try:
        ctrl.iface_value.SetValue(text)
    except Exception as e:
        print(f"⚠️ UIA SetValue: {e}")
        return False
    ok = re.sub(r"\s+", "", _notepad_text(win) or "")[:40] == re.sub(r"\s+", "", text)[:40]
    if ok:
        print("✅ Текст записан в пустую вкладку (UIA)")
    return ok


def open_file_in_notepad(path, timeout=6.0):
    """Самый надёжный способ показать код: файл открывается Блокнотом, без буфера и гонок с фокусом."""
    try:
        subprocess.Popen(["notepad.exe", path])
    except Exception as e:
        print(f"❌ notepad.exe: {e}")
        return False
    name = os.path.splitext(os.path.basename(path))[0].lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in gw.getAllWindows():
            if name in (w.title or "").lower():
                _force_foreground(getattr(w, "_hWnd", None))
                return True
        time.sleep(0.2)
    return False


def set_clipboard_private(text):
    """Кладёт текст в буфер так, чтобы он НЕ попадал в журнал буфера (Win+V) и в облако.
    Служебные форматы Windows: ExcludeClipboardContentFromMonitorProcessing,
    CanIncludeInClipboardHistory=0, CanUploadToCloudClipboard=0."""
    try:
        import win32clipboard
        import win32con
        for _ in range(10):
            try:
                win32clipboard.OpenClipboard()
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError("буфер занят")
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            zero = struct.pack("<I", 0)
            win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("ExcludeClipboardContentFromMonitorProcessing"), zero)
            win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("CanIncludeInClipboardHistory"), zero)
            win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("CanUploadToCloudClipboard"), zero)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        print(f"⚠️ буфер (private): {e} — обычное копирование")
        pyperclip.copy(text)
        return False


_clip_before_paste = [""]


def _paste(text, restore_delay=1.0, restore_old=True):
    """Раньше старый буфер возвращался через 0.4 c — новая вкладка Блокнота не успевала
    обработать Ctrl+V и вставляла уже ВОССТАНОВЛЕННЫЙ (часто пустой) буфер."""
    old = ""
    try: old = pyperclip.paste() or ""
    except Exception: pass
    _clip_before_paste[0] = old
    set_clipboard_private(text)
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(restore_delay)
    if restore_old:
        set_clipboard_private(old)


def _wait_notepad_ready(win, timeout=4.0):
    """Ждёт, пока новая вкладка/окно Блокнота станет активным и в нём появится поле текста."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        a = gw.getActiveWindow()
        if a is not None and getattr(a, "_hWnd", None) == getattr(win, "_hWnd", None) and _notepad_edit(win) is not None:
            time.sleep(0.4)
            return True
        _force_foreground(getattr(win, "_hWnd", None))
        time.sleep(0.2)
    return False


def insert_into_notepad(text, fresh=False):
    """Вставляет текст в позицию курсора (раньше ВЕСЬ текст Блокнота заменялся).
    fresh=True (для кода): если в Блокноте уже что-то есть — открывает новый документ.
    После вставки проверяет через UIA, что текст действительно появился."""
    win = _find_notepad_window()
    opened = False
    if win is None:
        win, opened = _open_new_notepad(), True
    elif fresh:
        current = _notepad_text(win)
        if current is None or current.strip():
            win, opened = _open_new_notepad(), True
    if win is None:
        return False, "Блокнот не найден"
    try:
        if win.isMinimized:
            win.restore()
        _force_foreground(win._hWnd)
        if opened and not _wait_notepad_ready(win):
            print("⚠️ Блокнот долго открывается")
        before = _notepad_text(win)

        # Новый ПУСТОЙ документ — можно записать напрямую через UIA (без буфера и без гонок)
        if opened and before == "":
            ctrl = _notepad_edit(win)
            try:
                ctrl.iface_value.SetValue(text)
                if (_notepad_text(win) or "").strip():
                    print("✅ insert_into_notepad: UIA")
                    return True, "UIA"
            except Exception as e:
                print(f"⚠️ UIA SetValue: {e}")

        norm = lambda t: re.sub(r"\s+", "", t or "")      # RichEdit отдаёт переносы как \r
        snippet = norm(text)[:40]
        # Ctrl+V жмём ТОЛЬКО если Блокнот реально активен — иначе текст ушёл бы в чужое окно
        if not _force_foreground(win._hWnd):
            print("⚠️ Не получилось сделать Блокнот активным — не вставляю вслепую.")
            return False, "нет фокуса"
        time.sleep(0.2)
        # Вставляем РОВНО ОДИН раз. Раньше при неудачной проверке вставка повторялась —
        # а проверка могла ошибаться, и текст оказывался в Блокноте дважды.
        _paste(text, restore_old=False)
        after = _notepad_text(win)
        grew = before is not None and after is not None and len(norm(after)) - len(norm(before)) >= len(norm(text)) * 0.8
        if after is None or snippet in norm(after) or grew:
            set_clipboard_private(_clip_before_paste[0])     # проверено — возвращаем старый буфер
            print("✅ insert_into_notepad: Clipboard")
            return True, "Clipboard"
        # Не смогли подтвердить: текст оставляем в буфере, чтобы его можно было вставить руками
        print(f"⚠️ Не смогла проверить вставку (было {len(norm(before or ''))} симв., стало {len(norm(after or ''))}). "
              f"Текст оставила в буфере.")
        return True, "unverified"
    except Exception as e:
        print(f"⚠️ insert_into_notepad: {e}")
        return False, str(e)


# Старое имя — для совместимости
_set_notepad_text = insert_into_notepad


# ==================== ОКНА ====================
def _find_window_by_title(title):
    if not title: return None
    ws = gw.getWindowsWithTitle(title)
    if not ws: ws = [w for w in gw.getAllWindows() if title.lower() in (w.title or "").lower()]
    return ws[0] if ws else None

def _safe_maximize(win):
    if win is None: return False
    try:
        if win.isMinimized:
            win.restore()
            time.sleep(0.2)
    except Exception: pass
    hwnd = getattr(win, "_hWnd", None)
    if hwnd:
        try:
            app = pywinauto.Application(backend="uia").connect(handle=hwnd, timeout=2)
            w = app.window(handle=hwnd)
            w.maximize()
            try: w.set_focus()
            except Exception: pass
            return True
        except Exception as e: print(f"⚠️ pywinauto max: {e}")
    try:
        win.maximize()
        try: win.activate()
        except Exception: pass
        return True
    except Exception as e: print(f"⚠️ maximize: {e}")
    return False

def _close_all_windows():
    closed, failed = [], []
    skip = ("roxy console", "программный менеджер", "program manager", "панель задач", "taskbar", "пуск", "start", "roxy live2d")
    for w in gw.getAllWindows():
        t = (w.title or "").strip()
        if not t or any(s in t.lower() for s in skip): continue
        try:
            w.close()
            closed.append(t)
            time.sleep(0.15)
        except Exception as e: failed.append(f"{t}: {e}")
    return closed, failed

# ==================== МЕДИАКЛАВИШИ ====================
_MEDIA_VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
             "play_pause": 0xB3, "next": 0xB0, "prev": 0xB1}


def media_key(action, times=1):
    vk = _MEDIA_VK.get(action)
    if vk is None:
        return False
    try:
        for _ in range(times):
            _user32.keybd_event(vk, 0, 0, 0)
            _user32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.02)
        return True
    except Exception as e:
        print(f"❌ media_key: {e}")
        return False
