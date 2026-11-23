"""Ввод текста в активное окно при любой раскладке и с кириллицей.

Раньше: ASCII — через pyautogui.write(), остальное — через буфер и pyautogui.hotkey("ctrl", "v").
В русской раскладке оба способа ломались: write() печатал «фис» вместо «abc», а Ctrl+V превращался
в мусорную комбинацию (см. kbfix.py). Теперь короткий текст печатается символами Юникода
(раскладка не важна, буфер не трогается), длинный — вставляется через буфер настоящим Ctrl+V.
"""
import time

import pyautogui
import pyperclip

import kbfix            # noqa: F401 — при импорте чинит коды клавиш pyautogui


def type_text(text, interval=0.02):
    text = str(text)
    if not text:
        return
    if len(text) <= 300 and kbfix.type_unicode(text, interval=interval if len(text) < 60 else 0.0):
        return
    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    try:
        pyperclip.copy(text)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.15)          # даём окну забрать вставку, прежде чем вернуть буфер
    finally:
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
