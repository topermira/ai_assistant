"""Лаунчер Рокси: запустить/остановить, настройки тумблерами, журнал, автозапуск, ярлык на рабочий стол.

    .venv\\Scripts\\pythonw.exe roxy_launcher.py              # окно
    .venv\\Scripts\\pythonw.exe roxy_launcher.py --autostart  # из автозагрузки: сразу запускает Рокси, сидит в трее

Настройки пишутся в config.json (через settings.save_config — секреты не трогаются и не показываются).
Почти все применяются при следующем запуске Рокси — лаунчер подскажет и предложит перезапустить.
Остановка — мягкая: лаунчер кладёт temp/roxy_stop.flag, main.py замечает его и выключается с обычной уборкой
(память, окно аватара, RVC). Если за 8 с не вышла — завершает дерево процессов.
"""
import ctypes
import os
import traceback
import time as _time

_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "launcher.log")


def _log(msg):
    """pythonw не показывает консоль — всё важное и все падения пишем в logs/launcher.log."""
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"{_time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _excepthook(t, v, tb):
    _log("ОШИБКА:\n" + "".join(traceback.format_exception(t, v, tb)))


sys_excepthook_set = False
try:
    import sys as _sys
    _sys.excepthook = _excepthook
    sys_excepthook_set = True
except Exception:
    pass
import re
import socket
import subprocess
import sys
import threading

from PySide6.QtCore import (Qt, QTimer, QPropertyAnimation, QEasingCurve, Property, QRectF, Signal, QObject,
                            QSize, QPointF)
from PySide6.QtGui import (QPainter, QColor, QFont, QPixmap, QPainterPath, QIcon, QAction, QPen, QImage,
                           QLinearGradient, QFontDatabase, QTextCursor)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QAbstractButton, QFrame, QScrollArea, QPlainTextEdit, QSystemTrayIcon, QMenu,
                               QButtonGroup, QSizePolicy, QSlider, QComboBox, QLineEdit)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

import settings
from settings import load_config, save_config, PROJECT_DIR, TEMP_DIR, LIVE2D_PORT, LOG_DIR

IS_WIN = os.name == "nt"
APP_ID = "RoxyLauncher"
STOP_FLAG = os.path.join(TEMP_DIR, "roxy_stop.flag")
AVATAR_IMG = os.path.join(PROJECT_DIR, "live2d_assets", "models", "roxy", "Roxy.jpg")
ICON_FILE = os.path.join(PROJECT_DIR, "live2d_assets", "roxy.ico")
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
STARTUP_LNK = os.path.join(STARTUP_DIR, "Roxy.lnk")

# ---- оформление ----
BG, CARD, CARD_BORDER, TEXT, MUTED = "#14161b", "#1c1f26", "#272b35", "#eceef3", "#8b91a1"
ACCENT, ACCENT2, WARM, GOOD, WARN, BAD = "#6f8ff2", "#9a7cf0", "#c39a63", "#53c98b", "#e6b450", "#e5685f"

# (ключ, заголовок, подпись, по умолчанию)
SECTIONS = [
    ("Разговор", [
        ("barge_in", "Перебивание голосом", "Заговори, пока она отвечает, — замолчит и выслушает", True),
        ("confirm_dangerous", "Спрашивать перед опасным", "Удаление, отправка в Telegram и т. п. — только после «да»", True),
        ("use_llm_router", "Умное понимание команд", "Фразу не узнал шаблон — разберёт нейросеть", True),
    ]),
    ("Аватар", [
        ("pet_autonomy", "Живёт сама", "Гуляет, садится на окна, прячется, колдует", True),
        ("music_dance", "Танцует под музыку", "Играет музыка — может сама пуститься в пляс", True),
        ("live2d_shadows", "Тени", "Мягкие тени на одежде и на полу", True),
    ]),
    ("Скорость и память", [
        ("rvc_worker", "Держать аниме-голос загруженным", "Отвечает быстрее, но занимает память", True),
        ("preload_whisper", "Заранее грузить распознавание речи", "Не ждать при плохом интернете", True),
    ]),
    ("Телефон (Telegram)", [
        ("tg_screen_after_mouse", "Снимок после мыши и клавиш", "Команда с телефона — сразу пришлёт экран с сеткой и курсором", True),
        ("tg_notify_timers", "Таймеры и напоминания на телефон", "Сработал таймер — продублирует сообщением в чат", True),
    ]),
    ("Приватность", [
        ("send_screenshots_to_cloud", "Скриншоты в облако без вопроса", "Для «глянь на экран»; выключено — спросит", False),
    ]),
]


