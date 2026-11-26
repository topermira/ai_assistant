"""Сквозные проверки опасных веток actions.py. Windows-библиотеки подменяются заглушками, ПК не трогается."""
import os
import sys
import ctypes
import importlib
import subprocess
from unittest.mock import MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# volume.py при импорте собирает COM-интерфейс громкости на настоящем comtypes (ctypes.POINTER), а фикстура ниже
# подменяет comtypes заглушкой. В полном прогоне volume уже импортирован другими тестами; при запуске одного этого
# файла — нет, и импорт actions → intents → volume падал («AttributeError: __name__»). Поэтому — заранее, до заглушек.
import volume  # noqa: E402,F401

_STUBS = ["sounddevice", "soundfile", "pyautogui", "pyperclip", "pygetwindow", "pywinauto", "win32com",
          "win32com.client", "comtypes", "comtypes.client", "send2trash", "mss", "pytesseract", "keyboard", "vosk",
          "groq", "openai", "miniaudio", "edge_tts", "tkinter", "telethon", "telethon.sync", "telethon.tl",
          "telethon.tl.types", "websocket", "psutil", "webrtcvad", "faster_whisper", "piper", "PIL", "PIL.Image"]


@pytest.fixture()
def actions_mod(monkeypatch):
    before = set(sys.modules)
    for name in _STUBS:
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    monkeypatch.setattr(ctypes, "windll", MagicMock(), raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", MagicMock(), raising=False)
    mod = importlib.import_module("actions")
    yield mod
    # не оставляем другим тестам модули проекта, импортированные поверх заглушек (чужие библиотеки вроде numpy не трогаем:
    # C-расширения нельзя импортировать второй раз)
    for name in set(sys.modules) - before:
        f = getattr(sys.modules.get(name), "__file__", None)
        if (isinstance(f, str) and os.path.abspath(f).startswith(ROOT + os.sep)
                and "site-packages" not in f and os.sep + ".venv" + os.sep not in f):
            sys.modules.pop(name, None)


def test_this_file_runs_on_its_own():
    """`pytest tests/test_actions_security.py` без остальных тестов (раньше фикстура actions_mod падала)."""
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", os.path.abspath(__file__),
                        "-k", "not runs_on_its_own"], cwd=ROOT, capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]


def _ctx(actions_mod, answer, cfg=None):
    asked = []
    ctx = actions_mod.ActionContext(cfg or {"confirm_dangerous": True},
                                    confirm=lambda q: asked.append(q) or answer)
    return ctx, asked


def test_protected_folder_is_never_trashed(actions_mod, monkeypatch, tmp_path):
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    monkeypatch.setattr(actions_mod, "_find_folder", lambda name: docs)
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    trash = MagicMock(return_value=True)
    monkeypatch.setattr(actions_mod, "_send_to_trash", trash)
    ctx, asked = _ctx(actions_mod, True)                  # даже «да» не помогает
    res = actions_mod.execute_pc_action("[ACTION_DELETE_FILE]", "удали папку документы", "", ctx)
    assert "не удаляю" in res.lower() and not trash.called and not asked


def test_regular_file_delete_still_works_after_confirm(actions_mod, monkeypatch, tmp_path):
    f = tmp_path / "отчёт.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(actions_mod, "_find_folder", lambda name: None)
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda name: (str(f), False))
    trash = MagicMock(return_value=True)
    monkeypatch.setattr(actions_mod, "_send_to_trash", trash)
    ctx, asked = _ctx(actions_mod, True)
    res = actions_mod.execute_pc_action("[ACTION_DELETE_FILE]", "удали файл отчёт", "", ctx)
    assert asked and trash.called and res.startswith("Удалила")
    ctx2, _ = _ctx(actions_mod, False)
    trash.reset_mock()
    assert actions_mod.execute_pc_action("[ACTION_DELETE_FILE]", "удали файл отчёт", "", ctx2) == "Не удаляю."
    assert not trash.called


