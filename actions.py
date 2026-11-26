"""Выполнение действий на ПК. Всё опасное (удаление, очистка, закрытие всех окон,
отправка в Telegram, Vision-агент) — только после голосового «да»."""
import os
import re
import time
import subprocess

import pyautogui
import kbfix  # noqa: F401 — Ctrl+V/C/N… работают и в русской раскладке
import pyperclip
import pygetwindow as gw

import audio
import gui
from settings import APP_DICT, DESKTOP_PATH, PROJECT_DIR
from intents import (_RE_DELETE_FILE, _RE_CLEAR_FOLDER, _RE_MAXIMIZE_FULL, _RE_WINDOW, _RE_HERE,
                     _RE_EXPLICIT_FOLDER, _FOLDER_KW, _WINDOW_NOISE, _WINDOW_NOISE_PREFIX, _clean_folder_query,
                     create_name)
from winutil import (_force_foreground, _bring_to_front, _focus_new_app, _launch_by_name, _press_win_d,
                     _press_hotkey, _find_folder, _find_file, _find_file_ex, _open_path, _send_to_trash,
                     _clear_folder_contents,
                     count_folder_items, get_active_explorer_path, is_explorer_window, _clear_notepad,
                     insert_into_notepad, type_at_cursor, _find_window_by_title, _find_notepad_window,
                     set_text_if_empty, open_file_in_notepad, _safe_maximize, _close_all_windows, media_key,
                     _window_process_name, window_minimized, focus_existing_app, focus_if_running)
import winalias
from vision import _smart_find_target, execute_vision_task, read_screen_and_analyze
from llm import think_llm, generate_code, clean_code, rewrite_message_for_contact, draft_tg_reply
from library import find_work_in_library, play_library_work, make_work_id
from telegram_service import TG_SERVICE, _init_telegram_if_needed
from timers import parse_duration, parse_reminder, format_duration
from weather import _extract_weather_city, get_weather, parse_weather_day
from clock import say_now
from textinput import type_text
import volume
from actionctx import ActionContext
from pathsafe import safe_filename as _safe_filename, is_protected_path, is_clear_forbidden, is_executable


GENERATED_CODE_DIR = os.path.join(PROJECT_DIR, "generated_code")


def _save_generated_code(code, task):
    try:
        os.makedirs(GENERATED_CODE_DIR, exist_ok=True)
        slug = make_work_id(re.sub(r"^(?:написать|напиши)\s+(?:программу|код|скрипт)?\s*", "", task or "", flags=re.I))[:30]
        path = os.path.join(GENERATED_CODE_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{slug or 'code'}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code.rstrip() + "\n")
        print(f"💾 Код сохранён: {path}")
        return path
    except Exception as e:
        print(f"⚠️ Не смогла сохранить код: {e}")
        return None


def _smart_find(ctx, query, kind):
    """Поиск «на глаз»: картинка активного окна И распознанный с него текст уходят в облачную нейросеть.
    Там может быть банк, почта, переписка — поэтому спрашиваем (всегда, если не включено send_screenshots_to_cloud)."""
    if not ctx.config.get("send_screenshots_to_cloud", False):
        if not ctx.confirm(f"Не нашла «{query}». Показать активное окно облачной нейросети, чтобы найти? "
                           f"Картинка и текст окна уйдут в интернет.", force=True):
            return None
    return _smart_find_target(query, kind)


def _base(p):
    return os.path.basename(str(p).rstrip("/\\")) or str(p)


def _fuzzy_run_ok(ctx, path, fuzzy):
    """Программу или скрипт (.exe, .bat, .lnk…), найденные по ПОХОЖЕМУ имени, без вопроса не запускаем:
    «открой документ» не должно запускать «документ.exe» из Загрузок."""
    if not (fuzzy and is_executable(path)):
        return True
    return ctx.confirm(f"Нашла похожий файл «{_base(path)}» — это программа. Запустить?", force=True)




# ==================== TELEGRAM ====================
def _tg_find(contact):
    dialog, label = TG_SERVICE.find_dialog(contact)
    if dialog is None:
        if label:
            return None, f"Нашла несколько похожих: {', '.join(label)}. Скажи точнее."
        return None, None
    return dialog, label


def _tg_send(ctx, contact, text, fallback_notepad=False):
    ok, msg = _init_telegram_if_needed(ctx.config)
    if not ok:
        if fallback_notepad:
            print(f"⚠️ Telegram: {msg} — печатаю в блокнот")
            ok2, _ = insert_into_notepad(text)
            return "Telegram недоступен. Напечатала в блокнот." if ok2 else "Не смогла отправить и напечатать."
        return f"Telegram: {msg}"
    dialog, label = _tg_find(contact)
    if dialog is None:
        if label:
            return label
        if fallback_notepad:
            ok2, _ = insert_into_notepad(text)
            return f"Контакт '{contact}' не найден в Telegram." + (" Напечатала в блокнот." if ok2 else "")
        return f"Не нашла '{contact}' в Telegram."
    rewritten = rewrite_message_for_contact(label, text)
    if not ctx.confirm(f"Отправить {label}: {rewritten}?", force=True):
        return "Не отправила."
    return TG_SERVICE.send_to_dialog(dialog, label, rewritten)


def execute_routed(intent, phrase, extracted_task="", ctx=None):
    """Команда, которую понял LLM-роутер. Действию отдаём КАНОНИЧЕСКУЮ фразу роутера: исходную регулярки уже
    не поняли (поэтому и сработал роутер) — раньше open/type/window отвечали «Не поняла», create делал «Новый файл».
    Белый список интентов проверен в llm.llm_route; подтверждения — внутри execute_pc_action, как у обычных команд."""
    ctx = ctx or ActionContext({})
    if intent == "[ACTION_SCREEN]" and not ctx.config.get("send_screenshots_to_cloud", False):
        # «глянь, что там» распознала нейросеть, а не шаблон — без «да» снимок окна в облако не уходит
        if not ctx.confirm("Показать активное окно облачной нейросети? Картинка уйдёт в интернет.", force=True):
            return "Хорошо, не смотрю."
    return execute_pc_action(intent, phrase, extracted_task, ctx)


