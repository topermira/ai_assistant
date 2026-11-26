import sys
import os
import json
import time
import random
import ctypes
import asyncio
import socket
import threading
from ctypes import wintypes

import websockets
from websockets.exceptions import ConnectionClosed
from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QPoint, QEvent
from PySide6.QtGui import QRegion, QCursor, QGuiApplication, QIcon, QPixmap, QPainter, QColor, QFont, QAction
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QMenu, QWidget, QLineEdit, QHBoxLayout, QPushButton
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage

_DEBUG = bool(os.environ.get("ROXY_DEBUG"))      # подробные служебные строки — только для отладки


class _Page(QWebEnginePage):
    """Печатает console.log / ошибки JavaScript в консоль main.py (раньше Qt их молча глотал)."""
    _seen = {}

    def javaScriptConsoleMessage(self, level, message, line, source):
        key = (message, line)
        n = self._seen.get(key, 0)
        self._seen[key] = n + 1
        # обычные console.log (шляпа, «файла кружева нет» и т.п.) — только в режиме отладки; предупреждения и ошибки показываем всегда
        if n < 3 and (_DEBUG or level != QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel
                      or message.startswith(("⚠", "❌"))):
            print(f"[JS:{line}] {message}", flush=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from settings import load_config
except Exception:
    load_config = lambda: {}
try:
    import keyboard          # глобальные клавиши: Num * и Num /
except Exception:
    keyboard = None

PORT = 8765
ALLOWED_EMOTIONS = {"neutral", "happy", "sad", "angry", "surprised", "shy", "smug"}
BEHAVIORS = {"jump", "perch", "floor", "come", "walk", "nod", "shake", "autonomy_on", "autonomy_off",
             "dance", "spin", "bow", "stretch", "yawn", "look_around", "lean", "magic", "peek", "hide", "push",
             "magic_orb", "magic_ice", "magic_rain", "magic_fountain", "staff_lean", "staff_twirl",
             "dance_music", "dance_stop", "flip", "handstand", "cartwheel"}
PERCH_SINK = 0.36     # сидит: ноги спрятаны «за край окна», на краю — юбка; на окне «сидит»: низ модели (ноги, юбка) свешивается ниже края окна на 22% роста
PERCH_SINK_MAX = 0.72  # если окно стоит высоко — садится «глубже» (ноги перед окном), но не больше 60% роста

GRAVITY = 2600.0      # px/c²
WALK_SPEED = 140.0    # px/c
SINK = 6              # насколько «утопить» ноги в край окна/панели задач
OWN_TITLES = {"Roxy Live2D", "Roxy Console", "Roxy Input", "Program Manager", "Параметры", "Settings"}

_user32 = ctypes.windll.user32 if os.name == "nt" else None
ROOT = os.path.dirname(os.path.abspath(__file__))
THREE_FILES = ["three.module.min.js", "three-vrm.module.min.js",
               os.path.join("addons", "loaders", "GLTFLoader.js"),
               os.path.join("addons", "utils", "BufferGeometryUtils.js")]


def _find_vrm():
    """3D-модель: "vrm_model" в config.json или самый свежий *.vrm в live2d_assets/models
    (новый экспорт из VRoid сразу подхватывается, старые файлы остаются как резерв)."""
    p = load_config().get("vrm_model")
    if p:
        p = p if os.path.isabs(p) else os.path.join(ROOT, p)
        return p if os.path.isfile(p) else None
    base = os.path.join(ROOT, "live2d_assets", "models")
    found = [os.path.join(d, f) for d, _, files in os.walk(base) for f in files if f.lower().endswith(".vrm")]
    return max(found, key=os.path.getmtime) if found else None


def _choose_avatar():
    """'vrm' или 'live2d'. "avatar" в config.json: auto (по умолчанию) | vrm | live2d."""
    want = str(load_config().get("avatar", "auto")).lower()
    if want == "live2d":
        return "live2d", None
    vrm = _find_vrm()
    js_dir = os.path.join(ROOT, "live2d_assets", "js", "three")
    libs = all(os.path.isfile(os.path.join(js_dir, f)) for f in THREE_FILES)
    if vrm and libs:
        return "vrm", vrm
    if want == "vrm" or vrm:
        why = "нет файла .vrm в live2d_assets/models" if not vrm else "нет three.js — запусти: python setup_vrm.py"
        print(f"⚠️ 3D-Рокси не запущена ({why}). Пока показываю Live2D.", flush=True)
    return "live2d", None


def _start_page_server():
    """Мини-сервер только на 127.0.0.1: ES-модули three.js не грузятся с file://.
    Отдаёт ТОЛЬКО vrm.html и live2d_assets/ (настройки, ключи и сессии наружу не попадают)."""
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    class H(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                          ".js": "text/javascript", ".mjs": "text/javascript", ".html": "text/html",
                          ".vrm": "application/octet-stream", ".json": "application/json"}

        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ROOT, **kw)

        def _allowed(self):
            """Только свои файлы и только по адресу 127.0.0.1/localhost (проверка Host — защита от DNS-rebinding)."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in ("127.0.0.1", "localhost"):
                return False
            path = self.path.split("?", 1)[0]
            if ".." in path or "\\" in path:
                return False
            return path == "/vrm.html" or (path.startswith("/live2d_assets/") and not path.endswith("/"))

        def do_GET(self):
            if not self._allowed():
                self.send_error(404)
                return
            super().do_GET()

        def do_HEAD(self):
            # раньше HEAD отвечал на ЛЮБОЙ путь проекта и выдавал размеры файлов (config.json, сессия и т.д.)
            if not self._allowed():
                self.send_error(404)
                return
            super().do_HEAD()

        def list_directory(self, path):
            self.send_error(404)           # без листинга папок
            return None

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="vrm-http").start()
    return srv.server_address[1]
_dwm = ctypes.windll.dwmapi if os.name == "nt" else None


# ==================== ОКНА WINDOWS (на чём сидеть) ====================
def _window_rect(hwnd):
    """Видимые границы окна (без невидимой рамки Windows 10/11), в физических пикселях."""
    r = wintypes.RECT()
    try:
        if _dwm and _dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:  # EXTENDED_FRAME_BOUNDS
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _is_perchable(hwnd, own_hwnd):
    if not _user32 or hwnd == own_hwnd or not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or _user32.IsZoomed(hwnd):
        return False
    cloaked = ctypes.c_int(0)
    try:
        _dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))   # скрытые окна UWP
    except Exception:
        pass
    if cloaked.value:
        return False
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return False
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    if buf.value in OWN_TITLES:
        return False
    l, t, r, b = _window_rect(hwnd)
    return (r - l) >= 400 and (b - t) >= 200


def _top_windows(own_hwnd):
    """Окна сверху вниз по z-порядку."""
    res = []
    if not _user32:
        return res
    cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if _is_perchable(h, own_hwnd):
            res.append(int(h))
        return True
    _user32.EnumWindows(cb_type(cb), 0)
    return res


class TypeBubble(QWidget):
    """Поле «Написать Рокси» прямо над её головой. Enter — отправить, Esc или клик мимо — закрыть.
    Открывается: двойной клик по Рокси, Num +, пункт трея «Написать Рокси…»."""

    def __init__(self, on_send):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Roxy Input")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._on_send = on_send
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("Напиши Рокси и нажми Enter…")
        self.edit.setMinimumWidth(300)
        self.edit.setStyleSheet("QLineEdit{background:#2b2b2b;color:white;border:2px solid #6c7ad6;border-radius:12px;"
                                "padding:6px 10px;font:11pt 'Segoe UI';}")
        btn = QPushButton("➤", self)
        btn.setFixedWidth(36)
        btn.setStyleSheet("QPushButton{background:#6c7ad6;color:white;border:none;border-radius:12px;padding:6px;"
                          "font:12pt 'Segoe UI';} QPushButton:hover{background:#7f8ce0;}")
        lay.addWidget(self.edit)
        lay.addWidget(btn)
        self.edit.returnPressed.connect(self._send)
        btn.clicked.connect(self._send)

    def _send(self):
        text = self.edit.text().strip()
        if text:
            self._on_send(text)
        self.edit.clear()
        self.hide()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def changeEvent(self, e):
        super().changeEvent(e)
        # кликнули в другое окно — поле прячется (написанное остаётся до следующего раза)
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, lambda: (not self.isActiveWindow()) and self.hide())

    def popup(self, anchor):
        """anchor — QRect окна Рокси на экране: поле ставим по центру над головой, не вылезая за экран."""
        self.adjustSize()
        w, h = max(self.width(), 340), self.height()
        x = anchor.center().x() - w // 2
        y = anchor.top() + int(anchor.height() * 0.08) - h
        scr = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = max(g.left() + 4, min(x, g.right() - w - 4))
        y = max(g.top() + 4, min(y, g.bottom() - h - 4))
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()
        self.activateWindow()
        if _user32:
            try:
                # Windows не даёт фоновой программе забирать фокус (особенно после глобальной клавиши).
                # Как в winutil._force_foreground: любое «нажатие» от нашего процесса снимает запрет
                # (0xE8 — неназначенная клавиша), затем SetForegroundWindow.
                hwnd = ctypes.c_void_p(int(self.winId()))
                _user32.keybd_event(0xE8, 0, 0, 0)
                _user32.keybd_event(0xE8, 0, 0x0002, 0)
                _user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        self.edit.setFocus()


class Live2DWindow(QMainWindow):
    music_sig = Signal(dict)         # состояние музыки из потока music.MusicWatcher
    js_signal = Signal(str)
    behavior_signal = Signal(str)
    key_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Roxy Live2D")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.view = QWebEngineView(self)
        self.view.setPage(_Page(self.view))
        self.view.page().setBackgroundColor(Qt.transparent)
        self.view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.avatar, vrm_path = _choose_avatar()
        # доля роста ниже края окна, когда сидит (для 3D приходит из модели — ровно по тазу)
        self.perch_frac, self.perch_frac_max = PERCH_SINK, PERCH_SINK_MAX
        if self.avatar == "vrm":
            port = _start_page_server()
            rel = os.path.relpath(vrm_path, ROOT).replace(os.sep, "/")
            url = QUrl(f"http://127.0.0.1:{port}/vrm.html")
            q = "model=/" + QUrl.toPercentEncoding(rel, b"/").data().decode()
            try:
                fps = int(load_config().get("live2d_fps", 0) or 0)      # config.json: "live2d_fps": 40 — реже кадры, меньше GPU
            except (TypeError, ValueError):
                fps = 0
            if fps > 0:
                q += f"&fps={fps}"
            if load_config().get("live2d_shadows", True) is False:   # config.json: "live2d_shadows": false — без теней
                q += "&shadows=0"
            dbg = str(load_config().get("live2d_debug", "") or "")     # отладка: доп. параметры страницы, например "clothpush=0"
            if dbg and all(c.isalnum() or c in "=&_,.-" for c in dbg):
                q += "&" + dbg.lstrip("&")
            pt = load_config().get("live2d_posetest")
            if pt:                                                  # отладка: позы стоя по очереди (true — все, "a,b" — только эти)
                q += "&posetest=" + (QUrl.toPercentEncoding(pt).data().decode() if isinstance(pt, str) else "1")
            url.setQuery(q)
            print(f"🧍 3D-Рокси: {rel}", flush=True)
            self.view.load(url)
        else:
            self.view.load(QUrl.fromLocalFile(os.path.join(ROOT, "live2d.html")))
        self.setCentralWidget(self.view)
        # Размер Рокси: "live2d_height" в config.json (по умолчанию 600; раньше было 700, а ещё раньше 950).
        # Чем меньше — тем на большее число окон она может сесть.
        h = int(load_config().get("live2d_height", 600) or 600)
        if self.avatar == "vrm":
            # запас вокруг модели: руки вверх/в стороны, прыжки и танец не обрезаются краем окна
            side = int(h * 1.15)
            self.resize(side, side)
        else:
            self.resize(int(h * 700 / 950), h)

        self.js_signal.connect(self._run_js_safe)
        self.behavior_signal.connect(self.do_behavior)
        self.key_signal.connect(self._on_hotkey)

        # По умолчанию клики проходят сквозь Рокси. Num / — переключить (можно таскать мышкой).
        # Num * — спрятать/показать Рокси (вместо старого «ПКМ = закрыть»).
        self.click_through = True
        self._ex_state = True          # желаемое состояние WS_EX_TRANSPARENT
        self._hit_busy = False
        self._hit_timer = QTimer(self)
        self._hit_timer.timeout.connect(self._hit_poll)
        QTimer.singleShot(300, self._apply_click_through)
        QTimer.singleShot(3000, self._start_flicker_watch)     # после того как окно встало на место
        QTimer.singleShot(500, self._setup_tray)          # когда окно уже полностью настроено
        if keyboard is not None:
            try:
                # suppress=True: свои клавиши Рокси «съедает», чтобы они не печатались в активное окно
                # (раньше Num 9 печатал «9» прямо в открытый в PyCharm код). Остальные клавиши проходят как обычно.
                keyboard.hook(self._on_key, suppress=True)
                print("⌨️ Live2D: Num / — клики сквозь Рокси, Num * — спрятать/показать, Num 9 — одежда, Num + — написать ей "
                      "(пока Рокси запущена, эти клавиши в другие программы не попадают)", flush=True)
            except Exception as e:
                print(f"⚠️ Live2D: горячие клавиши не работают: {e}", flush=True)
        threading.Thread(target=self._start_ws_server, daemon=True).start()

        self._bbox = None
        self._t_start = time.monotonic()
        self._mask_timer = QTimer(self)
        self._mask_timer.timeout.connect(self._update_mask)
        self._mask_timer.start(1000)

        self._last_look = (None, None)
        self._look_timer = QTimer(self)
        self._look_timer.timeout.connect(self._update_look)
        self._look_timer.start(50)

        # ---- «живое» поведение
        self.autonomy = bool(load_config().get("pet_autonomy", True))
        self.mode = "idle"           # idle | walk | air | perch | drag
        self.px, self.py = float(self.x()), float(self.y())
        self.vx = self.vy = 0.0
        self.walk_target = None
        self.after_walk = None       # что сделать, когда дошла
        self.perch_hwnd = None
        self.perch_rel = 0.0         # смещение по x относительно левого края окна
        self.land_on = None          # hwnd, на который летим (None — панель задач)
        self.perch_sink = 0.0        # на сколько px ноги ниже края окна (зависит от высоты окна)
        self.last_talk = 0.0
        self._t_prev = time.monotonic()
        self._placed = False
        self._phys = QTimer(self)
        self._phys.timeout.connect(self._tick)
        # 60 Гц и точный таймер: при 33 мс обычный таймер Windows срабатывал то через 31, то через 47 мс —
        # окно ехало неровными рывками, и на ходу Рокси «дёргалась как курица»
        self._phys.setTimerType(Qt.PreciseTimer)
        self._phys.start(16)
        self._walk_pause = False
        self._brain = QTimer(self)
        self._brain.setSingleShot(True)
        self._brain.timeout.connect(self._think)
        self._schedule_think()

        self.drag_pos = None
        self._behind_hwnd = None     # прячется за этим окном (Рокси на время не «поверх всех»)
        # танец под музыку: None | "cmd" (попросили) | "auto" (сама); состояние музыки — из music.py
        self._mdance = None
        self._music = {"on": False}
        self._music_off_since = None
        self._mdance_until = 0.0
        self._mdance_cool = 0.0
        self._mdance_wait = None
        self.music_sig.connect(self._on_music)
        try:
            import music
            self._music_thread = music.MusicWatcher(self.music_sig.emit)
            self._music_thread.start()
        except Exception as e:
            print(f"🎵 танец под музыку недоступен: {e}", flush=True)
        self._edge = None            # игра с окнами: что сейчас делает (см. _edge_*)

    # ==================== клавиши ====================
    _ROXY_KEYS = ("*", "/", "9", "page up", "+")    # page up — это Num 9 при выключенном Num Lock; Num + — написать Рокси

    def _on_key(self, e):
        """Поток библиотеки keyboard, хук с suppress=True: вернуть True — клавиша идёт дальше в систему,
        False — съедаем. Только клавиши ЦИФРОВОГО блока (is_keypad); обычные * / 9 и PageUp не трогаем.
        Здесь нельзя ни падать, ни долго думать: любая ошибка возвращает True, чтобы не заблокировать клавиатуру."""
        try:
            if e.event_type == "down":
                self._typing_t = time.monotonic()      # для взгляда «на окно, где печатаешь» (только время, не сами клавиши)
            if getattr(e, "is_keypad", False) and e.name in self._ROXY_KEYS:
                if e.event_type == "down":
                    self.key_signal.emit("9" if e.name == "page up" else e.name)
                return False                   # и нажатие, и отпускание не уходят в активное окно
        except Exception:
            pass
        return True

    def _on_hotkey(self, key):
        if key == "/":
            self.click_through = not self.click_through
            self._apply_click_through()
            print(f"🖱️ Рокси: {'клики проходят сквозь неё' if self.click_through else 'её можно взять мышкой (фон вокруг неё кликается насквозь)'}", flush=True)
            if not self.click_through:
                self._js("hop(1)")              # видно, что режим переключился
        elif key == "9":
            self._js("toggleOutfit()")
            print("👙 Num 9: одежда переключена", flush=True)
        elif key == "+":
            self.open_input()
        elif key == "*":
            if self.isVisible():
                self._user_hidden = True
                self.hide()
                print("🙈 Рокси спрятана (Num * — показать)", flush=True)
            else:
                self._user_hidden = False
                self.show()
                self._apply_click_through()
                print("👀 Рокси снова на экране", flush=True)

    # ==================== письменный ввод ====================
    def open_input(self):
        """Поле ввода над головой Рокси (двойной клик по ней, Num +, трей)."""
        try:
            if getattr(self, "_bubble", None) is None:
                self._bubble = TypeBubble(self._typed)
            if not self.isVisible():
                self._user_hidden = False
                self.show()
                self._apply_click_through()
            self._bubble.popup(self.frameGeometry())
            self._js("hop(1)")
        except Exception as e:
            print(f"⚠️ поле ввода: {e}", flush=True)

    def _typed(self, text):
        print(f"⌨️ Написали Рокси: {text}", flush=True)
        self.last_talk = time.monotonic()          # не убегать, пока отвечает
        self._send_main({"type": "typed", "text": text})

    def _set_transparent(self, on):
        """WS_EX_TRANSPARENT: окно не ловит мышь, клики уходят в окна под ним."""
        self._ex_state = bool(on)
        if not _user32:
            return
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED = -20, 0x20, 0x80000
            ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TRANSPARENT | WS_EX_LAYERED) if on else (ex & ~WS_EX_TRANSPARENT)
            _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        except Exception as e:
            print(f"⚠️ click-through: {e}", flush=True)

    def _apply_click_through(self):
        """Режим по умолчанию: клики проходят сквозь всё окно. После Num / — сквозь прозрачный фон,
        а Рокси (её непрозрачные пиксели) можно взять мышкой: проверяем пиксель под курсором 30 раз в секунду."""
        if self.click_through:
            self._hit_timer.stop()
            self._js_raw("window._hitEnabled = false;")
            self._set_transparent(True)
        else:
            self._js_raw("window._hitEnabled = true;")
            self._set_transparent(False)
            if self.avatar == "vrm":
                self._hit_timer.start(30)
            # у Live2D окно обрезано маской по контуру модели — там ловим мышь как раньше

    def _hit_poll(self):
        if self.click_through or not self.isVisible() or self.drag_pos is not None or self._hit_busy:
            return
        local = self.view.mapFromGlobal(QCursor.pos())
        if not self.view.rect().contains(local):
            return
        self._hit_busy = True
        self.view.page().runJavaScript(
            f"window._hitTest ? window._hitTest({local.x()},{local.y()}) : '1'", self._hit_result)

    def _hit_result(self, v):
        self._hit_busy = False
        if self.click_through or self.drag_pos is not None:
            return
        solid = str(v) != "0"                 # пустой/непонятный ответ считаем «твёрдым»
        # антидребезг: переключаем стиль окна, только если два замера подряд согласны. Курсор на краю
        # силуэта раньше дёргал стиль Windows десятки раз в секунду — подозреваемый номер один в мигании.
        if solid != getattr(self, "_hit_last", None):
            self._hit_last = solid
            return
        if solid == self._ex_state:           # _ex_state=True — окно сейчас прозрачно для кликов
            self._ex_toggles = getattr(self, "_ex_toggles", 0) + 1
            self._set_transparent(not solid)

    # ==================== значок в трее ====================
    QUIT_CODE = 42      # main.py, увидев этот код выхода окна, выключается целиком

    def _setup_tray(self):
        try:
            self._setup_tray_inner()
        except Exception as e:
            print(f"⚠️ трей: {e}", flush=True)

    def _setup_tray_inner(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        pm = QPixmap(64, 64)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(108, 122, 214))
        p.setPen(QColor(255, 255, 255))
        p.drawEllipse(2, 2, 60, 60)
        f = QFont("Segoe UI", 30, QFont.Bold)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, "Р")
        p.end()
        self._tray = QSystemTrayIcon(QIcon(pm), self)
        self._tray.setToolTip("Рокси")
        m = QMenu()
        a_show = QAction("Спрятать / показать (Num *)", m); a_show.triggered.connect(lambda: self._on_hotkey("*"))
        a_click = QAction("Брать мышкой / клики сквозь (Num /)", m); a_click.triggered.connect(lambda: self._on_hotkey("/"))
        a_outfit = QAction("Одежда (Num 9)", m); a_outfit.triggered.connect(lambda: self._on_hotkey("9"))
        a_type = QAction("Написать Рокси… (Num +, двойной клик)", m); a_type.triggered.connect(self.open_input)
        a_walk = QAction("Пусть гуляет сама", m, checkable=True); a_walk.setChecked(bool(getattr(self, "autonomy", True)))
        a_walk.toggled.connect(lambda on: self.do_behavior("autonomy_on" if on else "autonomy_off"))
        a_quit = QAction("Выключить Рокси", m); a_quit.triggered.connect(self._quit_all)
        for a in (a_type, a_show, a_click, a_outfit, a_walk):
            m.addAction(a)
        m.addSeparator()
        m.addAction(a_quit)
        self._tray_menu = m
        self._tray.setContextMenu(m)
        self._tray.activated.connect(lambda r: self._on_hotkey("*") if r == QSystemTrayIcon.Trigger else None)
        self._tray.show()

    def _send_main(self, obj):
        """Событие в main.py (по тому же WebSocket, в обратную сторону)."""
        conn, loop = getattr(self, "_ws_conn", None), getattr(self, "_ws_loop", None)
        if conn is None or loop is None:
            print("⚠️ main.py ещё не подключился", flush=True)
            return
        asyncio.run_coroutine_threadsafe(conn.send(json.dumps(obj)), loop)

    def _quit_all(self):
        print("👋 Выключаю Рокси (из трея)", flush=True)
        try:
            self._tray.hide()
        except Exception:
            pass
        QApplication.instance().exit(self.QUIT_CODE)

    # ==================== движение окна → физика ткани ====================
    def moveEvent(self, event):
        super().moveEvent(event)
        now = time.monotonic()
        if now - getattr(self, "_winpos_t", 0.0) >= 0.012:          # не чаще ~80 раз в секунду
            self._winpos_t = now
            p = self.pos()
            # с временем отправки: страница считает скорость по нему, а не по времени прихода (приходят пачками)
            self._js(f"winPos({p.x()},{p.y()},{now * 1000:.1f})")

    # ==================== сторож мигания ====================
    def _start_flicker_watch(self):
        """Раз в 100 мс смотрим на окно глазами Windows и пишем в лог ТОЛЬКО перемены: окно скрыто,
        свёрнуто, потеряло «поверх всех», стиль layered, ушло за экран. Если Рокси мигнёт, а в логе
        рядом окажется 🔦 — причина найдена. Плюс раз в минуту — сколько раз переключался стиль кликов."""
        self._fw_last = None
        self._fw_min_t = time.monotonic()
        self._fw_timer = QTimer(self)
        self._fw_timer.timeout.connect(self._flicker_tick)
        self._fw_timer.start(100)

    def _flicker_tick(self):
        try:
            hwnd = int(self.winId())
            st = {"qt_visible": self.isVisible()}
            if _user32:
                ex = _user32.GetWindowLongW(hwnd, -20)
                st.update(win_visible=bool(_user32.IsWindowVisible(hwnd)), iconic=bool(_user32.IsIconic(hwnd)),
                          layered=bool(ex & 0x80000), topmost=bool(ex & 0x8))
            scr = QApplication.screenAt(self.geometry().center())
            st["on_screen"] = scr is not None
            if self._fw_last is not None and st != self._fw_last and not self._hidden_by_user():
                changed = ", ".join(f"{k}: {self._fw_last.get(k)}→{v}" for k, v in st.items() if self._fw_last.get(k) != v)
                print(f"🔦 {time.strftime('%H:%M:%S')} окно Рокси: {changed} (режим {self.mode})", flush=True)
                if _user32 and st.get("topmost") is False and self._fw_last.get("topmost") and not self._behind_hwnd:
                    # слетело «поверх всех» — возвращаем без активации окна
                    # HWND_TOPMOST = -1; c_void_p — чтобы на 64-битной Windows ушло именно -1, а не 0xFFFFFFFF
                    _user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self._fw_last = st
            now = time.monotonic()
            if now - self._fw_min_t >= 60:
                n = getattr(self, "_ex_toggles", 0)
                if n > 30:
                    print(f"🔦 стиль «клики насквозь» переключался {n} раз за минуту", flush=True)
                self._ex_toggles = 0
                self._fw_min_t = now
        except Exception:
            pass

    def _hidden_by_user(self):
        return bool(getattr(self, "_user_hidden", False))

    # ==================== мышь ====================
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._edge_abort()
            self._mdance_stop("взяли мышкой")
            self.drag_pos = event.globalPosition().toPoint()
            self._press_pos = QPoint(self.drag_pos)
            self._press_t = time.monotonic()
            self._last_touch = self._press_t
            self._held_on = False
            self._set_walking(False)
            self.mode = "drag"
            self._drag_acc, self._drag_t = 0.0, time.monotonic()
            # «схватили» — только если держат дольше 0.2 с или сразу тянут; короткий клик — это «тык»
            QTimer.singleShot(200, self._maybe_start_held)

    def _maybe_start_held(self):
        if self.drag_pos is not None and not self._held_on:
            self._held_on = True
            self._js("setHeld(true)")             # схватили: болтается, медленно дрыгается, пугается

    def mouseMoveEvent(self, event):
        if self.drag_pos:
            if not getattr(self, "_held_on", True) and (event.globalPosition().toPoint() - self._press_pos).manhattanLength() > 6:
                self._maybe_start_held()
            delta = event.globalPosition().toPoint() - self.drag_pos
            self.move(self.pos() + delta)
            self.drag_pos = event.globalPosition().toPoint()
            # скорость рывков — в маятник тела; не чаще ~30 раз в секунду, чтобы не заваливать JS
            self._drag_acc = getattr(self, "_drag_acc", 0.0) + delta.x()
            now = time.monotonic()
            dt = now - getattr(self, "_drag_t", now)
            if dt >= 0.033:
                self._js(f"dragMove({self._drag_acc / dt:.0f})")
                self._drag_acc, self._drag_t = 0.0, now

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dbl = True              # второе отпускание этой пары — не «отпустили после переноски»
            self._poke_id = getattr(self, "_poke_id", 0) + 1     # отменяем «тык» от первого клика
            self.drag_pos = None
            self.mode = "idle"
            self.open_input()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if getattr(self, "_dbl", False):
                self._dbl = False
                self.drag_pos = None
                return
            self.drag_pos = None
            if not getattr(self, "_held_on", True):
                # короткий клик без перетаскивания — «тык»: в голову или в тело (решает страница)
                local = self.view.mapFromGlobal(event.globalPosition().toPoint())
                # ждём, не будет ли второго клика: двойной клик — это «написать», а не «тык»
                self._poke_id = getattr(self, "_poke_id", 0) + 1
                pid, lx, ly = self._poke_id, local.x(), local.y()
                QTimer.singleShot(QApplication.doubleClickInterval() + 20,
                                  lambda: self._js(f"poke({lx},{ly})") if pid == self._poke_id else None)
                self._held_on = True
                self.mode = "idle"
                self.last_talk = time.monotonic() - 2.5     # пусть секунду-другую не убегает
                return
            self._js("setHeld(false)")
            self.px, self.py = float(self.x()), float(self.y())
            if self.autonomy:
                self._fall()           # отпустили — падает на окно под ней или на панель задач
            else:
                self.mode = "idle"     # без автономии остаётся там, где оставили (как раньше)

    # ==================== JS ====================
    def _run_js_safe(self, script):
        if self.view and self.view.page():
            self.view.page().runJavaScript(script)

    def _run_js(self, script):
        self.js_signal.emit(script)

    def _js_raw(self, script):
        if self.view and self.view.page():
            self.view.page().runJavaScript(script)

    def _js(self, call):
        self._run_js_safe(f"if(window.roxyLive2D) window.roxyLive2D.{call}")

    def _update_mask(self):
        # окно могло потерять WS_EX_TRANSPARENT (Qt пересоздаёт хэндл) — следим раз в секунду
        if _user32 and self.isVisible() and self.mode != "drag":
            try:
                ex = _user32.GetWindowLongW(int(self.winId()), -20)
                if bool(ex & 0x20) != self._ex_state:
                    self._set_transparent(self._ex_state)
            except Exception:
                pass
        js = """
        (function() {
            try {
                if (!window._model) return JSON.stringify({err: 'model not loaded'});
                const m = window._model;
                let b, err = '';
                try {
                    b = window._stableBounds ? window._stableBounds() : m.getBounds();
                } catch (e1) {
                    err = 'getBounds: ' + e1;
                    const lb = m.getLocalBounds(), s = m.scale.x;     // старый способ (как было раньше)
                    b = {x: m.x + lb.x * s, y: m.y + lb.y * s, width: lb.width * s, height: lb.height * s};
                }
                // строкой: объекты JS в этой версии PySide6 приходят в Python пустыми
                const sink = window._perchSink ? window._perchSink() : null;
                const busy = window._petBusy ? !!window._petBusy() : false;      // 3D: подбирает слетевшую шляпу
                return JSON.stringify({x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), err: err, sink: sink, busy: busy});
            } catch (e) { return JSON.stringify({err: String(e)}); }
        })();
        """
        self.view.page().runJavaScript(js, self._apply_mask)

    def _apply_mask(self, bbox):
        if isinstance(bbox, str) and bbox.startswith("{"):
            try:
                bbox = json.loads(bbox)
            except ValueError:
                pass
        # «model not loaded» — модель просто ещё грузится; если так и не загрузится, скажет предупреждение ниже
        if isinstance(bbox, dict) and bbox.get("err") and bbox["err"] != "model not loaded" and not getattr(self, "_warned_err", False):
            self._warned_err = True
            print(f"⚠️ Live2D bounds: {bbox['err']}", flush=True)
        if not isinstance(bbox, dict) or bbox.get('w', 0) <= 0 or bbox.get('h', 0) <= 0:
            if not getattr(self, "_warned_bbox", False) and time.monotonic() - self._t_start > 15:
                self._warned_bbox = True
                print(f"⚠️ Live2D: не получаю размеры модели ({bbox!r}) — поведение не работает. Смотри строки [JS:...] выше.", flush=True)
            return
        self._avatar_busy = bool(bbox.get("busy"))
        sink = bbox.get("sink")
        if isinstance(sink, (int, float)) and 0.2 < sink < 0.8:
            # 3D: садится попой на край — глубже нельзя (иначе висит в воздухе перед окном)
            self.perch_frac, self.perch_frac_max = float(sink), float(sink) + 0.04
        old = self._bbox
        # Модель чуть «дышит» и качается — мелкие изменения границ игнорируем, иначе окно дёргается
        if old is None or any(abs(bbox[k] - old[k]) > 6 for k in ("x", "y", "w", "h")):
            self._bbox = bbox
        bbox = self._bbox
        pad, top_pad = 15, 45          # сверху запас под подпрыгивания
        x = max(0, bbox['x'] - pad)
        y = max(0, bbox['y'] - top_pad)
        w = min(self.width() - x, bbox['w'] + pad * 2)
        h = min(self.height() - y, bbox['h'] + pad + top_pad)
        if w > 0 and h > 0 and self.avatar != "vrm":
            self.setMask(QRegion(x, y, w, h))
        if not self._placed:
            self._placed = True
            print(f"🐾 Live2D: модель {bbox['w']}x{bbox['h']}, прогулки {'вкл' if self.autonomy else 'выкл'}", flush=True)
            if self.autonomy:
                self._place_on_taskbar()

    def _update_look(self):
        b = self._bbox
        hx, hy = (b['x'] + b['w'] / 2, b['y'] + b['h'] * 0.22) if b else (self.width() / 2, self.height() * 0.3)
        head = self.mapToGlobal(self.rect().topLeft())
        hx += head.x()
        hy += head.y()
        cur = QCursor.pos()
        # курсор над окном — странице (поглаживание по голове); быстрое движение рядом будит дремлющую
        local = self.view.mapFromGlobal(cur)
        if self.view.rect().contains(local):
            if (cur.x(), cur.y()) != getattr(self, "_last_cur", None):
                self._js(f"cursorAt({local.x()},{local.y()})")
                self._last_touch = time.monotonic()
        self._last_cur = (cur.x(), cur.y())
        # печатаешь — иногда поглядывает на окно, в котором ты пишешь (а не на курсор)
        look_at = cur
        if time.monotonic() - getattr(self, "_typing_t", 0.0) < 1.5 and _user32:
            try:
                fg = _user32.GetForegroundWindow()
                if fg and fg != int(self.winId()):
                    l, t, r, btm = self._rect_logical(fg)
                    if r - l > 50:
                        look_at = QPoint(int((l + r) / 2), int(t + (btm - t) * 0.6))
            except Exception:
                pass
        cur = look_at
        screen = QGuiApplication.screenAt(cur) or QGuiApplication.primaryScreen()
        g = screen.geometry() if screen else None
        sw = (g.width() if g else 1920) / 2
        sh = (g.height() if g else 1080) / 2
        dx = max(-1.0, min(1.0, (cur.x() - hx) / sw))
        dy = max(-1.0, min(1.0, (hy - cur.y()) / sh))
        if self.mode == "walk":
            dx = 0.35 if self.vx > 0 else -0.35     # при ходьбе смотрит вперёд
            dy = 0.0
        lx, ly = self._last_look
        if lx is None or abs(dx - lx) > 0.01 or abs(dy - ly) > 0.01:
            self._last_look = (dx, dy)
            self._js(f"lookAt({dx:.3f},{dy:.3f})")

    # ==================== геометрия ====================
    def _screen(self):
        c = self.mapToGlobal(self.rect().center())
        return QGuiApplication.screenAt(c) or QGuiApplication.primaryScreen()

    def _work(self):
        return self._screen().availableGeometry()      # без панели задач

    def _dpr(self):
        return self._screen().devicePixelRatio() or 1.0

    def _feet(self):
        """Где ноги/низ модели и её центр внутри окна."""
        b = self._bbox
        if not b:
            return None
        return b['y'] + b['h'], b['x'] + b['w'] / 2, b['w'], b['h']

    def _rect_logical(self, hwnd):
        d = self._dpr()
        l, t, r, b = _window_rect(hwnd)
        return l / d, t / d, r / d, b / d

    def _floor_y(self):
        """y окна, при котором Рокси стоит на текущем «полу»."""
        f = self._feet()
        if not f:
            return None
        if self.land_on and _user32 and _user32.IsWindow(self.land_on):
            return self._rect_logical(self.land_on)[1] - f[0] + (self.perch_sink or f[3] * self.perch_frac)
        return self._work().bottom() - f[0] + SINK

    def _clamp_x(self, x):
        f = self._feet()
        w = self._work()
        if not f:
            return x
        half = f[2] / 2
        return max(w.left() - f[1] + half, min(w.right() - f[1] - half, x))

    # ==================== поведение ====================
    def _set_walking(self, on, direction=None):
        speed = abs(self.vx) if on else 0          # 3D-модель подгоняет шаг под скорость, чтобы ступни не скользили
        self._js(f"setWalking({'true' if on else 'false'}, {speed:.0f})")
        if direction is not None:
            self._js(f"setFacing({1 if direction > 0 else -1})")

    def _place_on_taskbar(self):
        f = self._feet()
        if not f:
            return
        w = self._work()
        self.land_on = None
        self.px = self._clamp_x(w.right() - f[1] - f[2])
        self.py = self._floor_y()
        self.move(int(self.px), int(self.py))
        self.mode = "idle"

    def _fall(self, vy=0.0):
        """Падение вниз: приземлится на окно под ней или на панель задач."""
        self.perch_sink = 0.0
        self._js("setSitting(false)")
        self._js("air()")
        self.mode = "air"
        self.vx, self.vy = 0.0, vy
        self.land_on = self._window_below()

    def _window_below(self):
        f = self._feet()
        if not f or not _user32:
            return None
        feet_x = self.px + f[1]
        feet_y = self.py + f[0]
        for h in _top_windows(int(self.winId())):
            l, t, r, b = self._rect_logical(h)
            if l + 60 < feet_x < r - 60 and t >= feet_y - 5:
                return h
        return None

    def _jump(self, power=1050.0):
        if self.mode in ("idle", "perch", "walk"):
            self._set_walking(False)
            self._js("crouch()")                       # сначала присесть...
            self.mode = "prep"

            def launch():                              # ...потом оттолкнуться
                if self.mode != "prep":
                    return
                self.mode = "air"
                self.vy = -power
                self.vx = 0.0
                self._js("setSitting(false)")
                self._js("air()")
            QTimer.singleShot(260, launch)             # присед виден — потом толчок

    def _flip(self, power=1250.0):
        """Сальто назад: присела → сильный толчок вверх → страница крутит её в воздухе за время полёта."""
        if self.mode not in ("idle", "perch", "walk"):
            return
        self._set_walking(False)
        self._js("crouch()")
        self.mode = "prep"

        def launch():
            if self.mode != "prep":
                return
            self.mode = "air"
            self.vy = -power
            self.vx = 0.0
            self._js("setSitting(false)")
            self._js(f"flip({2 * power / GRAVITY:.2f})")     # время полёта до того же уровня
        QTimer.singleShot(300, launch)

    ACRO_DUR = {"handstand": 6.0, "cartwheel": 2.4}   # как в vrm.html (HS_DUR, CW_DUR)

    def _acro(self, name):
        """Стойка на руках — на месте; колесо — окно едет в сторону, где больше места (как в странице: та же
        плавная кривая, что и поворот тела, — ступни и кисти не скользят)."""
        if self.mode not in ("idle", "walk") or self.land_on is not None:
            return                                         # сидя на окне и в полёте — нет
        self._set_walking(False)
        f = self._feet()
        d = 1
        dist = 0.0
        if name == "cartwheel" and f:
            # Окно во время колеса НЕ двигается (перемещение прозрачного окна дёргалось — колесо шло рывками):
            # тело катится внутри окна на ~четверть роста, а потом окно плавно подъезжает под неё,
            # а страница в ту же секунду сдвигает тело обратно (на глаз она просто стоит).
            dist = min(0.25 * f[3], 0.2 * self.width())
            right = self._clamp_x(self.px + dist) - self.px
            left = self.px - self._clamp_x(self.px - dist)
            d = 1 if right >= left else -1
            dist = max(right, left)
        self._acro_x0, self._acro_d, self._acro_t, self._acro_name = self.px, d * dist, 0.0, name
        self.mode = "acro"
        self._js(f"acro('{name}', {d}, {dist:.0f})")

    def _jump_to(self, tx, ty_floor, hwnd, flight=0.75):
        """Прыжок по дуге в точку (tx — x окна, ty_floor — y окна в точке приземления)."""
        self._set_walking(False)
        self._js("setSitting(false)")
        T = flight
        self.vx = (tx - self.px) / T
        self.vy = (ty_floor - self.py - 0.5 * GRAVITY * T * T) / T
        self.land_on = hwnd
        self.mode = "air"
        self._js(f"setFacing({1 if self.vx >= 0 else -1})")
        self._js("air()")

    def _walk_to(self, tx, then=None):
        tx = self._clamp_x(tx)
        if abs(tx - self.px) < 10:
            if then:
                then()
            return
        self.walk_target = tx
        self.after_walk = then
        self.vx = WALK_SPEED if tx > self.px else -WALK_SPEED
        self.mode = "walk"
        self._walk_pause = False
        self._set_walking(True, self.vx)

    def _perch(self):
        f = self._feet()
        if not f:
            return False
        work = self._work()
        skipped = 0
        self._perch_why = ""
        wins = _top_windows(int(self.winId()))
        for h in wins:
            l, t, r, b = self._rect_logical(h)
            # садимся так, чтобы голова осталась на экране: чем выше окно, тем «глубже»
            sink = max(f[3] * self.perch_frac, f[3] - (t - work.top()) + 10)
            if sink > f[3] * self.perch_frac_max:            # окно почти у верха экрана — не влезет
                skipped += 1
                continue
            cx = random.uniform(l + 80, r - 80)
            tx = cx - f[1]
            ty = t - f[0] + sink
            self.perch_sink = sink
            self.perch_rel = tx - l
            self._jump_to(tx, ty, h)
            print("🪟 Прыгаю на окно")
            return True
        if not wins:
            self._perch_why = "все окна развёрнуты на весь экран или свёрнуты — сверни или уменьши одно"
            print("🪟 Не на что сесть: все окна развёрнуты на весь экран или свёрнуты.")
        else:
            self._perch_why = f"у {skipped} окон верхний край слишком высоко, голова не поместится на экране — опусти окно пониже"
            need = int(f[3] * (1 - self.perch_frac_max) + 10 + work.top())
            print(f"🪟 Не на что сесть: у {skipped} окон верх выше {need} px, голова ушла бы за экран. "
                  f"Опусти окно пониже или уменьши Рокси: \"live2d_height\" в config.json.")
        return False

    def do_behavior(self, name):
        self._last_touch = time.monotonic()
        if getattr(self, "_dozing", False) and name not in ("autonomy_on", "autonomy_off"):
            self._dozing = False
            self._js("setDoze(false)")                # любая команда будит
        if self.mode in ("drag", "acro") or not self._feet():
            if name == "perch":
                self._send_main({"type": "behavior_result", "name": "perch", "ok": False,
                                 "reason": "я сейчас занята (кувыркаюсь или меня держат мышкой) или ещё не измерила себя — повтори через пару секунд"})
            return
        self._edge_abort()
        if name == "dance_music":
            return self._mdance_cmd()
        if name == "dance_stop":
            return self._mdance_stop("попросили")
        self._mdance_stop("другая команда")
        if name == "autonomy_on":
            self.autonomy = True
            if self.mode == "idle" and self.land_on is None:
                self._fall()
            return
        if name == "autonomy_off":
            self.autonomy = False
            self._set_walking(False)
            if self.mode == "walk":
                self.mode = "idle"
            return
        if name == "nod" or name == "shake":
            self._js(f"gesture('{name}')")
        elif name in ("dance", "spin", "bow", "stretch", "yawn", "look_around") or name.startswith(("magic", "staff_")):
            self._set_walking(False)
            if self.mode == "walk":
                self.mode = "idle"
            self._js(f"play('{name}')")
        elif name == "jump":
            if self.mode == "air":
                return
            self._jump()
            self._js("hop(1)")
        elif name == "flip":
            if self.mode == "air":
                return
            self._flip()
        elif name in ("handstand", "cartwheel"):
            self._acro(name)
        elif name == "perch":
            ok = self._perch()
            self._send_main({"type": "behavior_result", "name": "perch", "ok": bool(ok),
                             "reason": getattr(self, "_perch_why", "")})      # main.py отвечает честно, а не «Сажусь»
            if not ok:
                self._js("gesture('shake')")      # некуда садиться
        elif name == "floor":
            self.land_on = None
            if self.mode == "perch":
                self.mode = "idle"
            self._fall(vy=-500)
        elif name == "come":
            cur = QCursor.pos()
            f = self._feet()
            target = cur.x() - f[1]
            print(f"🚶 Иду к курсору (x={cur.x()})")
            if self.mode == "perch":
                self.land_on = None
                self._fall(vy=-500)
                QTimer.singleShot(1500, lambda: self._walk_to(target))
            else:
                self._walk_to(target, then=lambda: self._js("gesture('nod')"))
        elif name == "lean":
            self._lean()
        elif name in ("peek", "hide", "push"):
            if self.mode == "perch":                 # сначала слезть на пол
                self.land_on = None
                self._fall(vy=-500)
                QTimer.singleShot(1600, lambda: self._edge_play(name))
            elif not self._edge_play(name):
                self._js("gesture('shake')")
        elif name == "walk":
            if self.mode == "perch":
                self.land_on = None
                self._fall(vy=-500)
            else:
                w = self._work()
                f = self._feet()
                for _ in range(6):                     # цель хотя бы в 150 px, чтобы прогулка была заметной
                    tx = self._clamp_x(random.uniform(w.left(), w.right() - f[2]) - (f[1] - f[2] / 2))
                    if abs(tx - self.px) > 150:
                        break
                self._walk_to(tx)

    def _lean(self):
        """Подойти к ближнему краю экрана и опереться о него рукой (нога за ногу, вторая рука на бедре)."""
        f = self._feet()
        if not f or self.mode not in ("idle", "walk"):
            return
        w = self._work()
        ppm = f[3] / 1.75                                   # px на метр (рост модели 1.75 м)
        reach = 0.42 * ppm                                   # от центра ступней до стены
        feet_x = self.px + f[1]
        side = -1 if feet_x - w.left() < w.right() - feet_x else 1

        def arrived():
            wall_px = (w.left() - self.px) if side < 0 else (w.right() - self.px)   # край экрана в координатах окна
            self._js(f"setLean({side}, {wall_px:.0f})")
            print(f"🧱 опирается о {'левый' if side < 0 else 'правый'} край экрана", flush=True)
        target_feet = (w.left() + reach) if side < 0 else (w.right() - reach)
        self._walk_to(target_feet - f[1], then=arrived)

    # ==================== игра с окнами ====================
    # peek — выглядывает из-за края экрана; hide — прячется за окно (на время уходит под него по z-порядку)
    # и выглядывает из-за его края; push — упирается ладонями в край окна и сдвигает его.
    def _ppm(self):
        f = self._feet()
        return f[3] / 1.75 if f else 300.0                   # px на метр (рост модели 1.75 м)

    def _screen_at(self, x, y):
        return QGuiApplication.screenAt(QPoint(int(x), int(y)))

    def _edge_play(self, kind):
        f = self._feet()
        if not f or self.mode not in ("idle", "walk") or self.land_on is not None:
            return False
        if kind == "push":
            return self._push_start()
        if kind == "hide" and self._hide_start():
            return True
        return self._peek_start()

    def _peek_start(self):
        f, w, ppm = self._feet(), self._work(), self._ppm()
        feet_x = self.px + f[1]
        sides = []
        for side in (-1, 1):                                 # край, за которым нет другого монитора
            ex = w.left() if side < 0 else w.right()
            if self._screen_at(ex + side * 30, w.center().y()) is None:
                sides.append(side)
        if not sides:
            return False
        side = min(sides, key=lambda sd: abs(feet_x - (w.left() if sd < 0 else w.right())))
        ex = w.left() if side < 0 else w.right()
        self._edge_begin("peek", ex, -side, None)
        print(f"🙈 выглядывает из-за {'левого' if side < 0 else 'правого'} края экрана", flush=True)
        return True

    def _hide_start(self):
        f, w, ppm = self._feet(), self._work(), self._ppm()
        feet_x, feet_y = self.px + f[1], self.py + f[0]
        best = None
        for h in _top_windows(int(self.winId())):
            l, t, r, b = self._rect_logical(h)
            # окно должно закрыть её от макушки почти до колен
            if t > feet_y - f[3] * 0.98 or b < feet_y - f[3] * 0.35 or (r - l) < 1.2 * ppm:
                continue
            for ex, out in ((l, -1), (r, 1)):                # out — куда она выглядывает (наружу из-за окна)
                ax = ex + out * 0.45 * ppm                   # откуда подходит
                if not (w.left() + 0.3 * ppm < ax < w.right() - 0.3 * ppm):
                    continue
                d = abs(ax - feet_x)
                if best is None or d < best[0]:
                    best = (d, h, ex, out)
            if best:                                         # ближайшее сверху по z-порядку окно — достаточно
                break
        if not best:
            return False
        _, h, ex, out = best
        self._edge_begin("hide", ex, out, h)
        print("🙈 прячется за окно", flush=True)
        return True

    def _push_start(self):
        f, w, ppm = self._feet(), self._work(), self._ppm()
        feet_x, feet_y = self.px + f[1], self.py + f[0]
        chest = feet_y - f[3] * 0.72
        best = None
        for h in _top_windows(int(self.winId())):
            l, t, r, b = self._rect_logical(h)
            if not (t < chest - 20 and b > chest + 20):      # край окна — на уровне её груди
                continue
            for ex, dirn in ((l, 1), (r, -1)):               # dirn — куда толкает
                stand = ex - dirn * 0.36 * ppm               # где стоит (ступни)
                room = (w.right() - r) if dirn > 0 else (l - w.left())
                if not (w.left() + 0.25 * ppm < stand < w.right() - 0.25 * ppm) or room < 60:
                    continue
                d = abs(stand - feet_x)
                if best is None or d < best[0]:
                    best = (d, h, ex, dirn, min(160.0, room))
            if best:
                break
        if not best:
            return False
        _, h, ex, dirn, dist = best
        self._edge = {"kind": "push", "hwnd": h, "dir": dirn, "dist": dist, "moved": 0.0}

        def arrived():
            if not self._edge or self._edge.get("hwnd") != h:
                return
            self._js(f"setFacing({dirn})")
            l, t, r, b = self._rect_logical(h)
            wall = (l if dirn > 0 else r) - self.px
            self._js(f"setEdge('push', {dirn}, {wall:.0f})")
            def go():
                if self._edge and self._edge.get("kind") == "push" and self.mode == "idle":
                    self.mode = "edge_push"
                    self.vx = 70.0 * dirn
                    self._set_walking(True, dirn)
            QTimer.singleShot(700, go)                       # ладони легли на край — поехали
        self._walk_to(ex - dirn * 0.36 * ppm - f[1], then=arrived)
        print(f"🫸 толкает окно {'вправо' if dirn > 0 else 'влево'}", flush=True)
        return True

    def _edge_begin(self, kind, ex, out, hwnd):
        """Подойти к краю ex (снаружи, со стороны out), уйти за него и выглянуть в сторону out."""
        f, ppm = self._feet(), self._ppm()
        self._edge = {"kind": kind, "ex": ex, "out": out, "hwnd": hwnd}

        def arrived():
            if not self._edge or self._edge.get("ex") != ex:
                return
            if hwnd and not self._go_behind(hwnd):
                self._edge = None
                return
            self._edge["stage"] = "in"
            self._slide_to(ex - out * 0.1 * ppm - f[1])     # за край: снаружи остаются голова и плечо
        self._walk_to(ex + out * 0.45 * ppm - f[1], then=arrived)

    def _slide_to(self, tx):
        self.walk_target = tx
        self.vx = WALK_SPEED * 0.7 * (1 if tx > self.px else -1)
        self.mode = "edge_slide"
        self._set_walking(True, self.vx)

    def _edge_tick(self, dt):
        e = self._edge
        if not e:
            self.mode = "idle"
            return
        if self.mode == "edge_slide":
            self.px += self.vx * dt
            if (self.vx > 0 and self.px >= self.walk_target) or (self.vx < 0 and self.px <= self.walk_target):
                self.px = self.walk_target
                self.mode = "idle"
                self._set_walking(False)
                if e.get("stage") == "in":
                    e["stage"] = "peek"
                    out, ex = e["out"], e["ex"]
                    self._js(f"setEdge('peek', {out}, {ex - self.px:.0f})")
                    QTimer.singleShot(random.randint(5000, 9000), lambda ee=e: self._edge_out(ee))
                else:
                    self._edge_finish()
            return
        if self.mode == "edge_push":
            h = e.get("hwnd")
            if not h or not _user32 or not _user32.IsWindow(h) or not _is_perchable(h, int(self.winId())):
                return self._edge_finish()
            step = self.vx * dt
            if abs(e["moved"] + step) >= e["dist"]:
                step = e["dir"] * e["dist"] - e["moved"]
            d = self._dpr()
            wl = wintypes.RECT()
            _user32.GetWindowRect(h, ctypes.byref(wl))     # SetWindowPos — по рамке окна, а не по видимой части
            e["fx"] = e.get("fx", float(wl.left)) + step * d
            _user32.SetWindowPos(ctypes.c_void_p(h), None, int(round(e["fx"])), wl.top, 0, 0, 0x0001 | 0x0004 | 0x0010)
            e["moved"] += step
            self.px += step
            if abs(e["moved"]) >= e["dist"] - 0.5:
                self._edge_finish()
                self._js("setEmotion('happy', 1500)")
                QTimer.singleShot(400, lambda: self._js("gesture('nod')"))

    def _edge_out(self, e):
        if self._edge is not e or e.get("stage") != "peek" or self.mode != "idle":
            return
        self._js("setEdge('')")
        e["stage"] = "out"
        self._js("setEmotion('happy', 1500)")
        f, ppm = self._feet(), self._ppm()
        QTimer.singleShot(350, lambda: self._slide_to(e["ex"] + e["out"] * 0.5 * ppm - f[1]) if self._edge is e else None)

    def _edge_finish(self):
        self._js("setEdge('')")
        self._set_walking(False)
        self._come_out()
        self._edge = None
        if self.mode in ("edge_slide", "edge_push"):
            self.mode = "idle"

    def _edge_abort(self):
        """Взяли мышкой / дали другую команду — бросает игру (и снова «поверх всех»)."""
        if self._edge or self._behind_hwnd:
            self._edge = None
            self._js("setEdge('')")
            self._come_out()
            self.px = self._clamp_x(self.px)                 # не оставаться наполовину за краем экрана
            if self.mode in ("edge_slide", "edge_push"):
                self.mode = "idle"
                self._set_walking(False)

    def _go_behind(self, hwnd):
        if not _user32:
            return False
        me = ctypes.c_void_p(int(self.winId()))
        flags = 0x0001 | 0x0002 | 0x0010                    # без перемещения/размера/активации
        _user32.SetWindowPos(me, ctypes.c_void_p(-2), 0, 0, 0, 0, flags)       # HWND_NOTOPMOST
        _user32.SetWindowPos(me, ctypes.c_void_p(hwnd), 0, 0, 0, 0, flags)     # сразу под этим окном
        self._behind_hwnd = hwnd
        return True

    def _come_out(self):
        if self._behind_hwnd and _user32:
            _user32.SetWindowPos(ctypes.c_void_p(int(self.winId())), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        self._behind_hwnd = None

    # ==================== танец под музыку ====================
    def _music_on_cfg(self):
        try:
            from settings import live_config
            return bool(live_config("music_dance", True))
        except Exception:
            return bool(load_config().get("music_dance", True))

    def _can_dance(self):
        return self.mode in ("idle",) and self.land_on is None and not self._edge

    def _on_music(self, st):
        self._music = st
        now = time.monotonic()
        if self._mdance:
            self._js(f"setMusic({json.dumps(st)})")
            if st.get("on"):
                self._music_off_since = None
            else:
                self._music_off_since = self._music_off_since or now
                if now - self._music_off_since > 2.5:
                    self._mdance_stop("музыка закончилась")
            if self._mdance == "auto" and now > self._mdance_until:
                self._mdance_stop("натанцевалась")
                self._mdance_cool = now + 60
            return
        if self._mdance_wait is not None:                       # попросили, ждём, пока найдётся такт
            if st.get("on"):
                self._mdance_wait = None
                self._mdance_start("cmd")
            elif now > self._mdance_wait:
                self._mdance_wait = None
                print("🎵 музыки не слышно — танцую просто так", flush=True)
                self._js("play('dance')")
            return
        # сама: музыка играет уже 6 с, она стоит без дела — иногда пускается в пляс
        if (st.get("on") and st.get("for", 0) > 6 and self.autonomy and self._music_on_cfg() and self._can_dance()
                and now > self._mdance_cool and not getattr(self, "_dozing", False) and random.random() < 0.06):
            self._mdance_start("auto")

    def _mdance_cmd(self):
        if self.mode == "perch":                                # сначала слезть
            self.land_on = None
            self._fall(vy=-500)
        if self._music.get("on"):
            self._mdance_start("cmd")
        else:
            self._mdance_wait = time.monotonic() + 4.0

    def _mdance_start(self, why):
        if not self._can_dance():
            if why == "cmd":
                QTimer.singleShot(1500, lambda: self._mdance_start(why) if self._can_dance() else None)
            return
        self._mdance = why
        self._music_off_since = None
        self._mdance_until = time.monotonic() + random.uniform(40, 90)
        self._set_walking(False)
        self._js(f"setMusic({json.dumps(self._music)})")
        self._js("setMusicDance(true)")
        print(f"💃 танцует под музыку ({'попросили' if why == 'cmd' else 'сама'}, {self._music.get('bpm', 0)} уд/мин)", flush=True)

    def _mdance_stop(self, why=""):
        self._mdance_wait = None
        if not self._mdance:
            return
        self._mdance = None
        self._js("setMusicDance(false)")
        print(f"💃 перестала танцевать ({why})", flush=True)

    def _schedule_think(self):
        self._brain.start(random.randint(15000, 45000))

    def _think(self):
        """Сама по себе: иногда гуляет, прыгает, садится на окна."""
        self._schedule_think()
        if not self.autonomy or self.mode in ("drag", "air", "walk", "prep", "acro") or time.monotonic() - self.last_talk < 3:
            return
        if getattr(self, "_avatar_busy", False) or self._edge or self._mdance:   # шляпа / окна / танцует — не мешать
            return
        # ночью (23:00–6:00), если 3 минуты никто не говорил и не трогал — дремлет и никуда не ходит.
        # Будят: голос, перетаскивание, клик, поглаживание, любая команда.
        hour = time.localtime().tm_hour
        quiet = time.monotonic() - max(self.last_talk, getattr(self, "_last_touch", 0.0)) > 180
        if (hour >= 23 or hour < 6) and quiet:
            if not getattr(self, "_dozing", False):
                self._dozing = True
                self._set_walking(False)
                self._js("setDoze(true)")
            return
        if getattr(self, "_dozing", False):
            self._dozing = False
            self._js("setDoze(false)")
        r = random.random()
        if self.mode == "perch":
            if r < 0.25:
                self.do_behavior("floor")
            elif r < 0.6:
                self._js("idle()")                 # оглядывается, зевает, потягивается
            return
        if r < 0.27:
            self.do_behavior("walk")
        elif r < 0.37:
            self.do_behavior("jump")
        elif r < 0.55:
            self.do_behavior("perch")
        elif r < 0.62:
            self.do_behavior("lean")
        elif r < 0.67:
            self.do_behavior(random.choice(("peek", "hide")))   # игра с окнами (толкать окна — только по просьбе)
        elif r < 0.695 and self.land_on is None:
            # изредка (~2–3 % раздумий) — трюк сама: сальто, колесо или стойка на руках
            self.do_behavior(random.choice(("flip", "cartwheel", "handstand")))
        elif r < 0.90:
            self._js("idle()")
        else:
            self._js("play('dance')")

    def _tick(self):
        now = time.monotonic()
        dt = min(0.05, now - self._t_prev)
        self._t_prev = now
        if self.mode in ("drag", "idle", "prep") or not self._feet():
            if self.mode == "idle" and self.autonomy and self.land_on is None:
                fy = self._floor_y()                       # панель задач могла сдвинуться
                if fy is not None and abs(self.py - fy) > 8:
                    self._fall()
            return
        if self.mode == "acro":
            self._acro_t += dt
            dur = self.ACRO_DUR.get(self._acro_name, 2.0)
            if self._acro_t >= dur + 0.1:
                # подвезти окно под неё (колесо) — за 0.9 с, страница компенсирует по winPos
                rc = self._acro_t - dur - 0.1
                s = min(1.0, rc / 0.9)
                self.px = self._acro_x0 + self._acro_d * s * s * (3 - 2 * s)
                self.move(int(round(self.px)), int(self.py))
                if s >= 1.0:
                    self.mode = "idle"
                    p = self.pos()                                  # последний сдвиг мог не уйти (moveEvent реже 12 мс)
                    self._js(f"winPos({p.x()},{p.y()},{time.monotonic() * 1000:.1f})")
                    self._js("acroCarryDone()")
            return
        if self.mode == "walk":
            # пока говорит — стоит. Раньше окно замирало на каждом слове, а ноги продолжали шагать: между словами
            # оно снова ехало — рывки в такт речи. Теперь останавливается целиком и идёт дальше после паузы в речи.
            quiet = time.monotonic() - self.last_talk
            if quiet < 0.5 or (self._walk_pause and quiet < 1.2):
                if not self._walk_pause:
                    self._walk_pause = True
                    self._set_walking(False)
                return
            if self._walk_pause:
                self._walk_pause = False
                self._set_walking(True)
        if self.mode == "walk":
            self._walk_t = getattr(self, "_walk_t", 0.0) + dt
            # ровная скорость: раньше окно ехало «толчками» с частотой, не связанной с шагами модели,
            # и ступни проскальзывали — походка выглядела неправдоподобно
            self.px += self.vx * dt
            if (self.vx > 0 and self.px >= self.walk_target) or (self.vx < 0 and self.px <= self.walk_target):
                self.px = self.walk_target
                self.mode = "idle"
                self._set_walking(False)
                then, self.after_walk = self.after_walk, None
                if then:
                    then()
        elif self.mode == "air":
            self.vy += GRAVITY * dt
            self.px += self.vx * dt
            self.py += self.vy * dt
            fy = self._floor_y()
            if fy is not None and self.vy > 0 and self.py >= fy:
                self._js(f"land({min(1.6, max(0.5, self.vy / 1200)):.2f})")
                self.py = fy
                self.vx = self.vy = 0.0
                if self.land_on:
                    self.mode = "perch"
                    self._js("setSitting(true)")
                    self.perch_hwnd = self.land_on
                    self.perch_rel = self.px - self._rect_logical(self.land_on)[0]
                else:
                    self.mode = "idle"
            elif fy is not None and self.py > fy + 400:       # улетела за пол — на всякий случай
                self.py = fy
                self.mode = "idle"
        elif self.mode in ("edge_slide", "edge_push"):
            self._edge_tick(dt)
        elif self.mode == "perch":
            h = self.perch_hwnd
            if not h or not _user32.IsWindow(h) or not _is_perchable(h, int(self.winId())):
                self.perch_hwnd = None
                self._fall()                                   # окно закрыли/свернули — падает
            else:
                l, t, r, b = self._rect_logical(h)
                f = self._feet()
                self.px = l + self.perch_rel                   # окно двигают — едет вместе с ним
                self.py = t - f[0] + self.perch_sink
        self.move(int(self.px), int(self.py))

    # ==================== WebSocket ====================
    def _start_ws_server(self):
        async def handler(websocket):
            print("🔗 main.py подключился к Live2D!")
            self._ws_conn, self._ws_loop = websocket, asyncio.get_running_loop()
            await asyncio.sleep(0.5)
            self._run_js("if(window.roxyLive2D) window.roxyLive2D.setMouthOpen(0.0)")
            try:
                async for message in websocket:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    t = data.get("type")
                    try:
                        if t == "mouth":
                            v = max(0.0, min(1.0, float(data.get("value", 0))))
                            if v > 0.05:
                                self.last_talk = time.monotonic()
                            self._run_js(f"if(window.roxyLive2D) window.roxyLive2D.setMouthOpen({v})")
                        elif t == "viseme":
                            vals = [max(0.0, min(1.0, float(data.get(k, 0)))) for k in ("aa", "ih", "ou", "ee", "oh")]
                            self._run_js("if(window.roxyLive2D && window.roxyLive2D.setViseme) window.roxyLive2D.setViseme(" +
                                         ",".join(f"{v:.2f}" for v in vals) + ")")
                        elif t == "emotion":
                            name = str(data.get("name", "neutral"))
                            if name in ALLOWED_EMOTIONS:      # имя идёт в JS — только из списка
                                dur = int(data.get("duration", 6000))
                                self._run_js(f"if(window.roxyLive2D) window.roxyLive2D.setEmotion('{name}', {dur})")
                        elif t == "behavior":
                            name = str(data.get("name", ""))
                            if name in BEHAVIORS:
                                self.behavior_signal.emit(name)
                    except (TypeError, ValueError):
                        pass
            except ConnectionClosed:
                print("🔌 main.py отключился.")
            except Exception as e:
                print(f"⚠️ WS Error: {e}")

        async def main():
            # ping/pong отключён: websocket-client в main.py не читает сокет и не отвечает на ping.
            # origins: браузерные страницы (чужие сайты) подключиться не смогут — их Origin не в списке.
            # None = клиент без заголовка Origin (не браузер). max_size: сообщения команд крошечные.
            async with websockets.serve(handler, "127.0.0.1", PORT, ping_interval=None, ping_timeout=None,
                                        origins=[None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"],
                                        max_size=64 * 1024):
                print(f"✅ WebSocket сервер на порту {PORT}")
                await asyncio.Future()

        asyncio.run(main())


def _is_port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


if __name__ == '__main__':
    if _is_port_open("127.0.0.1", PORT):
        print(f"💡 Порт {PORT} уже занят. Live2D уже запущен.")
        sys.exit(0)

    # Мигание: прозрачное окно Chromium на Windows иногда показывает пустой кадр, когда собирает картинку через
    # DirectComposition (аппаратные слои композиции). Без неё кадр всегда идёт обычным путём. Если вдруг стало хуже —
    # config.json: "live2d_direct_composition": true вернёт как было.
    _flags = ["--enable-transparent-visuals"]
    if not load_config().get("live2d_direct_composition", False):
        _flags.append("--disable-direct-composition")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_flags)
    import signal
    app = QApplication(sys.argv)
    # Ctrl+C в консоли main.py доходит и сюда: закрываемся тихо, без трейсбека
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    window = Live2DWindow()
    window.show()
    try:
        code = app.exec()
    except KeyboardInterrupt:
        code = 0
    if keyboard is not None:
        try:
            keyboard.unhook_all()
        except Exception:
            pass
    sys.exit(code)