def _mix(a, b, t):
    a, b = QColor(a), QColor(b)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _python_exe():
    """Python проекта (консольный, чтобы читать вывод), окно консоли не показываем."""
    venv = os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv):
        return venv
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def _pythonw_exe():
    venv = os.path.join(PROJECT_DIR, ".venv", "Scripts", "pythonw.exe")
    return venv if os.path.exists(venv) else sys.executable


def _port_open(port, timeout=0.15):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                          capture_output=True, text=True, creationflags=0x08000000 if IS_WIN else 0)


def _q(s):
    return "'" + s.replace("'", "''") + "'"


def make_shortcut(path, args):
    icon = f"$s.IconLocation={_q(ICON_FILE)};" if os.path.exists(ICON_FILE) else ""
    r = _ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_q(path)});"
            f"$s.TargetPath={_q(_pythonw_exe())};$s.Arguments={_q(args)};$s.WorkingDirectory={_q(PROJECT_DIR)};"
            f"{icon}$s.Description='Рокси';$s.Save()")
    return r.returncode == 0 and os.path.exists(path), (r.stderr or r.stdout).strip()


def desktop_dir():
    r = _ps("[Environment]::GetFolderPath('Desktop')")
    d = (r.stdout or "").strip()
    return d if d and os.path.isdir(d) else os.path.join(os.path.expanduser("~"), "Desktop")


def _trash(path):
    try:
        from send2trash import send2trash
        send2trash(path)
    except ImportError:
        os.remove(path)


def autostart_on():
    if not os.path.exists(STARTUP_LNK):
        return False
    r = _ps(f"(New-Object -ComObject WScript.Shell).CreateShortcut({_q(STARTUP_LNK)}).Arguments")
    return "roxy_launcher" in (r.stdout or "")