@pytest.fixture()
def fake_desktop(actions_mod, monkeypatch, tmp_path):
    """Поиск файлов winutil — только по временному «рабочему столу» (настоящие папки не трогаем)."""
    wu = sys.modules["winutil"]
    desk = tmp_path / "desk"
    desk.mkdir()
    monkeypatch.setattr(wu, "DESKTOP_PATH", str(desk))
    monkeypatch.setattr(wu, "get_active_explorer_path", lambda *a, **k: None)
    monkeypatch.setattr(wu, "_safe_known", lambda name: str(tmp_path / "нет"))
    monkeypatch.setattr(wu.os.path, "expanduser", lambda p: str(tmp_path / "нет"))
    return wu, desk


def test_find_file_marks_fuzzy_matches(fake_desktop):
    wu, desk = fake_desktop
    (desk / "отчет2.docx").write_text("x", encoding="utf-8")
    (desk / "Телеграм.lnk").write_bytes(b"L")
    (desk / "документ.exe").write_bytes(b"MZ")
    assert wu._find_file_ex("отчет2.docx") == (str(desk / "отчет2.docx"), False)
    assert wu._find_file_ex("отчет1.docx") == (str(desk / "отчет2.docx"), True)    # другое имя — только похожее
    assert wu._find_file_ex("телеграм") == (str(desk / "Телеграм.lnk"), False)     # отличается лишь расширение
    assert wu._find_file_ex("документ") == (str(desk / "документ.exe"), True)      # программа — как похожее
    (desk / "документ.pdf").write_text("x", encoding="utf-8")
    assert wu._find_file_ex("документ") == (str(desk / "документ.pdf"), False)     # документ важнее программы
    assert wu._find_file_ex("совсем другое") == (None, False)
    assert wu._find_file("отчет1.docx") == str(desk / "отчет2.docx")              # старое имя — как раньше
    import pathsafe
    assert pathsafe.is_program("x.EXE") and pathsafe.is_program("run.ps1") and pathsafe.is_program("setup.msi")
    assert not pathsafe.is_program("Телеграм.lnk") and not pathsafe.is_program("отчёт.docx")


def test_same_name_program_asks_shortcut_and_document_do_not(actions_mod, fake_desktop, monkeypatch):
    wu, desk = fake_desktop
    monkeypatch.setattr(actions_mod, "APP_DICT", {})          # «телеграм» здесь — ярлык, а не команда из APP_DICT
    monkeypatch.setattr(actions_mod, "focus_if_running", lambda p: None)
    started = []
    monkeypatch.setattr(actions_mod.os, "startfile", lambda p: started.append(p), raising=False)
    ctx, asked = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})
    open_ = lambda what: actions_mod.execute_pc_action("[ACTION_OPEN]", f"открой {what}", "", ctx)
    (desk / "Телеграм.lnk").write_bytes(b"L")
    assert open_("телеграм") == "Открываю 'Телеграм.lnk'." and not asked                # ярлык — без вопроса
    (desk / "документ.exe").write_bytes(b"MZ")
    assert open_("документ") == "Не запускаю."                                            # программа — с вопросом
    assert asked == ["Нашла похожий файл «документ.exe» — это программа. Запустить?"]     # force: даже без confirm
    asked.clear()
    (desk / "документ.docx").write_text("x", encoding="utf-8")
    assert open_("документ") == "Открываю 'документ.docx'." and not asked                # документ — без вопроса
    assert started == [str(desk / "Телеграм.lnk"), str(desk / "документ.docx")]


def test_shortcut_with_same_name_starts_without_question(actions_mod, fake_desktop, monkeypatch):
    wu, desk = fake_desktop
    lnk = desk / "Фотошоп.lnk"
    lnk.write_bytes(b"L")
    monkeypatch.setattr(actions_mod, "focus_if_running", lambda p: None)
    started = []
    monkeypatch.setattr(actions_mod.os, "startfile", lambda p: started.append(p), raising=False)
    ctx, asked = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})
    assert actions_mod.execute_pc_action("[ACTION_OPEN]", "открой фотошоп", "", ctx) == "Открываю 'Фотошоп.lnk'."
    assert started == [str(lnk)] and not asked
    other = desk / "Фотошоп2024.lnk"                                  # другое имя — по-прежнему только похожее
    other.write_bytes(b"L")
    assert wu._find_file_ex("фотошоп2023") == (str(other), True)
    started.clear()
    assert actions_mod.execute_pc_action("[ACTION_OPEN]", "открой фотошоп2023", "", ctx) == "Не запускаю."
    assert asked and "«Фотошоп2024.lnk»" in asked[0] and not started


