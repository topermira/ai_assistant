"""Консоль Рокси (tkinter) + лог в файл.

Раньше print из других потоков напрямую трогал tkinter (он не потокобезопасен).
Теперь строки идут в очередь, GUI-поток забирает их сам каждые 100 мс.
"""
import re
import sys
import queue
import logging
import logging.handlers
import tkinter as tk

from settings import LOG_FILE, OPENROUTER_API_KEY, GROQ_API_KEY, TG_API_HASH, mask_secret

GUI_ROOT = None
_gui_user_wants_visible = False
_lines = queue.Queue()

_logger = logging.getLogger("roxy")
if not _logger.handlers:
    _h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)


# Страховка: если библиотека напечатает ключ в тексте ошибки, в консоль/лог он попадёт уже замаскированным.
_KEY_SHAPES = re.compile(r"sk-or-v1-[0-9a-fA-F]{20,}|gsk_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_\-]{20,}|\bsk-[A-Za-z0-9_\-]{32,}")
_KNOWN_SECRETS = [v for v in (OPENROUTER_API_KEY, GROQ_API_KEY, TG_API_HASH) if v and len(v) >= 8]


def redact(text):
    for v in _KNOWN_SECRETS:
        if v in text:
            text = text.replace(v, mask_secret(v))
    return _KEY_SHAPES.sub(lambda m: mask_secret(m.group(0)), text)


class ConsoleRedirector:
    """Пишет в исходный stdout, в лог-файл и в очередь для GUI."""

    def __init__(self, original):
        self.original = original
        self._buf = ""

    def write(self, s):
        s = redact(s)
        try:
            if self.original:
                self.original.write(s)
        except Exception:
            pass
        _lines.put(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                try:
                    _logger.info(line)
                except Exception:
                    pass

    def flush(self):
        try:
            if self.original:
                self.original.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return getattr(self.original, "encoding", None) or "utf-8"


def install_redirect():
    sys.stdout = ConsoleRedirector(sys.__stdout__)
    sys.stderr = ConsoleRedirector(sys.__stderr__)


def gui_call(fn, *args, **kwargs):
    if GUI_ROOT is None:
        return
    try:
        GUI_ROOT.after(0, lambda: fn(*args, **kwargs))
    except Exception as e:
        print(f"⚠️ GUI: {e}")


def is_visible_wanted():
    return _gui_user_wants_visible


def set_visible(visible, force_foreground=None):
    global _gui_user_wants_visible
    _gui_user_wants_visible = visible
    if GUI_ROOT is None:
        return
    if visible:
        gui_call(GUI_ROOT.deiconify)
        gui_call(GUI_ROOT.attributes, "-topmost", True)
        if force_foreground:
            gui_call(lambda: force_foreground(int(GUI_ROOT.winfo_id())))
    else:
        gui_call(GUI_ROOT.withdraw)


def hide_temporarily():
    if GUI_ROOT is not None:
        gui_call(GUI_ROOT.withdraw)


_ENTRY = None


def show_input(force_foreground=None):
    """Показать консоль и поставить курсор в поле ввода (пункт трея «Написать Рокси…»)."""
    def _do():
        set_visible(True, force_foreground=force_foreground)
        if _ENTRY:
            entry, clear_hint = _ENTRY
            GUI_ROOT.after(150, clear_hint)
    gui_call(_do)


def start_gui(on_close, on_text=None):
    global GUI_ROOT
    root = tk.Tk()
    GUI_ROOT = root
    root.title("Roxy Console")
    root.geometry("520x290+100+100")
    root.attributes("-topmost", True)
    root.overrideredirect(True)
    root.configure(bg="#1e1e1e")
    root.withdraw()

    title_bar = tk.Frame(root, bg="#333333", height=25)
    title_bar.pack(fill=tk.X)
    tk.Label(title_bar, text="🤖 Roxy Assistant", bg="#333333", fg="white",
             font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=10)
    tk.Button(title_bar, text="X", bg="#333333", fg="red", bd=0, command=on_close).pack(side=tk.RIGHT, padx=5)

    drag = {"x": 0, "y": 0}

    def sm(e): drag.update(x=e.x, y=e.y)
    def dm(e): root.geometry(f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}")

    title_bar.bind("<ButtonPress-1>", sm)
    title_bar.bind("<B1-Motion>", dm)

    # письменный ввод: строка внизу, Enter — отправить Рокси (как будто сказал голосом)
    if on_text is not None:
        bar = tk.Frame(root, bg="#1e1e1e")
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        entry = tk.Entry(bar, bg="#2b2b2b", fg="white", insertbackground="white", font=("Segoe UI", 10),
                         bd=0, highlightthickness=1, highlightbackground="#444444", highlightcolor="#6c7ad6")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        hint = "Напиши Рокси и нажми Enter…"

        def show_hint():
            if not entry.get():
                entry.insert(0, hint)
                entry.config(fg="#888888")

        def clear_hint(_e=None):
            if entry.get() == hint:
                entry.delete(0, tk.END)
                entry.config(fg="white")
            entry.focus_force()               # у окна без рамки фокус клавиатуры надо забирать явно

        def send(_e=None):
            msg = entry.get().strip()
            if msg and msg != hint:
                entry.delete(0, tk.END)
                try:
                    on_text(msg)
                except Exception as ex:
                    print(f"⚠️ ввод: {ex}")
            return "break"

        entry.bind("<Button-1>", clear_hint)
        entry.bind("<FocusIn>", clear_hint)
        entry.bind("<FocusOut>", lambda e: show_hint())
        entry.bind("<Return>", send)
        tk.Button(bar, text="➤", bg="#333333", fg="white", bd=0, command=send).pack(side=tk.RIGHT, padx=(4, 0))
        show_hint()
        global _ENTRY
        _ENTRY = (entry, clear_hint)

    text = tk.Text(root, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9), wrap="word", bd=0, highlightthickness=0)
    text.pack(expand=True, fill="both", padx=5, pady=5)

    def pump():
        chunk = []
        try:
            while len(chunk) < 500:
                chunk.append(_lines.get_nowait())
        except queue.Empty:
            pass
        if chunk:
            text.insert(tk.END, "".join(chunk))
            lines = int(text.index("end-1c").split(".")[0])
            if lines > 300:
                text.delete("1.0", f"{lines - 300}.0")
            text.see(tk.END)
        root.after(100, pump)

    pump()
    root.mainloop()