def execute_pc_action(intent, user_text, extracted_task="", ctx=None):
    ctx = ctx or ActionContext({})
    lower = user_text.lower()

    if intent == "[ACTION_LIBRARY]":
        return play_library_work(find_work_in_library(user_text))

    if intent == "[ACTION_WEATHER]":
        city = _extract_weather_city(user_text)
        day = parse_weather_day(user_text)
        print(f"🌤️ Погода: {city or 'геолокация'}" + (f", день +{day}" if day else ""))
        res = get_weather(city, day)
        return res or (f"Не нашла '{city}'." if city else "Ошибка прогноза.")

    if intent == "[ACTION_TIME]":
        return say_now(extracted_task if extracted_task in ("time", "date", "both") else "time")

    if intent == "[ACTION_READ]":
        old = ""
        try: old = pyperclip.paste() or ""
        except Exception: pass
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.5)
        try:
            sel = pyperclip.paste() or ""
        except Exception:
            return "Не смогла прочитать."
        if not sel.strip() or sel == old:
            return "Ты ничего не выделил."
        return think_llm(f"Проанализируй текст: {sel}", "[CHAT]")

    if intent == "[ACTION_SCREEN]":
        # вопрос пользователя целиком: «что думаешь?», «переведи», «как это исправить?» — а не только «что на экране»
        return read_screen_and_analyze(extracted_task or ("Объясни ошибку." if "ошибк" in lower else "Что на экране?"))

    if intent == "[ACTION_CLOSE_TAB]":
        try:
            pyautogui.hotkey("ctrl", "w")
            return "Закрыла вкладку."
        except Exception:
            return "Не удалось закрыть вкладку."

    if intent == "[ACTION_MINIMIZE_ALL]":
        if _press_win_d(): return "Свернула всё."
        try:
            pyautogui.hotkey("win", "d")
            return "Свернула всё."
        except Exception:
            return "Не удалось."

    if intent == "[ACTION_CLOSE_ALL]":
        skip = ("roxy console", "roxy live2d", "program manager", "программный менеджер")
        n = len([w for w in gw.getAllWindows() if (w.title or "").strip() and not any(s in w.title.lower() for s in skip)])
        if not n:
            return "Нечего закрывать."
        if not ctx.confirm(f"Закрыть все окна? Их примерно {n}."):
            return "Не закрываю."
        closed, failed = _close_all_windows()
        if failed: return f"Закрыла {len(closed)}, ошибок {len(failed)}."
        return f"Закрыла {len(closed)} окон."

    if intent == "[ACTION_HOTKEY]":
        if "|" not in extracted_task: return "Не поняла клавиши."
        keys, desc = extracted_task.split("|", 1)
        if keys == "win+shift+s" and ctx.from_chat():          # с телефона «скрин» — это снимок в чат, а не «Ножницы»
            return _phone_action(ctx, "screen")
        if keys == "win+l":
            import syscmd
            return syscmd.lock()
        print(f"⌨️ {keys}")
        if keys == "alt+f4" and not ctx.confirm("Закрыть активную программу? Несохранённое пропадёт."):
            return "Не закрываю."
        return desc if _press_hotkey(keys) else f"Не удалось {keys}."

    if intent == "[ACTION_PET]":
        from live2d_client import LIVE2D
        if extracted_task in ("autonomy_on", "autonomy_off"):
            ctx.config["pet_autonomy"] = extracted_task == "autonomy_on"
            try:
                from settings import update_config
                update_config("pet_autonomy", ctx.config["pet_autonomy"])
            except Exception:
                pass
        if extracted_task == "perch":
            # окно отвечает, села ли она: раньше «Сажусь на окно» говорилось всегда, а потом аватар жаловался «не на что сесть»
            if not LIVE2D.connected:
                return "Окно Live2D не подключено."
            res = LIVE2D.behavior_result("perch")
            if res is not None and not res.get("ok"):
                return "Не получилось сесть на окно: " + (res.get("reason") or "подходящего окна нет") + "."
        elif not LIVE2D.send("behavior", name=extracted_task):
            return "Окно Live2D не подключено."
        # Без тега эмоции: раньше «[happy]» добавлял радостный подскок к каждой команде («иди сюда» → прыжки)
        return {"autonomy_off": "Стою на месте.", "autonomy_on": "Гуляю сама.", "jump": "Прыгаю.", "flip": "Смотри, сальто!", "handstand": "Встаю на руки!", "cartwheel": "Колесо!",
                "perch": "Сажусь на окно.", "floor": "Слезаю.", "come": "Иду к курсору.", "walk": "Гуляю.",
                "dance": "Танцую.", "dance_music": "Под музыку? С удовольствием!", "dance_stop": "Ладно, отдыхаю.", "spin": "Кручусь.", "magic": "Смотри — магия!", "magic_orb": "Водный шар!", "magic_ice": "Ледяные кристаллы!", "magic_rain": "Сейчас будет дождик!",
                "magic_fountain": "Фонтан!", "staff_lean": "Передохну.", "staff_twirl": "Смотри, как умею!", "peek": "Ку-ку!", "hide": "Ищи меня!", "push": "Сейчас подвину.", "bow": "Кланяюсь.", "stretch": "Потягиваюсь.",
                "yawn": "Ааах... зеваю.", "look_around": "Осматриваюсь.", "lean": "Пойду обопрусь о край.",
                "nod": "Киваю.", "shake": "Мотаю головой."}.get(extracted_task, "Ок.")

    if intent == "[ACTION_VOLUME]":
        kind, _, val = (extracted_task or "").partition("|")
        if kind == "playing":
            track = volume.now_playing()
            return f"Сейчас играет: {track}." if track else "Не вижу, что играет. Я узнаю трек по окну Spotify или вкладке YouTube, Яндекс Музыки, VK."
        if kind == "query":
            cur = volume.get_volume()
            if cur is None:
                return "Не могу узнать громкость."
            return f"Громкость {cur[0]} процентов" + (", но звук выключен." if cur[1] else ".")
        if kind == "mute":
            on = val == "1"
            if not volume.set_mute(on):
                return "Не получилось."
            return "Выключила звук." if on else "Включила звук."
        if kind in ("set", "change"):
            n = int(val or 0)
            lvl = volume.set_volume(n) if kind == "set" else volume.change_volume(n)
            if lvl is None:
                return "Не получилось."
            return "Готово." if lvl == "" else f"Громкость {lvl}."
        return "Не поняла, что сделать со звуком."

    if intent == "[ACTION_MEDIA]":
        times = 5 if extracted_task in ("volume_up", "volume_down") else 1
        return "Готово." if media_key(extracted_task, times) else "Не получилось."

    if intent == "[ACTION_VISION]":
        task = extracted_task or user_text
        if not ctx.confirm(f"Запустить агента? Он сам будет кликать и печатать: {task}. "
                           f"Скриншоты окна уходят в облако.", force=True):
            return "Не запускаю."
        audio.reset_stop()
        print("💡 Остановить агента: Num Lock или увести мышь в левый верхний угол.")
        return execute_vision_task(task, max_steps=10, should_stop=audio.is_stopped)

    # ==================== УДАЛЕНИЕ / ОЧИСТКА ====================
    if intent == "[ACTION_CLEAR_CONTEXT]":
        win = gw.getActiveWindow()
        if not win or not win.title: return "Не вижу активного окна."
        t = win.title.lower()
        if "блокнот" in t or "notepad" in t or _window_process_name(getattr(win, "_hWnd", 0)) == "notepad.exe":
            if not ctx.confirm("Стереть весь текст в Блокноте?"):
                return "Не трогаю."
            ok, _ = _clear_notepad()
            return "Удалила текст из Блокнота." if ok else "Не удалось."
        hwnd = getattr(win, "_hWnd", None)
        if hwnd and is_explorer_window(hwnd):      # раньше хватало " — " в заголовке
            folder = get_active_explorer_path(require_active=True)
            if not folder: return "Не вижу открытой папки."
            return _clear_folder_confirmed(ctx, folder)
        return f"Не знаю, как очистить '{win.title}'."

    if intent == "[ACTION_CLEAR_TEXT]":
        if not ctx.confirm("Стереть весь текст в Блокноте?"):
            return "Не трогаю."
        ok, _ = _clear_notepad()
        return "Удалила текст из Блокнота." if ok else "Не вижу Блокнота."

    if intent == "[ACTION_DELETE_FILE]":
        m = _RE_DELETE_FILE.search(user_text)
        if not m: return "Не поняла, что удалить."
        raw = m.group(1).strip(" .,!?«»\"'")
        raw = re.sub(r"\s+(?:из|в)\s+(?:текущей|этой|открытой|данной)\s+(?:папки|директории|каталога)\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s+(?:с|из)\s+(?:рабочего\s+стола|desktop)\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s+на\s+(?:рабочем\s+столе|desktop)\s*$", "", raw, flags=re.IGNORECASE)
        raw = raw.strip(" .,!?«»\"'")
        if not raw: return "Не поняла, что удалить."
        fuzzy = False
        target = _find_folder(raw)
        if not target:
            target, fuzzy = _find_file_ex(raw)
        if not target: return f"Не нашла '{raw}'."
        is_dir = os.path.isdir(target)
        if is_dir and is_protected_path(target, PROJECT_DIR, (DESKTOP_PATH,)):
            return (f"Папку «{_base(target)}» я не удаляю: она системная, личная или в ней лежит проект. "
                    f"Если правда надо, удали вручную.")
        kind = "папку" if is_dir else "файл"
        where = _base(os.path.dirname(target))
        extra = f" В ней {count_folder_items(target)} объектов." if is_dir else ""
        question = f"Удалить {kind} «{_base(target)}» из «{where}» в корзину?{extra}"
        if fuzzy:                            # нашлось только похожее имя — спрашиваем всегда, даже без confirm_dangerous
            question = f"Точного «{raw}» нет, нашла похожий. " + question
        if not ctx.confirm(question, force=fuzzy):
            return "Не удаляю."
        if _send_to_trash(target):
            return f"Удалила {kind} '{_base(target)}'."
        return f"Не удалось удалить '{raw}'."

    if intent == "[ACTION_CLEAR_FOLDER]":
        folder = None
        m = _RE_CLEAR_FOLDER.search(user_text)
        if m:
            name = m.group(1).strip(" .,!?«»\"'")
            if name:
                folder = _find_folder(name)
                if not folder: return f"Не нашла папку '{name}'."
        if not folder:
            folder = get_active_explorer_path(require_active=True)
            if not folder: return "Не вижу открытой папки. Открой её и сделай активной."
        return _clear_folder_confirmed(ctx, folder)

    # ==================== ОКНА ====================
    if intent == "[ACTION_MAXIMIZE_FULL]":
        m = _RE_MAXIMIZE_FULL.search(user_text)
        target = ""
        if m: target = (m.group(1) or "").strip(" .,!?")
        if target and (target.lower() in _WINDOW_NOISE or _WINDOW_NOISE_PREFIX.match(target)): target = ""
        win = None
        if target:
            win = _find_window_by_title(target)
            if not win: return f"Не нашла окно '{target}'."
        else: win = gw.getActiveWindow()
        if not win: return "Нет активного окна."
        return f"Развернула '{win.title}'." if _safe_maximize(win) else "Не удалось."

    if intent == "[ACTION_WINDOW]":
        m = _RE_WINDOW.search(user_text)
        if not m: return "Не поняла."
        action = (m.group(1) or "").lower().strip()
        target = (m.group(2) or "").strip(" .,!?")
        target = re.sub(r"^(?:папку|папка|окно)\s+", "", target, flags=re.IGNORECASE).strip()
        if _WINDOW_NOISE_PREFIX.match(target): target = ""
        if action == "свернуть": action = "сверни"
        elif action == "развернуть": action = "разверни"
        elif action == "закрыть": action = "закрой"
        elif action == "активировать": action = "активируй"
        elif action.startswith("переключись"): action = "активируй"
        elif action in ("верни", "вернуть", "восстанови", "восстановить", "подними", "поднять"): action = "восстанови"
        if target.lower() in _WINDOW_NOISE: target = ""
        print(f"🪟 {action}, '{target or 'активное'}'")
        try:
            if target:
                win = _find_window_by_title(target)
                if not win: return f"Не нашла '{target}'."
            else:
                win = gw.getActiveWindow()
                if not win: return "Нет активного окна."
            t = win.title
            if action == "сверни":
                if window_minimized(win): return f"'{t}' уже свёрнуто."
                win.minimize()
                time.sleep(0.15)
                if window_minimized(win): return f"Свернула '{t}'."
                return f"Не получилось свернуть '{t}': это окно не сворачивается."
            elif action == "разверни":
                if win.isMinimized:
                    win.restore()
                    return f"Развернула '{t}'."
                return f"Развернула '{t}'." if _safe_maximize(win) else f"Не удалось '{t}'."
            elif action == "восстанови":
                if win.isMinimized: win.restore()
                hwnd = getattr(win, "_hWnd", None)
                if hwnd: _force_foreground(hwnd)
                else: win.activate()
                return f"Восстановила '{t}'."
            elif action == "закрой":
                win.close()
                return f"Закрыла '{t}'."
            elif action == "активируй":
                if win.isMinimized: win.restore()
                win.activate()
                return f"Активировала '{t}'."
        except Exception as e: return f"Ошибка: {e}"

    if intent == "[ACTION_MAXIMIZE_CURSOR]":
        x, y = pyautogui.position()
        for win in gw.getWindowsAt(x, y):
            if win.title and win.title not in ("Roxy Console", "Roxy Live2D"):
                if _safe_maximize(win): return f"Развернула '{win.title}'."
        return "Не нашла окно под курсором."

    if intent == "[ACTION_SHOW_GUI]":
        gui.set_visible(True, force_foreground=_force_foreground)
        return "Показала GUI."
    if intent == "[ACTION_HIDE_GUI]":
        gui.set_visible(False)
        return "Скрыла GUI."

    if intent == "[ACTION_RENAME]":
        m = re.search(r"переименуй\s+(?:в|на)\s+(.+)", user_text, re.IGNORECASE)
        if not m: return "Не поняла имя."
        new_name = re.sub(r'[^\w\s\.-]', '', m.group(1).strip()).strip()
        # расширение голосом не меняем: F2 выделяет только имя, и «отчёт.txt» дал бы «отчёт.txt.txt»
        base_name, ext = os.path.splitext(new_name)
        had_ext = bool(ext) and 1 < len(ext) <= 6 and ext[1:].isalnum()
        if had_ext:
            new_name = base_name.strip()
        new_name = _safe_filename(new_name)
        if not new_name: return "Не поняла имя."
        try:
            x, y = pyautogui.position()
            target = None
            for w in gw.getWindowsAt(x, y):
                if "проводник" in w.title.lower() or "explorer" in w.title.lower():
                    target = w
                    break
            if not target:
                a = gw.getActiveWindow()
                if a and ("проводник" in a.title.lower() or "explorer" in a.title.lower()): target = a
            if not target: return "Не вижу Проводника."
            if target.isMinimized: target.restore()
            target.activate()
            time.sleep(0.5)
            # F2 сам выделяет имя без расширения. Ctrl+A НЕ нажимаем: если F2 не вошёл в режим правки
            # (ничего не выбрано), Ctrl+A выделил бы ВСЕ файлы, а Enter открыл бы их все.
            pyautogui.press('f2')
            time.sleep(0.5)
            type_text(new_name)          # кириллица через буфер, pyautogui.write её не вводит
            time.sleep(0.2)
            pyautogui.press('enter')
            note = " Расширение оставила прежним." if had_ext else ""
            return f"Переименовала в '{new_name}'.{note}"
        except Exception as e: return f"Ошибка: {e}"

    # ==================== TELEGRAM ====================
    if intent == "[ACTION_TG_HELP]":
        return ("Не поняла Telegram-команду. Форматы: «прочитай телеграм <контакт>», "
                "«напиши в телеграм <контакт> <текст>», «ответь в телеграм <контакт> <что ответить>».")

    if intent in ("[ACTION_TG_SEND_MAYBE]", "[ACTION_TG_SEND]"):
        if "|" not in extracted_task: return "Формат: 'напиши в телеграм <контакт> <текст>'."
        contact, text = extracted_task.split("|", 1)
        print(f"📨 TG: {contact} → {text[:60]}")
        return _tg_send(ctx, contact, text, fallback_notepad=(intent == "[ACTION_TG_SEND_MAYBE]"))

    if intent == "[ACTION_TG_REPLY]":
        if "|" not in extracted_task: return "Формат: 'ответь <контакт> <текст>'."
        contact, instruction = extracted_task.split("|", 1)
        ok, msg = _init_telegram_if_needed(ctx.config)
        if not ok: return f"Telegram: {msg}"
        dialog, label = _tg_find(contact)
        if dialog is None: return label or f"Не нашла '{contact}'."
        try:
            history = "\n".join(TG_SERVICE.read_last(dialog, limit=20))
        except Exception as e:
            return f"Ошибка чтения: {e}"
        reply = draft_tg_reply(history, instruction)
        if not reply: return "Не смогла составить ответ."
        # force: ответ сочинила LLM по тексту ЧУЖОЙ переписки — без «да» от тебя он не уходит, даже при confirm_dangerous=false
        if not ctx.confirm(f"Ответить {label}: {reply}?", force=True):
            return "Не отправила."
        return TG_SERVICE.send_to_dialog(dialog, label, reply)

    if intent == "[ACTION_TG_READ]":
        ok, msg = _init_telegram_if_needed(ctx.config)
        if not ok: return f"Telegram: {msg}"
        contact = extracted_task.strip()
        if not contact: return "Укажи контакт: 'прочитай телеграм <контакт>'."
        dialog, label = _tg_find(contact)
        if dialog is None: return label or f"Не нашла диалог '{contact}'."
        try:
            msgs = TG_SERVICE.read_last(dialog, limit=10)
        except Exception as e:
            return f"Ошибка чтения: {e}"
        return f"Последние {len(msgs)} сообщений с {label}:\n" + "\n".join(msgs)

    # ==================== ТЕКСТ И КОД ====================
    if intent == "[ACTION_CODE_FILE]":
        task = extracted_task or lower
        if not extracted_task:
            task = re.sub(r"\bв\s+(?:текущ\w*|этом\s+|это\s+|открыт\w*)\s+(?:блокнот\w*|проводник\w*|окн\w*|окошк\w*)\b", " ", task)
            task = re.sub(r"\b(?:тут|сюда|здесь|напиши|создай|сделай|сгенерируй|вставь|ставь|введи|впиши|веди|пиши|выведи|тот|блокнот|мне)\b", " ", task)
            task = re.sub(r"\s+", " ", task).strip(" .,!?-\n\t")
        print(f"💻 Код: {task}")
        code = generate_code(task)
        if not code: return "Не удалось сгенерировать код."
        clean = clean_code(code)
        # 1) открытый пустой Блокнот → пишем прямо в него
        if set_text_if_empty(_find_notepad_window(), clean):
            return "Код вставила в Блокнот."
        # 2) иначе — сохраняем .py и открываем его Блокнотом (новая вкладка уже С КОДОМ)
        path = _save_generated_code(clean, task)
        if path and open_file_in_notepad(path):
            return "Код готов, открыла его в Блокноте. Файл лежит в папке generated_code."
        try:
            pyperclip.copy(clean)
            return "Код в буфере — не смогла открыть Блокнот."
        except Exception:
            return "Не удалось."

    if intent == "[ACTION_NEW_TAB]":
        if "блокнот" in lower:
            wins = gw.getWindowsWithTitle('Блокнот') + gw.getWindowsWithTitle('Notepad')
            if not wins:
                subprocess.Popen("notepad.exe")
                time.sleep(1)
                _focus_new_app("notepad.exe", timeout=2.0)
                wins = gw.getWindowsWithTitle('Блокнот') + gw.getWindowsWithTitle('Notepad')
            if wins:
                try:
                    wins[0].activate()
                    time.sleep(0.5)
                    pyautogui.hotkey('ctrl', 'n')
                    return "Новая вкладка в Блокноте."
                except Exception: pass
            return "Не нашла Блокнот."
        pyautogui.hotkey('ctrl', 'n')
        return "Новая вкладка."

    if intent == "[ACTION_CAPTURE]":
        return _media_action(ctx, extracted_task)

    if intent == "[ACTION_PLAN]":
        return _plan_action(ctx, user_text)

    if intent == "[ACTION_BRIGHTNESS]":
        import pcctl
        kind, _, n = (extracted_task or "query|0").partition("|")
        return pcctl.brightness((kind, int(n or 0)))

    if intent == "[ACTION_MOUSE]":
        import pcctl
        p = (extracted_task or "").split("|")
        if p[0] == "move":
            task = ("move", int(p[1]), int(p[2]))
        elif p[0] == "click":
            task = ("click", p[1], int(p[2]))
        elif p[0] == "scroll":
            task = ("scroll", int(p[1]))
        elif p[0] == "cell":
            task = ("cell", int(p[1]), int(p[2]), p[3], int(p[4])) + ((p[5],) if len(p) > 5 and p[5] else ())
        elif p[0] == "zoom":
            if not ctx.can_send_files():
                return "Приближение показываю только в Telegram: оно работает через управление с телефона."
            import screengrid
            data, cap = screengrid.zoom_screenshot(int(p[1]), int(p[2]), p[3] if len(p) > 3 else "")
            ctx.send_file(data, "zoom.jpg", cap, photo=True)
            return f"Приблизила {screengrid.region_name(int(p[1]), int(p[2]), p[3] if len(p) > 3 else '')}."
        elif p[0] == "step":
            task = ("step", int(p[1]))
        elif p[0] == "stepq":
            task = ("stepq",)
        elif p[0] == "armode":
            task = ("armode", p[1])
        elif p[0] in ("down", "up"):
            task = (p[0], p[1] if len(p) > 1 else ("left" if p[0] == "down" else "all"))
        else:
            task = (p[0], p[1] if len(p) > 1 else "center")
        result = pcctl.mouse(task)
        if p[0] == "step":                         # шаг запоминаем между запусками
            try:
                from settings import update_config
                ctx.config["mouse_step"] = pcctl.get_step()
                update_config("mouse_step", ctx.config["mouse_step"])
            except Exception as e:
                print(f"⚠️ Не смогла сохранить шаг мыши: {e}")
        if p[0] not in ("ask", "step", "stepq", "armode"):    # на вопросы и настройки снимок экрана не нужен
            _screen_after(ctx, delay=0.6 if p[0] in ("click", "cell") else 0.25)
        return result

    if intent == "[ACTION_KEYS]":
        import keys as keyboard_keys
        combo, _, times = (extracted_task or "").partition("|")
        if combo == "alt+f4" and not ctx.confirm("Закрыть активную программу? Несохранённое пропадёт."):
            return "Не закрываю."
        if combo == "win+l":
            import syscmd
            return syscmd.lock()
        ok, msg = keyboard_keys.press(combo, int(times or 1))
        if ok:
            _screen_after(ctx, delay=0.5)
        return msg

    if intent == "[ACTION_SYS]":
        return _sys_action(ctx, *(extracted_task or "").split("|", 1))

    if intent == "[ACTION_PHONE]":
        return _phone_action(ctx, *(extracted_task or "").split("|", 1))

    if intent == "[ACTION_POWER]":
        import power
        kind, _, sec = (extracted_task or "").partition("|")
        return power.run(kind, int(sec or 0), lambda q: ctx.confirm(q, force=True))

    if intent == "[ACTION_CREATE]":
        is_folder = "папку" in lower
        use_current = bool(_RE_HERE.search(user_text))
        name = create_name(user_text)
        name = name.strip(" .,!?-\"«»")
        name = re.sub(r"\s+\.", ".", name)
        if not name: name = "Новая папка" if is_folder else "Новый файл"
        if not is_folder and not name.endswith(".txt") and "." not in name: name += ".txt"

        explicit = _RE_EXPLICIT_FOLDER.search(user_text)
        base_dir = DESKTOP_PATH
        if explicit:
            fn = explicit.group(1).strip()
            if fn:
                found = _find_folder(fn)
                if not found: return f"Не нашла '{fn}'."
                base_dir = found
                use_current = False
        elif use_current:
            cur = get_active_explorer_path()
            if cur: base_dir = cur
            else: use_current = False

        name = _safe_filename(name) or ("Новая папка" if is_folder else "Новый файл.txt")
        target = os.path.join(base_dir, name)
        try:
            if is_folder:
                existed = os.path.isdir(target)
                os.makedirs(target, exist_ok=True)
                _open_path(target)
                return f"Папка {name} уже есть, открыла её." if existed else f"Создала папку {name}."
            else:
                try:
                    with open(target, "x", encoding="utf-8"):     # "x": существующий файл НЕ затираем
                        pass
                    created = True
                except FileExistsError:
                    created = False
                _open_path(target)
                return f"Создала файл {name}." if created else f"Файл {name} уже есть, не стала его затирать, просто открыла."
        except Exception as e: return f"Ошибка: {e}"

    if intent == "[ACTION_OPEN]":
        m = re.search(
            r"(?:откр[оыа]й|отр[оыа]й|открыть|запуст[иь]|включ[иь]|вруби|старт)\s+(?:(?:программу|файл|папку|каталог|директорию)\s+)?(.+)",
            user_text, re.IGNORECASE
        )
        if not m: return "Не поняла, что открыть."
        raw = m.group(1).strip()
        raw = re.sub(r"\bв\s+(?:текущей|этой|открытой|данной)\s+(?:папке|директории|каталоге)\b", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\b(?:с|под|со)\s+(?:названием|именем)\b", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r'[.!?]+$', '', raw).strip()
        raw = re.sub(r"\s+(?:с|из)\s+(?:рабочего\s+стола|desktop)\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s+на\s+(?:рабочем\s+столе|desktop)\s*$", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"^(?:папку|папка|каталог|файл|директорию)\s+", "", raw, flags=re.IGNORECASE)
        raw = raw.strip(" .,!?«»\"'")
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw: return "Не поняла, что открыть."

        is_file = bool(re.search(r"\.\w{2,5}\b", raw)) or ("файл" in lower)
        if is_file:
            tokens = raw.split()
            prog = None
            if tokens and tokens[0].lower() in APP_DICT:
                prog = tokens[0].lower()
                tokens = tokens[1:]
            fname = " ".join(tokens).strip()
            if not fname: return "Не поняла файл."
            fp, fuzzy = _find_file_ex(fname)
            if not fp:
                print(f"👁️ Vision: {fname}...")
                cand = _smart_find(ctx, fname, "файл")
                if cand: fp, fuzzy = _find_file_ex(cand)
            if fp:
                if not _fuzzy_run_ok(ctx, fp, fuzzy): return "Не запускаю."
                try:
                    os.startfile(fp)
                    _bring_to_front(os.path.basename(fp), timeout=2.0)
                    return f"Открываю '{os.path.basename(fp)}'."
                except Exception as e: return f"Ошибка: {e}"
            if prog and prog in APP_DICT:
                subprocess.Popen(APP_DICT[prog], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Файл не нашла, но открыла {prog}."
            return f"Не нашла '{fname}'."

        want_new, nw_name = winalias.split_new_window(raw)
        if want_new:                                          # «открой новое окно яндекса» — именно новое, а не показать старое
            if not nw_name:
                return "Новое окно чего? Скажи, например: «открой новое окно яндекса» или «…хрома»."
            cmd = winalias.new_window_command(nw_name)
            if not cmd:
                return "Новое окно умею открывать у браузеров: Яндекс, Хром, Эдж, Фаерфокс."
            try:
                subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return f"Не получилось открыть новое окно: {e}"
            return f"Открываю новое окно {nw_name}."

        prog_lower = raw.lower().strip()
        if prog_lower in APP_DICT:
            try:
                exe_b = os.path.basename(APP_DICT[prog_lower]).lower()
                if exe_b in winalias.BROWSER_EXES:    # браузер уже открыт — просто показать, не открывать новое окно
                    shown = focus_existing_app(exe_b)
                    if shown: return f"{raw} уже открыт, показала окно."
                subprocess.Popen(APP_DICT[prog_lower], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if APP_DICT[prog_lower].endswith(".exe") and " " not in APP_DICT[prog_lower]: _focus_new_app(APP_DICT[prog_lower], timeout=3.0)
                else: _bring_to_front(raw, timeout=3.0)
                return f"Открываю {raw}."
            except Exception: return f"Не удалось {raw}."

        if _FOLDER_KW.search(user_text):
            fn = _clean_folder_query(raw)
            p = _find_folder(fn) if fn else None
            if p:
                os.startfile(p)
                base = os.path.basename(p.rstrip("\\/"))
                _bring_to_front(base, timeout=3.0)
                return f"Открываю папку '{base or p}'."
            if fn:
                fp, fuzzy = _find_file_ex(fn)
                if not fp:
                    cand = _smart_find(ctx, fn, "папка")
                    if cand:
                        fc = _find_folder(cand)
                        if fc:
                            os.startfile(fc)
                            return f"Открываю '{os.path.basename(fc)}'."
                        fp, fuzzy = _find_file_ex(cand)
                if fp:
                    if not _fuzzy_run_ok(ctx, fp, fuzzy): return "Не запускаю."
                    os.startfile(fp)
                    return f"Открываю '{os.path.basename(fp)}'."
                return f"Не нашла '{fn}'."
            os.startfile(DESKTOP_PATH)
            return "Открываю рабочий стол."

        fp, fuzzy = _find_file_ex(raw)
        if fp:
            try:
                shown = focus_if_running(fp)          # программа уже открыта — показываем её окно, а не плодим новые
                if shown: return f"'{raw}' уже открыт, показала окно '{shown}'."
                if not _fuzzy_run_ok(ctx, fp, fuzzy): return "Не запускаю."
                os.startfile(fp)
                return f"Открываю '{os.path.basename(fp)}'."
            except Exception: pass

        print(f"🔍 Launch '{raw}'...")
        ok, method = _launch_by_name(raw)
        if ok:
            print(f"✅ {method}")
            return f"Открываю {raw}."

        cand = _smart_find(ctx, raw, "элемент")
        if cand:
            fp, fuzzy = _find_file_ex(cand)
            if fp:
                if not _fuzzy_run_ok(ctx, fp, fuzzy): return "Не запускаю."
                os.startfile(fp)
                return f"Открываю '{os.path.basename(fp)}'."
            fd = _find_folder(cand)
            if fd:
                os.startfile(fd)
                return f"Открываю '{os.path.basename(fd)}'."


        return f"Не нашла '{raw}'."

    if intent == "[ACTION_TYPE]":
        m = re.search(r"(?:напиши|введи|напечатай|вставь|ставь|впиши|запиши|добавь)\s+(.+)", user_text, re.IGNORECASE)
        if not m: return "Не поняла, что печатать."
        t = m.group(1).strip()
        t = re.sub(r"^(?:сюда|тут|здесь|в\s+(?:текущ\w*\s+|это\s+|этом\s+|это\s+же\s+|открыт\w*\s+)?(?:блокнот\w*|проводник\w*|окн\w*|окошк\w*))\s+", "", t, flags=re.IGNORECASE)
        t = re.sub(r"^текст\w*[.,!?;:\s]+", "", t, flags=re.IGNORECASE)
        t = re.sub(r"[,\s]+(?:в|во)\s+(?:\w+\s+)?(?:блокнот\w*|заметк\w*|текстовик\w*)\s*[.!]?\s*$", "", t, flags=re.IGNORECASE)
        t = t.lstrip(", ").strip()
        if not t: return "Не поняла, что вставлять."
        print(f"⌨️ {t[:80]}...")
        from intents import type_target
        if type_target(user_text.lower()) == "cursor":
            ok, where = type_at_cursor(t)
            if ok:
                return f"Напечатала в «{where[:40]}»." if where != "unverified" else \
                    "Напечатала. Если текста не видно — он в буфере, нажми Ctrl+V."
            try:
                pyperclip.copy(t)
            except Exception:
                pass
            if where == "своё окно":
                return "Сейчас активно моё окно — щёлкни туда, где нужно писать, и повтори. Текст пока в буфере."
            return "Не вижу, куда печатать — поставь курсор в поле ввода. Текст в буфере, можно нажать Ctrl+V."
        ok, why = insert_into_notepad(t)
        if ok and why == "unverified":
            return "Напечатала. Если текста не видно — он в буфере, нажми Ctrl+V."
        if ok:
            return f"Напечатала: {t[:60]}" + ("..." if len(t) > 60 else "")
        try:
            pyperclip.copy(t)
            return "Не смогла вставить, текст в буфере — нажми Ctrl+V." if why != "Блокнот не найден" else "Текст в буфере — Блокнот не найден."
        except Exception:
            return "Не удалось."

    # ==================== ТАЙМЕРЫ ====================
    if intent == "[ACTION_TIMER]":
        secs, _ = parse_duration(user_text)
        if not secs: return "На сколько поставить таймер? Скажи, например: таймер на пять минут."
        d = format_duration(secs)
        ctx.timers.add(secs, f"Таймер закончился! Прошло {d}.")
        return f"Поставила таймер. Время: {d}."

    if intent == "[ACTION_REMIND]":
        secs, what = parse_reminder(user_text)
        if not secs: return "Через сколько напомнить? Например: напомни через десять минут выключить чайник."
        ctx.timers.add(secs, f"Напоминаю: {what}." if what else "Напоминаю!")
        return f"Напомню через {format_duration(secs)}" + (f": {what}." if what else ".")

    if intent == "[ACTION_TIMER_CANCEL]":
        n = ctx.timers.cancel_all()
        return f"Отменила {n}." if n else "Таймеров нет."

    if intent == "[ACTION_TIMER_LIST]":
        items = ctx.timers.describe()
        if not items: return "Таймеров нет."
        return "Осталось: " + "; ".join(f"{left} — {msg}" for left, msg in items)

    return None


_PLAN_HELP = ("Скажи, например: «каждый день в 8:30 скажи погоду», «по будням в 9:00 напомни про планёрку», "
              "«в 23:00 сверни все окна», «каждый понедельник в 10:00 запусти сценарий работа».")
_RUNNABLE_EXTRA = {"[ACTION_WEATHER]", "[ACTION_READ]", "[ACTION_SCREEN]"}


def _step_intent(step):
    import intents
    intent, task = intents.get_intent(step)               # без LLM-роутера: шаг должен быть понятной командой
    if intent in intents.ACTION_INTENTS or intent in _RUNNABLE_EXTRA:
        if intent in ("[ACTION_PLAN]", "[ACTION_STOP]"):
            return None, None
        return intent, task
    return None, None


def run_scenario(ctx, name, steps, pause=0.8):
    """Шаги сценария по очереди, молча (говорит только итог). Возвращает текст ответа."""
    done, notes, infos = 0, [], []
    for i, step in enumerate(steps, 1):
        intent, task = _step_intent(step)
        if not intent:
            notes.append(f"«{step}» — не похоже на команду, пропустила")
            continue
        try:
            res = execute_pc_action(intent, step, task, ctx)
        except Exception as e:
            notes.append(f"«{step}» — ошибка {type(e).__name__}")
            continue
        print(f"   ▶ сценарий «{name}» {i}/{len(steps)}: {step} → {res}")
        done += 1
        if intent in ("[ACTION_WEATHER]", "[ACTION_TIME]", "[ACTION_READ]") and res:
            infos.append(res)
        if i < len(steps):
            time.sleep(pause)                             # окна и программы успевают открыться
    out = f"Сценарий «{name}»: выполнено {done} из {len(steps)}."
    if infos:
        out += " " + " ".join(infos)
    if notes:
        out += " Не вышло: " + "; ".join(notes) + "."
    return out


def _plan_action(ctx, user_text):
    """Расписания («каждый день в 8 скажи погоду») и сценарии («создай сценарий работа: …»)."""
    import scenarios
    pl = scenarios.get_planner()
    p = scenarios.parse(user_text)
    if not p:
        return "Не поняла, что запланировать. " + _PLAN_HELP
    kind = p[0]
    if kind == "plan_ask":
        return "Не поняла расписание: нужны время и действие. " + _PLAN_HELP
    if kind == "plan_add":
        spec = p[1]
        if spec["kind"] == "cmd" and _is_stop_phrase(spec["text"]):
            return "«Стоп» по расписанию не ставлю."
        pid = pl.add_plan(spec)
        if pid is None:
            return f"Расписаний уже {scenarios.MAX_PLANS} — удали ненужные («удали расписание 3»)."
        items = pl.list_plans()
        n = next((i for i, d in enumerate(items, 1) if d == scenarios.describe_plan(spec)), len(items))
        extra = ""
        m = re.match(r"^(?:запусти|выполни|включи)\s+сценари\w*\s+(.+)$", spec["text"])
        if m and not pl.find_scenario(m.group(1))[0]:
            extra = f" Такого сценария пока нет — создай: «создай сценарий {scenarios.clean_name(m.group(1))}: открой …, громкость 20»."
        return f"Поставила: {scenarios.describe_plan(spec)}. Это расписание №{n}." + extra
    if kind == "plan_list":
        items = pl.list_plans()
        if not items:
            return "Расписаний нет. " + _PLAN_HELP
        return "Расписание:\n" + "\n".join(f"{i}) {d}" for i, d in enumerate(items, 1))
    if kind == "plan_del":
        if p[1] == "ask":
            return "Какое удалить? Скажи номер: «удали расписание 2» или «удали все расписания»."
        if p[1] == "all":
            if not pl.list_plans():
                return "Расписаний нет."
            if not ctx.confirm("Удалить все расписания?"):
                return "Оставляю."
            return f"Удалила расписаний: {pl.delete_plan('all')}."
        return "Удалила расписание." if pl.delete_plan(p[1]) else "Нет расписания с таким номером. Список: «какое расписание»."
    if kind == "scen_ask":
        return "Скажи так: «создай сценарий работа: открой pycharm, громкость 20, открой яндекс»."
    if kind == "scen_save":
        name, steps = p[1], p[2]
        bad = [s for s in steps if not _step_intent(s)[0]]
        existed = pl.save_scenario(name, steps)
        out = f"{'Обновила' if existed else 'Запомнила'} сценарий «{name}»: шагов {len(steps)}. Запуск: «запусти сценарий {name}»."
        if bad:
            out += " Эти шаги я пока не пойму как команды: " + "; ".join(f"«{b}»" for b in bad) + "."
        return out
    if kind == "scen_list":
        names = list(pl.scenarios)
        return ("Сценарии: " + ", ".join(names) + ".") if names else "Сценариев нет. Создай: «создай сценарий работа: открой pycharm, громкость 20»."
    name = p[1]
    real, steps = pl.find_scenario(name)
    if kind == "scen_run":
        if not real:
            return f"Нет сценария «{name}»." + (f" Есть: {', '.join(steps)}." if steps else " Создай: «создай сценарий работа: открой pycharm, громкость 20».")
        return run_scenario(ctx, real, steps)
    if kind == "scen_show":
        if not real:
            return f"Нет сценария «{name}»."
        return f"Сценарий «{real}»:\n" + "\n".join(f"{i}) {s}" for i, s in enumerate(steps, 1))
    if kind == "scen_del":
        if not real:
            return f"Нет сценария «{name}»."
        if not ctx.confirm(f"Удалить сценарий «{real}»?"):
            return "Оставляю."
        pl.delete_scenario(real)
        return f"Удалила сценарий «{real}»."
    return "Не поняла."


def _is_stop_phrase(text):
    import intents
    return intents.get_intent(text)[0] == "[ACTION_STOP]"


def _set_allow_media(ctx, on):
    from settings import update_config
    ctx.config["allow_media"] = bool(on)
    try:
        update_config("allow_media", ctx.config["allow_media"])
    except Exception as e:
        print(f"⚠️ Не смогла сохранить настройку камеры/микрофона: {e}")


def _media_action(ctx, task):
    """Веб-камера и микрофон. По умолчанию выключено; включается отдельной командой с подтверждением."""
    import mediacap
    p = (task or "").split("|")
    if p[0] == "grant":
        if p[1] == "off":
            _set_allow_media(ctx, False)
            return "Закрыла доступ к веб-камере и микрофону."
        if mediacap.allowed(ctx.config):
            return "Доступ к камере и микрофону уже разрешён. Закрыть: «запрети камеру и микрофон»."
        if not ctx.confirm("Разрешить мне снимать с веб-камеры и слушать микрофон по твоей команде? "
                           "Отключить можно в любой момент: «запрети камеру и микрофон».", force=True):
            return "Оставляю доступ к камере и микрофону выключенным."
        _set_allow_media(ctx, True)
        return "Разрешила. Теперь работают «сними с веб-камеры» и «что ты слышишь»."
    if not mediacap.allowed(ctx.config):
        return ("Доступ к веб-камере и микрофону выключен (так безопаснее). "
                "Чтобы включить, скажи «разреши камеру и микрофон».")
    if p[0] == "cam":
        print("🎥 Снимаю с веб-камеры по команде.")
        try:
            data = mediacap.capture_photo()
        except RuntimeError as e:
            return f"Не вышло снять: {e}."
        if ctx.can_send_files():
            ctx.send_file(data, "webcam.jpg", "Снимок с веб-камеры", photo=True)
            return "Скинула снимок с веб-камеры."
        from settings import TEMP_DIR
        os.makedirs(TEMP_DIR, exist_ok=True)
        path = os.path.join(TEMP_DIR, time.strftime("webcam_%Y-%m-%d_%H-%M-%S.jpg"))
        with open(path, "wb") as f:
            f.write(data)
        return f"Сняла и сохранила: {path}"
    if p[0] == "mic":
        sec = int(p[1]) if len(p) > 1 else mediacap.MIC_DEFAULT_SEC
        send_audio = len(p) > 2 and p[2] == "1"
        import stt
        from settings import TEMP_DIR
        os.makedirs(TEMP_DIR, exist_ok=True)
        path = os.path.join(TEMP_DIR, f"mic_{int(time.time() * 1000)}.wav")
        print(f"🎙 Слушаю микрофон {sec} с по команде.")
        stt.PAUSE_WAKE.set()                       # пока пишем комнату, на «Рокси» не реагируем
        try:
            level = mediacap.record_mic(sec, path)
        except RuntimeError as e:
            return f"Не вышло послушать: {e}."
        finally:
            stt.PAUSE_WAKE.clear()
        audio_bytes = None
        if send_audio and ctx.can_send_files():
            with open(path, "rb") as f:
                audio_bytes = f.read()
        if level < mediacap.SILENCE_RMS:
            try:
                os.remove(path)
            except OSError:
                pass
            if audio_bytes:
                ctx.send_file(audio_bytes, "mic.wav", f"Запись {sec} с (тихо)", photo=False)
            return f"За {sec} с было тихо — ничего не слышу."
        text = stt.transcribe(path, use_prompt=False)     # удаляет файл сам
        if audio_bytes:
            ctx.send_file(audio_bytes, "mic.wav", f"Запись {sec} с", photo=False)
        return f"Слышу: «{text}»" if text else f"За {sec} с звук был, но слов я не разобрала."
    return "Не поняла, что сделать с камерой или микрофоном."


def _screen_after(ctx, delay=0.4):
    """Команда мышью/клавишами пришла с телефона — шлём снимок экрана с сеткой и курсором,
    чтобы было видно, куда попало (config «tg_screen_after_mouse», по умолчанию вкл)."""
    from settings import live_config
    if not ctx.from_chat() or not live_config("tg_screen_after_mouse", True):
        return
    try:
        import screengrid
        time.sleep(delay)                     # меню после правого клика успевает открыться
        data, cap = screengrid.screenshot(grid=True)
        ctx.send_file(data, "screen.jpg", cap, photo=True)
    except Exception as e:
        print(f"⚠️ Снимок после действия: {type(e).__name__}: {e}")


_NO_PHONE = ("Это работает через управление с телефона, а оно сейчас не подключено "
             "(нужен Telegram-аккаунт и чат «tg_remote_chat» в config.json).")


def _phone_action(ctx, kind, arg=""):
    import phonefiles
    if kind in ("screen", "grid"):
        if not ctx.can_send_files():
            return _NO_PHONE
        import screengrid
        data, cap = screengrid.screenshot(grid=(kind == "grid"))
        ctx.send_file(data, "screen.jpg", cap, photo=True)
        return "Скинула сетку." if kind == "grid" else "Скинула скриншот."

    if kind == "clip_set":
        pyperclip.copy(arg)
        return "Положила в буфер обмена" + (f": «{arg[:40]}…»." if len(arg) > 40 else f": «{arg}».")

    if kind == "clip_get":
        what, val = phonefiles.clipboard_get()
        if what == "empty":
            return "В буфере обмена пусто (или там что-то, что я не умею прочитать)."
        if what == "text":
            return "В буфере:\n" + (val if len(val) <= 3500 else val[:3500] + "…\n(обрезала, всего " + str(len(val)) + " символов)")
        if what == "files":
            names = ", ".join(os.path.basename(f) for f in val[:10])
            return f"В буфере файлы: {names}" + (f" и ещё {len(val) - 10}" if len(val) > 10 else "") + "."
        if not ctx.can_send_files():
            return "В буфере картинка. Показать её могу только в Telegram."
        import io
        buf = io.BytesIO()
        val.convert("RGB").save(buf, "PNG")
        ctx.send_file(buf.getvalue(), "clipboard.png", "Картинка из буфера", photo=False)
        return "В буфере картинка — скинула её."

    if kind == "file":
        if not ctx.can_send_files():
            return _NO_PHONE
        path, fuzzy = _find_file_ex(arg)
        if not path:
            return f"Не нашла «{arg}» на рабочем столе, в Документах, Загрузках и открытой папке."
        if phonefiles.is_secret(path, PROJECT_DIR):
            return f"«{os.path.basename(path)}» не отправляю: в таких файлах ключи и пароли."
        size = os.path.getsize(path)
        if size > phonefiles.MAX_BYTES:
            return f"«{os.path.basename(path)}» слишком большой ({size // 1024 ** 2} МБ)."
        if fuzzy and not ctx.confirm(f"Точного «{arg}» нет, нашла похожий: «{os.path.basename(path)}». Отправить его?",
                                     force=True):
            return "Не отправила."
        ok = ctx.send_file(path, os.path.basename(path), os.path.basename(path))
        return f"Скинула «{os.path.basename(path)}»." if ok else "Не получилось отправить файл."
    return "Не поняла."


def _sys_action(ctx, kind, arg=""):
    import syscmd
    if kind == "lock":
        return syscmd.lock()
    if kind == "sleep":
        if not ctx.confirm("Перевести компьютер в сон? Разбудить его с телефона я не смогу.", force=True):
            return "Не усыпляю."
        syscmd.sleep_later(5.0)
        return "Усыпляю компьютер через 5 секунд."
    if kind == "status":
        return syscmd.status()
    if kind == "procs":
        return syscmd.running_apps()
    if kind == "kill":
        exe = syscmd.resolve_proc(arg)
        if not exe:
            return f"Не знаю, как называется процесс «{arg}». Скажи по-английски, например «заверши chrome»."
        if exe in syscmd.PROTECTED:
            return f"{exe} — системный процесс, его не закрываю."
        procs = syscmd.find_procs(exe)
        if not procs:
            return f"{exe} не запущен."
        if not ctx.confirm(f"Принудительно закрыть {exe} ({len(procs)} проц.)? Несохранённое пропадёт.", force=True):
            return "Не закрываю."
        return syscmd.kill(exe, procs)
    return "Не поняла."


def _clear_folder_confirmed(ctx, folder):
    name = _base(folder)
    if is_clear_forbidden(folder, PROJECT_DIR, (DESKTOP_PATH,), allow=(DESKTOP_PATH,)):
        return (f"Папку «{name}» целиком не очищаю: она системная, личная или в ней лежит проект. "
                f"Скажи, какой файл удалить, или очисти вручную.")
    n = count_folder_items(folder)
    if n == 0:
        return f"Папка '{name}' уже пуста."
    if not ctx.confirm(f"Отправить в корзину всё из папки «{name}»? Это {n} объектов."):
        return "Не удаляю."
    d, f = _clear_folder_contents(folder)
    return f"Очистила '{name}': удалено {d}, ошибок {f}."