# ==================== виджеты ====================
class Toggle(QAbstractButton):
    """Тумблер-«пилюля» с плавным ходом."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._p = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._go)

    def sizeHint(self):
        return QSize(46, 26)

    def _go(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._p)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def set_silent(self, on):
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._p = 1.0 if on else 0.0
        self.update()

    def _get(self):
        return self._p

    def _set(self, v):
        self._p = v
        self.update()

    knob = Property(float, _get, _set)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, _mix("#353a46", ACCENT, self._p))
        g.setColorAt(1, _mix("#353a46", ACCENT2, self._p))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._p
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QRectF(x, r.top() + 4, d, d))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Row(QFrame):
    """Строка настройки: заголовок, подпись, тумблер справа; кликается вся строка."""

    def __init__(self, title, sub, parent=None):
        super().__init__(parent)
        self.setObjectName("row")
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 12, 10)
        v = QVBoxLayout()
        v.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("rowTitle")
        s = QLabel(sub)
        s.setObjectName("rowSub")
        s.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(s)
        h.addLayout(v, 1)
        self.toggle = Toggle()
        h.addWidget(self.toggle, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggle.toggle()


class Combo(QComboBox):
    """Выпадающий список со стрелкой: стиль из STYLE убирает стандартную, рисуем свою."""

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(MUTED), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 18, self.height() / 2
        p.drawPolyline([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)])
        p.end()


def card(title):
    f = QFrame()
    f.setObjectName("card")
    v = QVBoxLayout(f)
    v.setContentsMargins(6, 10, 6, 6)
    v.setSpacing(0)
    h = QLabel(title.upper())
    h.setObjectName("cardTitle")
    v.addWidget(h)
    return f, v


def divider():
    d = QFrame()
    d.setObjectName("div")
    d.setFixedHeight(1)
    return d


def avatar_pixmap(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(path)
    img = QImage(AVATAR_IMG)
    if not img.isNull():
        w = img.width()
        src = img.copy(int(w * 0.31), int(w * 0.27), int(w * 0.38), int(w * 0.38))     # лицо
        p.drawImage(QRectF(0, 0, size, size), src)
    else:
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor(ACCENT))
        g.setColorAt(1, QColor(ACCENT2))
        p.fillRect(0, 0, size, size, g)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", int(size * 0.42), QFont.Bold)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "Р")
    p.end()
    return pm


# ==================== процесс Рокси ====================
class Roxy(QObject):
    line = Signal(str)
    state = Signal(str)          # off | starting | on | stopping | external

    def __init__(self):
        super().__init__()
        self.proc = None
        self.st = "off"
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._check)
        self._poll.start(1500)
        self._ready_timer = QTimer(self)
        self._ready_timer.setSingleShot(True)
        self._ready_timer.timeout.connect(lambda: self._set("on") if self.st == "starting" else None)

    def _set(self, st):
        if st != self.st:
            self.st = st
            self.state.emit(st)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running() or self.st == "external":
            return
        try:
            os.makedirs(TEMP_DIR, exist_ok=True)
            if os.path.exists(STOP_FLAG):
                os.remove(STOP_FLAG)
        except OSError:
            pass
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "ROXY_LAUNCHER": "1"}
        try:
            self.proc = subprocess.Popen(
                [_python_exe(), os.path.join(PROJECT_DIR, "main.py")], cwd=PROJECT_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                creationflags=0x08000000 if IS_WIN else 0)        # CREATE_NO_WINDOW
        except Exception as e:
            self.line.emit(f"⚠️ Не удалось запустить: {e}")
            return
        self._set("starting")
        self._ready_timer.start(25000)                           # на случай, если «готова» не напечатается
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, p):
        for ln in p.stdout:
            ln = ln.rstrip()
            if ln:
                self.line.emit(ln)
        p.wait()

    def stop(self):
        if self.st in ("off", "stopping"):
            return
        try:
            os.makedirs(TEMP_DIR, exist_ok=True)
            open(STOP_FLAG, "w").close()
        except OSError:
            pass
        self._set("stopping")
        self._kill_timer.start(8000)

    def _force_kill(self):
        if self.running():
            self.line.emit("⏹ Не выключилась сама за 8 с — завершаю процессы.")
            if IS_WIN:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True,
                               creationflags=0x08000000)
            else:
                self.proc.kill()

    def _check(self):
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self._kill_timer.stop()
            try:
                if os.path.exists(STOP_FLAG):
                    os.remove(STOP_FLAG)
            except OSError:
                pass
            if self.st not in ("stopping",) and code not in (0, 42):
                self.line.emit(f"⚠️ Рокси завершилась с кодом {code} — смотри журнал выше.")
            self._set("off")
        if self.proc is None:
            ext = _port_open(LIVE2D_PORT)
            if ext and self.st == "off":
                self._set("external")
            elif not ext and self.st in ("external", "stopping"):
                self._set("off")

    def mark_ready(self):
        if self.st == "starting":
            self._set("on")


# ==================== окно ====================
STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 10pt; }}
QScrollArea, #inner {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 1px; }}
QScrollBar::handle:vertical {{ background: #2e3340; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
#card {{ background: {CARD}; border: 1px solid {CARD_BORDER}; border-radius: 14px; }}
#card QLabel, #row, #row QLabel {{ background: transparent; }}
#cardTitle {{ color: {MUTED}; font-size: 8pt; font-weight: 600; letter-spacing: 1.5px; padding: 0 14px 6px 14px; }}
#row {{ border-radius: 10px; }}
#row:hover {{ background: #232731; }}
#rowTitle {{ font-size: 10.5pt; font-weight: 600; }}
#rowSub {{ color: {MUTED}; font-size: 9pt; }}
#div {{ background: {CARD_BORDER}; margin: 0 14px; }}
#hero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #232a45, stop:0.55 #1f2233, stop:1 #2a2232);
         border: 1px solid #2d3350; border-radius: 18px; }}
#hero QLabel {{ background: transparent; }}
#name {{ font-size: 20pt; font-weight: 700; }}
#tag {{ color: {MUTED}; font-size: 9.5pt; }}
#pill {{ border-radius: 11px; padding: 3px 10px 3px 8px; font-size: 9pt; font-weight: 600; }}
#big {{ border: none; border-radius: 12px; padding: 12px; font-size: 11.5pt; font-weight: 700; color: white;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2}); }}
#big:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #829ef5, stop:1 #ab90f3); }}
#big:disabled {{ background: #2c303b; color: {MUTED}; }}
#big[stop="true"] {{ background: #2c303b; color: {TEXT}; border: 1px solid #3a3f4c; }}
#big[stop="true"]:hover {{ background: #352f35; border-color: {BAD}; color: #ffd9d5; }}
#ghost {{ background: #232731; border: 1px solid {CARD_BORDER}; border-radius: 10px; padding: 8px 12px; color: {TEXT}; }}
#ghost:hover {{ background: #2a2f3b; border-color: #39404f; }}
#seg {{ background: #15171d; border: 1px solid {CARD_BORDER}; border-radius: 11px; }}
#seg QPushButton {{ background: transparent; border: none; border-radius: 8px; padding: 8px 6px; color: {MUTED}; font-weight: 600; }}
#seg QPushButton:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2}); color: white; }}
#seg QPushButton:hover:!checked {{ color: {TEXT}; }}
#banner {{ background: #2e2817; border: 1px solid #5a4a1f; border-radius: 12px; }}
#banner QLabel {{ background: transparent; color: #f3d48a; }}
#bannerBtn {{ background: {WARN}; color: #1b1608; border: none; border-radius: 8px; padding: 6px 12px; font-weight: 700; }}
#bannerBtn:hover {{ background: #f0c766; }}
#log {{ background: #101216; border: 1px solid {CARD_BORDER}; border-radius: 10px; color: #b9c0cf;
        font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 8.5pt; padding: 6px; }}
#last {{ color: {MUTED}; font-size: 9pt; background: transparent; }}
#foot {{ color: #5d6372; font-size: 8pt; background: transparent; }}
#pct {{ color: {TEXT}; font-weight: 600; min-width: 42px; background: transparent; }}
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: #2a2e39; }}
QSlider::sub-page:horizontal {{ height: 6px; border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2}); }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: #f2f4f8; }}
QSlider::handle:horizontal:hover {{ background: white; }}
QComboBox {{ background: #15171d; border: 1px solid {CARD_BORDER}; border-radius: 9px; padding: 6px 28px 6px 10px; }}
QComboBox:hover {{ border-color: #39404f; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1c1f26; border: 1px solid {CARD_BORDER}; selection-background-color: #2f3a5c; outline: none; }}
#chat {{ background: rgba(10, 12, 18, 0.55); border: 1px solid #353c58; border-radius: 11px; padding: 9px 12px; color: {TEXT}; }}
#chat:focus {{ border-color: {ACCENT}; }}
#chat:disabled {{ color: {MUTED}; border-color: #2a2e3d; }}
#send {{ border: none; border-radius: 11px; padding: 0 14px; font-weight: 700; color: white; min-height: 36px;
         background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2}); }}
#send:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #829ef5, stop:1 #ab90f3); }}
#send:disabled {{ background: #2c303b; color: {MUTED}; }}
"""