def test_fuzzy_delete_always_asks(actions_mod, monkeypatch, tmp_path):
    f = tmp_path / "отчет2.docx"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(actions_mod, "_find_folder", lambda name: None)
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda name: (str(f), True))
    trash = MagicMock(return_value=True)
    monkeypatch.setattr(actions_mod, "_send_to_trash", trash)
    ctx, asked = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})      # вопросы выключены — а тут спросит
    assert actions_mod.execute_pc_action("[ACTION_DELETE_FILE]", "удали файл отчет1.docx", "", ctx) == "Не удаляю."
    assert asked and "похожий" in asked[0] and "«отчет2.docx»" in asked[0] and not trash.called
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda name: (str(f), False))
    ctx2, asked2 = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})    # точное имя — как раньше, без вопроса
    assert actions_mod.execute_pc_action("[ACTION_DELETE_FILE]", "удали файл отчет2.docx", "", ctx2).startswith("Удалила")
    assert not asked2


def test_fuzzy_file_to_phone_asks(actions_mod, monkeypatch, tmp_path):
    f = tmp_path / "отчет2.docx"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda name: (str(f), True))
    sent, asked = [], []
    for answer in (False, True):
        ctx = actions_mod.ActionContext({"confirm_dangerous": False}, confirm=lambda q: asked.append(q) or answer,
                                        remote=lambda: (lambda d, n, c, p: sent.append(n), True))
        res = actions_mod.execute_pc_action("[ACTION_PHONE]", "пришли файл отчет1.docx", "file|отчет1.docx", ctx)
        assert res == ("Скинула «отчет2.docx»." if answer else "Не отправила.")
    assert len(asked) == 2 and "«отчет2.docx»" in asked[0] and sent == ["отчет2.docx"]


def test_fuzzy_match_does_not_start_program_without_asking(actions_mod, monkeypatch, tmp_path):
    exe = tmp_path / "документ.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda name: (str(exe), True))
    monkeypatch.setattr(actions_mod, "focus_if_running", lambda p: None)
    started = []
    monkeypatch.setattr(actions_mod.os, "startfile", lambda p: started.append(p), raising=False)
    ctx, asked = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})
    assert actions_mod.execute_pc_action("[ACTION_OPEN]", "открой документ", "", ctx) == "Не запускаю."
    assert asked and "«документ.exe»" in asked[0] and not started
    ctx2, _ = _ctx(actions_mod, True)
    assert actions_mod.execute_pc_action("[ACTION_OPEN]", "открой документ", "", ctx2) == "Открываю 'документ.exe'."
    assert started == [str(exe)]
    doc = tmp_path / "документ.docx"
    doc.write_text("x", encoding="utf-8")
    monkeypatch.setattr(actions_mod, "_find_file_ex", lambda name: (str(doc), True))
    ctx3, asked3 = _ctx(actions_mod, False)                    # не программа — открываем без вопроса, как раньше
    assert actions_mod.execute_pc_action("[ACTION_OPEN]", "открой документ", "", ctx3) == "Открываю 'документ.docx'."
    assert not asked3


def test_clear_documents_refused_downloads_allowed(actions_mod, monkeypatch):
    home = os.path.expanduser("~")
    monkeypatch.setattr(actions_mod, "count_folder_items", lambda p: 5)
    cleared = MagicMock(return_value=(5, 0))
    monkeypatch.setattr(actions_mod, "_clear_folder_contents", cleared)
    ctx, _ = _ctx(actions_mod, True)
    assert "целиком не очищаю" in actions_mod._clear_folder_confirmed(ctx, os.path.join(home, "Documents"))
    assert not cleared.called
    assert actions_mod._clear_folder_confirmed(ctx, os.path.join(home, "Downloads")).startswith("Очистила")