# настройки, которые Рокси подхватывает сама, без перезапуска
LIVE_KEYS = {"voice_volume", "music_dance", "end_silence", "tg_screen_after_mouse", "tg_notify_timers"}
_MAPPER = re.compile(r"sound mapper|переназначение|первичный драйвер|primary sound", re.I)


def input_devices():
    """Имена микрофонов в том же списке (host API по умолчанию), по которому их ищет stt.py."""
    try:
        import sounddevice as sd
        api = sd.default.hostapi
        out = []
        for d in sd.query_devices():
            n = d.get("name", "")
            if d.get("max_input_channels", 0) > 0 and d.get("hostapi") == api and not _MAPPER.search(n) and n not in out:
                out.append(n)
        return out
    except Exception as e:
        _log(f"input_devices: {e}")
        return []

_PILL = {
    "off": ("Выключена", "#2a2d36", MUTED),
    "starting": ("Просыпается…", "#33301f", WARN),
    "on": ("Работает", "#1d3328", GOOD),
    "stopping": ("Засыпает…", "#33301f", WARN),
    "external": ("Работает (запущена не отсюда)", "#1d3328", GOOD),
}
_READY = re.compile(r"Перебивание голосом:|пружинных|Рокси готова|слушаю", re.I)
_NOISE = re.compile(r"^\[Live2D\]\s*(js:|🔦|⚡)")


class Launcher(QWidget):
    def __init__(self, autostart=False):
        super().__init__()
        self.setWindowTitle("Рокси")
        self.setObjectName("root")
        self.setMinimumSize(470, 640)
        self.resize(490, 800)
        self.setStyleSheet(STYLE)
        icon = QIcon(avatar_pixmap(256))
        self.setWindowIcon(icon)
        self._ensure_ico()
        self.cfg = load_config()
        self._quitting = False
        self._restart_pending = False

        self.roxy = Roxy()
        self.roxy.line.connect(self._on_line)
        self.roxy.state.connect(self._on_state)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        inner = QWidget()
        inner.setObjectName("inner")
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 18, 18, 14)
        lay.setSpacing(14)

        # ---- шапка ----
        hero = QFrame()
        hero.setObjectName("hero")
        hv = QVBoxLayout(hero)
        hv.setContentsMargins(18, 18, 18, 18)
        hv.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(14)
        av = QLabel()
        av.setPixmap(avatar_pixmap(72))
        av.setFixedSize(72, 72)
        top.addWidget(av)
        nv = QVBoxLayout()
        nv.setSpacing(4)
        name = QLabel("Рокси")
        name.setObjectName("name")
        tag = QLabel("Голосовой ассистент · водный маг")
        tag.setObjectName("tag")
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        pr = QHBoxLayout()
        pr.addWidget(self.pill)
        pr.addStretch(1)
        nv.addWidget(name)
        nv.addWidget(tag)
        nv.addLayout(pr)
        top.addLayout(nv, 1)
        hv.addLayout(top)
        self.big = QPushButton()
        self.big.setObjectName("big")
        self.big.setCursor(Qt.PointingHandCursor)
        self.big.clicked.connect(self._big_clicked)
        hv.addWidget(self.big)
        cr = QHBoxLayout()
        cr.setSpacing(8)
        self.chat = QLineEdit()
        self.chat.setObjectName("chat")
        self.chat.setMaxLength(2000)
        self.chat.returnPressed.connect(self._send_text)
        self.send = QPushButton("➤")
        self.send.setObjectName("send")
        self.send.setCursor(Qt.PointingHandCursor)
        self.send.setToolTip("Отправить (Enter)")
        self.send.clicked.connect(self._send_text)
        cr.addWidget(self.chat, 1)
        cr.addWidget(self.send)
        hv.addLayout(cr)
        self.last = QLabel("")
        self.last.setObjectName("last")
        self.last.setWordWrap(True)
        self.last.hide()
        hv.addWidget(self.last)
        lay.addWidget(hero)

        # ---- баннер «нужен перезапуск» ----
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(14, 10, 10, 10)
        bl = QLabel("Изменения применятся после перезапуска Рокси")
        bl.setWordWrap(True)
        bh.addWidget(bl, 1)
        bb = QPushButton("Перезапустить")
        bb.setObjectName("bannerBtn")
        bb.setCursor(Qt.PointingHandCursor)
        bb.clicked.connect(self._restart)
        bh.addWidget(bb)
        self.banner.hide()
        lay.addWidget(self.banner)

        # ---- голос ----
        c, v = card("Голос")
        seg = QFrame()
        seg.setObjectName("seg")
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(4, 4, 4, 4)
        sh.setSpacing(4)
        self.voice = QButtonGroup(self)
        for key, text in (("anime", "Аниме-голос"), ("fast", "Быстрый")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("key", key)
            self.voice.addButton(b)
            sh.addWidget(b)
        cur = self.cfg.get("voice_mode", "anime")
        for b in self.voice.buttons():
            b.setChecked(b.property("key") == cur)
        self.voice.buttonClicked.connect(lambda b: self._set("voice_mode", b.property("key")))
        wrap = QHBoxLayout()
        wrap.setContentsMargins(10, 2, 10, 6)
        wrap.addWidget(seg)
        v.addLayout(wrap)
        vs = QLabel("Аниме — её голос (медленнее, нужна видеокарта); быстрый — облачный, отвечает почти сразу")
        vs.setObjectName("rowSub")
        vs.setWordWrap(True)
        vs.setContentsMargins(14, 0, 14, 8)
        v.addWidget(vs)
        v.addWidget(divider())

        vr = QHBoxLayout()
        vr.setContentsMargins(14, 10, 14, 4)
        vr.setSpacing(12)
        vt = QLabel("Громкость голоса")
        vt.setObjectName("rowTitle")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 150)
        self.vol.setSingleStep(5)
        self.vol.setPageStep(10)
        self.vol.setCursor(Qt.PointingHandCursor)
        try:
            cur_v = int(round(float(self.cfg.get("voice_volume", 1.0)) * 100))
        except (TypeError, ValueError):
            cur_v = 100
        self.vol.setValue(max(0, min(150, cur_v)))
        self.vol_pct = QLabel(f"{self.vol.value()}%")
        self.vol_pct.setObjectName("pct")
        self.vol_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._vol_timer = QTimer(self)
        self._vol_timer.setSingleShot(True)
        self._vol_timer.setInterval(250)              # не писать config.json на каждый пиксель ползунка
        self._vol_timer.timeout.connect(lambda: self._set("voice_volume", round(self.vol.value() / 100, 2)))
        self.vol.valueChanged.connect(lambda x: (self.vol_pct.setText(f"{x}%"), self._vol_timer.start()))
        vr.addWidget(vt)
        vr.addWidget(self.vol, 1)
        vr.addWidget(self.vol_pct)
        v.addLayout(vr)
        vh = QLabel("Только её голос, не общая громкость Windows; меняется сразу")
        vh.setObjectName("rowSub")
        vh.setContentsMargins(14, 0, 14, 8)
        v.addWidget(vh)
        v.addWidget(divider())

        # пауза, после которой Рокси решает «договорил» (stt._end_silence, 0.7–4 с; читается на лету)
        sr = QHBoxLayout()
        sr.setContentsMargins(14, 10, 14, 4)
        sr.setSpacing(12)
        st = QLabel("Пауза перед ответом")
        st.setObjectName("rowTitle")
        self.sil = QSlider(Qt.Horizontal)
        self.sil.setRange(7, 30)                      # десятые доли секунды: 0,7 … 3,0 с
        self.sil.setSingleStep(1)
        self.sil.setPageStep(2)
        self.sil.setCursor(Qt.PointingHandCursor)
        try:
            cur_s = int(round(float(self.cfg.get("end_silence", 1.3)) * 10))
        except (TypeError, ValueError):
            cur_s = 13
        self.sil.setValue(max(7, min(30, cur_s)))
        fmt_s = lambda x: f"{x / 10:.1f} с".replace(".", ",")
        self.sil_val = QLabel(fmt_s(self.sil.value()))
        self.sil_val.setObjectName("pct")
        self.sil_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._sil_timer = QTimer(self)
        self._sil_timer.setSingleShot(True)
        self._sil_timer.setInterval(250)
        self._sil_timer.timeout.connect(lambda: self._set("end_silence", round(self.sil.value() / 10, 1)))
        self.sil.valueChanged.connect(lambda x: (self.sil_val.setText(fmt_s(x)), self._sil_timer.start()))
        for lbl in (vt, st):                           # оба ползунка начинаются с одной линии
            lbl.setMinimumWidth(185)
        sr.addWidget(st)
        sr.addWidget(self.sil, 1)
        sr.addWidget(self.sil_val)
        v.addLayout(sr)
        sh2 = QLabel("Сколько тишины ждать, чтобы понять, что ты договорил. Меньше — отвечает быстрее, "
                     "но может перебить на паузе; меняется сразу")
        sh2.setObjectName("rowSub")
        sh2.setWordWrap(True)
        sh2.setContentsMargins(14, 0, 14, 8)
        v.addWidget(sh2)
        v.addWidget(divider())

        mr = QHBoxLayout()
        mr.setContentsMargins(14, 10, 14, 4)
        mr.setSpacing(12)
        mt = QLabel("Микрофон")
        mt.setObjectName("rowTitle")
        self.mic = Combo()
        self.mic.setCursor(Qt.PointingHandCursor)
        self.mic.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.mic.setMinimumContentsLength(12)
        self._fill_mics()
        self.mic.currentIndexChanged.connect(lambda _i: self._set("mic_device", self.mic.currentData() or ""))
        mr.addWidget(mt)
        mr.addWidget(self.mic, 1)
        v.addLayout(mr)
        mh = QLabel("Применится после перезапуска Рокси")
        mh.setObjectName("rowSub")
        mh.setContentsMargins(14, 0, 14, 10)
        v.addWidget(mh)
        lay.addWidget(c)

        # ---- тумблеры ----
        self.toggles = {}
        for title, rows in SECTIONS:
            c, v = card(title)
            for i, (key, t, s, dflt) in enumerate(rows):
                if i:
                    v.addWidget(divider())
                r = Row(t, s)
                r.toggle.set_silent(bool(self.cfg.get(key, dflt)))
                r.toggle.toggled.connect(lambda on, k=key: self._set(k, on))
                v.addWidget(r)
                self.toggles[key] = r.toggle
            lay.addWidget(c)

        # ---- система ----
        c, v = card("Система")
        r = Row("Запускать вместе с Windows", "Лаунчер сам включит Рокси и спрячется в трей")
        r.toggle.set_silent(autostart_on() if IS_WIN else False)
        r.toggle.toggled.connect(self._autostart)
        self.auto_toggle = r.toggle
        v.addWidget(r)
        v.addWidget(divider())
        btns = QHBoxLayout()
        btns.setContentsMargins(10, 10, 10, 6)
        btns.setSpacing(8)
        for text, fn in (("Ярлык на рабочий стол", self._desktop), ("Папка с журналами", self._open_logs)):
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            btns.addWidget(b)
        v.addLayout(btns)
        self.sys_msg = QLabel("")
        self.sys_msg.setObjectName("rowSub")
        self.sys_msg.setContentsMargins(14, 0, 14, 6)
        self.sys_msg.setWordWrap(True)
        self.sys_msg.hide()
        v.addWidget(self.sys_msg)
        lay.addWidget(c)

        # ---- журнал ----
        c, v = card("Журнал")
        self.log_btn = QPushButton("Показать журнал")
        self.log_btn.setObjectName("ghost")
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.clicked.connect(self._toggle_log)
        lw = QHBoxLayout()
        lw.setContentsMargins(10, 2, 10, 8)
        lw.addWidget(self.log_btn)
        v.addLayout(lw)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMinimumHeight(240)
        self.log.hide()
        lv = QHBoxLayout()
        lv.setContentsMargins(10, 0, 10, 8)
        lv.addWidget(self.log)
        v.addLayout(lv)
        lay.addWidget(c)

        foot = QLabel("Настройки — в config.json · ключи и пароли тут не показываются")
        foot.setObjectName("foot")
        foot.setAlignment(Qt.AlignCenter)
        lay.addWidget(foot)
        lay.addStretch(1)

        # ---- трей ----
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Рокси")
        m = QMenu()
        m.addAction(QAction("Открыть", self, triggered=self._show))
        self.tray_act = QAction("Включить Рокси", self, triggered=self._big_clicked)
        m.addAction(self.tray_act)
        m.addSeparator()
        m.addAction(QAction("Выключить всё и выйти", self, triggered=self._quit_all))
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self._show() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

        self._on_state("off")
        self.roxy._check()
        if autostart:
            QTimer.singleShot(300, self.roxy.start)

    def _msg(self, text):
        self.sys_msg.setText(text)
        self.sys_msg.setVisible(bool(text))

    def _say(self, text):
        self.last.setText(text)
        self.last.setVisible(bool(text))

    def _fill_mics(self):
        cur = str(self.cfg.get("mic_device", "") or "")
        self.mic.blockSignals(True)
        self.mic.clear()
        self.mic.addItem("Системный по умолчанию", "")
        names = input_devices()
        for n in names:
            self.mic.addItem(n, n)
        if cur and cur not in names:
            self.mic.addItem(f"{cur} (не подключён)", cur)
        i = self.mic.findData(cur)
        self.mic.setCurrentIndex(max(0, i))
        self.mic.blockSignals(False)

    def _send_text(self):
        text = self.chat.text().strip()
        if not text or self.roxy.st not in ("on", "external"):
            return
        try:
            os.makedirs(TEMP_DIR, exist_ok=True)
            name = f"roxy_typed_{_time.time_ns()}.txt"
            tmp = os.path.join(TEMP_DIR, name + ".part")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, os.path.join(TEMP_DIR, name))   # Рокси видит файл только целиком
        except OSError as e:
            self._say(f"Не получилось отправить: {e}")
            return
        self.chat.clear()
        self._say(f"⌨️ Ты написал: {text[:120]}")

    # ---- настройки ----
    def _set(self, key, val):
        try:
            cfg = load_config()                # свежая копия: Рокси могла сама поменять голос командой
            cfg[key] = val
            save_config(cfg, from_launcher=True)
            self.cfg = cfg
        except Exception as e:
            self._msg(f"Не получилось сохранить настройку: {e}")
            return
        if key not in LIVE_KEYS and self.roxy.st in ("on", "starting", "external"):
            self.banner.show()

    def _autostart(self, on):
        if not IS_WIN:
            return
        try:
            if on:
                os.makedirs(STARTUP_DIR, exist_ok=True)
                ok, err = make_shortcut(STARTUP_LNK, f'"{os.path.join(PROJECT_DIR, "roxy_launcher.py")}" --autostart')
                self._msg("Рокси будет включаться вместе с Windows." if ok else f"Не получилось: {err}")
                if not ok:
                    self.auto_toggle.set_silent(False)
            else:
                if os.path.exists(STARTUP_LNK):
                    _trash(STARTUP_LNK)
                self._msg("Убрала из автозапуска.")
        except Exception as e:
            self._msg(f"Не получилось: {e}")

    def _desktop(self):
        if not IS_WIN:
            return
        path = os.path.join(desktop_dir(), "Рокси.lnk")
        ok, err = make_shortcut(path, f'"{os.path.join(PROJECT_DIR, "roxy_launcher.py")}"')
        self._msg("Ярлык «Рокси» на рабочем столе готов." if ok else f"Не получилось: {err}")

    def _open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        if IS_WIN:
            os.startfile(LOG_DIR)

    def _ensure_ico(self):
        if not os.path.exists(ICON_FILE):
            try:
                avatar_pixmap(256).toImage().save(ICON_FILE, "ICO")
            except Exception:
                pass

    # ---- запуск/остановка ----
    def _big_clicked(self):
        st = self.roxy.st
        if st == "off":
            self.banner.hide()
            self.roxy.start()
        elif st in ("on", "starting", "external"):
            self.roxy.stop()

    def _restart(self):
        self.banner.hide()
        self._restart_pending = True
        self.roxy.stop()

    def _on_state(self, st):
        text, bg, fg = _PILL[st]
        self.pill.setText(f"●  {text}")
        self.pill.setStyleSheet(f"background:{bg}; color:{fg};")
        busy = st in ("starting", "stopping")
        self.big.setText({"off": "Разбудить Рокси", "starting": "Выключить", "on": "Выключить",
                          "stopping": "Засыпает…", "external": "Выключить"}[st])
        self.big.setProperty("stop", "true" if st in ("on", "starting", "external") else "false")
        self.big.style().unpolish(self.big)
        self.big.style().polish(self.big)
        self.big.setEnabled(st != "stopping")
        talk = st in ("on", "external")
        self.chat.setEnabled(talk)
        self.send.setEnabled(talk)
        self.chat.setPlaceholderText("Написать Рокси…" if talk else
                                     ("Она просыпается…" if st == "starting" else "Сначала разбуди Рокси"))
        self.tray_act.setText("Включить Рокси" if st == "off" else "Выключить Рокси")
        self.tray.setToolTip(f"Рокси — {text.lower()}")
        if st == "off":
            self.banner.hide()
            self._say("")
            if getattr(self, "_restart_pending", False):
                self._restart_pending = False
                QTimer.singleShot(600, self.roxy.start)
            elif self._quitting:
                QApplication.quit()
        if st == "on":
            self._say("Скажи «Рокси» или напиши ей прямо здесь")

    def _on_line(self, ln):
        if not _NOISE.match(ln):
            self.log.appendPlainText(ln)
            self.log.moveCursor(QTextCursor.End)
        if _READY.search(ln):
            self.roxy.mark_ready()
        m = re.search(r"(👤 Ты сказал:.*|🗣.*|⚠️.*|❌.*)", ln)
        if m:
            self._say(m.group(1)[:140])

    def _toggle_log(self):
        vis = not self.log.isVisible()
        self.log.setVisible(vis)
        self.log_btn.setText("Скрыть журнал" if vis else "Показать журнал")

    # ---- окно/трей ----
    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_all(self):
        if self.roxy.st in ("on", "starting", "external"):
            self._quitting = True
            self.roxy.stop()
            QTimer.singleShot(10000, QApplication.quit)
        else:
            QApplication.quit()

    def closeEvent(self, e):
        if self._quitting:
            e.accept()
            return
        if self.roxy.st != "off":
            e.ignore()
            self.hide()
            if not getattr(self, "_told", False):
                self._told = True
                self.tray.showMessage("Рокси", "Лаунчер в трее. Выключить всё — правой кнопкой по значку.",
                                      QSystemTrayIcon.Information, 3500)
        else:
            e.accept()
            QApplication.quit()

    def showEvent(self, e):
        super().showEvent(e)
        try:
            self._fill_mics()                       # гарнитуру могли подключить, пока окно было в трее
        except Exception:
            pass
        if IS_WIN:                                  # тёмная рамка окна Windows 10/11
            try:
                hwnd = int(self.winId())
                v = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)
            except Exception:
                pass


def main():
    if IS_WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Roxy.Launcher")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # один лаунчер: второй запуск просто показывает первый
    sock = QLocalSocket()
    sock.connectToServer(APP_ID)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(APP_ID)
    server = QLocalServer()
    server.listen(APP_ID)
    autostart = "--autostart" in sys.argv
    w = Launcher(autostart=autostart)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w._show()))
    if not autostart:
        w.show()
    return app.exec()


if __name__ == "__main__":
    _log(f"старт: {sys.executable} {' '.join(sys.argv[1:])}")
    try:
        code = main()
    except Exception:
        _log("ОШИБКА при запуске:\n" + traceback.format_exc())
        try:
            ctypes.windll.user32.MessageBoxW(None, "Лаунчер не запустился. Подробности — в logs\\launcher.log",
                                             "Рокси", 0x10)
        except Exception:
            pass
        code = 1
    _log(f"выход: {code}")
    sys.exit(code)