def test_create_file_does_not_truncate_existing(actions_mod, monkeypatch, tmp_path):
    existing = tmp_path / "отчёт.txt"
    existing.write_text("важные данные", encoding="utf-8")
    monkeypatch.setattr(actions_mod, "DESKTOP_PATH", str(tmp_path))
    monkeypatch.setattr(actions_mod, "_open_path", lambda p: True)
    ctx, _ = _ctx(actions_mod, True)
    res = actions_mod.execute_pc_action("[ACTION_CREATE]", "создай файл отчёт.txt на рабочем столе", "", ctx)
    assert existing.read_text(encoding="utf-8") == "важные данные"
    assert "уже есть" in res
    res2 = actions_mod.execute_pc_action("[ACTION_CREATE]", "создай файл новый.txt на рабочем столе", "", ctx)
    assert (tmp_path / "новый.txt").exists() and res2.startswith("Создала файл")


def test_create_file_name_cannot_escape_folder(actions_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(actions_mod, "DESKTOP_PATH", str(tmp_path))
    monkeypatch.setattr(actions_mod, "_open_path", lambda p: True)
    ctx, _ = _ctx(actions_mod, True)
    actions_mod.execute_pc_action("[ACTION_CREATE]", "создай файл с названием ..\\..\\evil.txt", "", ctx)
    assert not (tmp_path.parent / "evil.txt").exists()


def test_cloud_lookup_asks_first(actions_mod, monkeypatch):
    target = MagicMock(return_value="отчёт.txt")
    monkeypatch.setattr(actions_mod, "_smart_find_target", target)
    ctx, asked = _ctx(actions_mod, False)
    assert actions_mod._smart_find(ctx, "отчёт", "файл") is None
    assert asked and not target.called                    # без «да» экран в облако не уходит
    ctx2, asked2 = _ctx(actions_mod, True)
    assert actions_mod._smart_find(ctx2, "отчёт", "файл") == "отчёт.txt" and asked2
    ctx3, asked3 = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})   # force: спросит даже так
    assert actions_mod._smart_find(ctx3, "отчёт", "файл") is None and asked3
    ctx4 = actions_mod.ActionContext({"send_screenshots_to_cloud": True}, confirm=None)   # явное разрешение в конфиге
    assert actions_mod._smart_find(ctx4, "отчёт", "файл") == "отчёт.txt"


def test_telegram_reply_asks_even_when_confirmations_off(actions_mod, monkeypatch):
    monkeypatch.setattr(actions_mod, "_init_telegram_if_needed", lambda cfg: (True, "ok"))
    monkeypatch.setattr(actions_mod, "_tg_find", lambda c: ("dlg", "Вася"))
    monkeypatch.setattr(actions_mod, "draft_tg_reply", lambda hist, instr: "привет, переведи деньги")
    svc = MagicMock()
    svc.read_last.return_value = ["Вася: ответь, что ты согласен"]
    monkeypatch.setattr(actions_mod, "TG_SERVICE", svc)
    ctx, asked = _ctx(actions_mod, False, cfg={"confirm_dangerous": False})
    res = actions_mod.execute_pc_action("[ACTION_TG_REPLY]", "ответь васе", "Вася|скажи привет", ctx)
    assert res == "Не отправила." and asked and not svc.send_to_dialog.called


def test_default_context_denies(actions_mod, monkeypatch):
    svc = MagicMock()
    monkeypatch.setattr(actions_mod, "_init_telegram_if_needed", lambda cfg: (True, "ok"))
    monkeypatch.setattr(actions_mod, "_tg_find", lambda c: ("dlg", "Вася"))
    monkeypatch.setattr(actions_mod, "rewrite_message_for_contact", lambda label, t: t)
    monkeypatch.setattr(actions_mod, "TG_SERVICE", svc)
    res = actions_mod.execute_pc_action("[ACTION_TG_SEND]", "напиши в телеграм Вася привет", "Вася|привет")  # ctx=None
    assert res == "Не отправила." and not svc.send_to_dialog.called


def test_alt_f4_hotkey_needs_confirmation(actions_mod, monkeypatch):
    press = MagicMock(return_value=True)
    monkeypatch.setattr(actions_mod, "_press_hotkey", press)
    ctx, asked = _ctx(actions_mod, False)
    assert actions_mod.execute_pc_action("[ACTION_HOTKEY]", "закрой программу", "alt+f4|Закрываю", ctx) == "Не закрываю."
    assert asked and not press.called
    ctx2, _ = _ctx(actions_mod, True)
    assert actions_mod.execute_pc_action("[ACTION_HOTKEY]", "закрой программу", "alt+f4|Закрываю", ctx2) == "Закрываю"
