"""Рокси — точка входа: запуск Live2D, GUI, основной голосовой цикл, корректное завершение."""
import os
import sys
import queue
import re
import shutil
import time
import atexit
import socket
import threading
import subprocess

import settings
from settings import (config, update_config, get_tg_credentials, MOANS_DIR, LIVE2D_SCRIPT, LIVE2D_PORT,
                      TEMP_DIR, MISSING_SECRETS)
import gui
gui.install_redirect()     # до остальных импортов, чтобы их предупреждения попали в лог

import keyboard
import audio
import stt
import bargein
import latency
import intents
import llm
from live2d_client import LIVE2D
from text_tts import extract_emotion
from timers import TimerManager
from library import find_work_in_library
from actions import execute_pc_action, ActionContext
from telegram_service import _TELEGRAM_OK, _tg_session_exists
from winutil import _force_foreground

VOICE_MODE = config.get("voice_mode", "anime")
_live2d_process = None
_current_intent = None      # для confirm(): повтор той же команды = «да»
_shutting_down = threading.Event()


# ==================== РЕЧЬ ====================
# Команда пришла из Telegram-бота: ответы уходят сообщением в чат, а не голосом (тебя у компьютера нет)
_sink = threading.local()


def _bot_reply():
    return getattr(_sink, "reply", None)


def say(text, default_emotion=None):
    """Озвучивает текст; тег эмоции ([happy] ...) уходит в Live2D, а не в TTS."""
    emotion, clean = extract_emotion(text or "")
    emotion = emotion or default_emotion
    if emotion:
        LIVE2D.emotion(emotion)
    if _bot_reply():
        _bot_reply()(clean)
        return clean
    if clean and not bargein.pending():          # перебили голосом — остальное этой команды не договариваем
        with bargein.Monitor():
            audio.speak(clean, VOICE_MODE)
    return clean


def say_stream(sentences, default_emotion=None):
    """Как say(), но для ответа, который ещё генерируется: тег эмоции из первого предложения — сразу в модель,
    дальше предложения озвучиваются по мере поступления."""
    def gen():
        first = True
        for sent in sentences:
            if first:
                first = False
                emotion, sent = extract_emotion(sent or "")
                emotion = emotion or default_emotion
                if emotion:
                    LIVE2D.emotion(emotion)
            else:
                _, sent = extract_emotion(sent or "")     # теги посреди ответа не озвучиваем
            if sent and sent.strip():
                yield sent
    if _bot_reply():
        _bot_reply()(" ".join(gen()))
        return True
    if bargein.pending():
        return False
    with bargein.Monitor():
        return audio.speak_stream(gen(), VOICE_MODE)


def confirm(question):
    """Голосовое подтверждение. По умолчанию — НЕТ (молчание, непонятный ответ)."""
    if getattr(_sink, "ask", None):                 # из Telegram — спрашиваем там же
        _, q = extract_emotion(question)
        verdict = intents.parse_yes_no(_sink.ask(q) or "")
        if not verdict:
            _sink.reply("Отменяю." if verdict is False else "Не дождалась ответа. Отменяю.")
        return bool(verdict)
    say(question, default_emotion="surprised")
    for attempt in range(2):
        pre = bargein.take_pending()             # ответил, не дослушав вопрос, — это и есть ответ
        ans = stt.listen_answer(timeout=6.0, beep_fn=None if pre else audio.beep, preroll=pre)
        verdict = intents.parse_yes_no(ans)
        if verdict is None and ans and _current_intent and intents.get_intent(ans)[0] == _current_intent:
            print("🔁 Повтор той же команды — считаю за «да».")
            verdict = True
        if verdict is not None:
            print(f"✅ Подтверждение: {'да' if verdict else 'нет'}")
            return verdict
        if attempt == 0:
            say("Скажи да или нет.")
    say("Не расслышала. Отменяю.")
    return False


REMOTE = None          # управление с телефона (tg_remote.TgRemote), если подключено


def _remote_channel():
    """Для действий: куда слать файлы/снимки и пришла ли команда из чата.
    Из чата — в тот же чат; голосом («пришли скриншот на телефон») — тоже в чат управления, если он есть."""
    fn = getattr(_sink, "send_file", None)
    if fn:
        return fn, True
    return (REMOTE.send_file if REMOTE else None), False


def _notify_phone(text):
    """Таймер/напоминание дублируем на телефон (config «tg_notify_timers», по умолчанию вкл) — вдруг ты не у компа."""
    if REMOTE is None or not settings.live_config("tg_notify_timers", True):
        return
    threading.Thread(target=lambda: REMOTE.send(text), daemon=True, name="tg-notify").start()


def _on_timer(message):
    print(f"⏰ {message}")
    _notify_phone(f"⏰ {message}")
    # Весь сигнал + речь — под замком речи: sounddevice.play() останавливает предыдущее воспроизведение,
    # поэтому раньше таймер обрывал ответ Рокси на полуслове. Теперь он ждёт, пока она договорит.
    with audio._speech_lock:
        audio.beep(1200, 0.15)
        audio.beep(1200, 0.15)
        say(message, default_emotion="happy")


def _on_plan_due(p):
    """Сработало расписание («каждый день в 8 скажи погоду»): напоминание — как таймер, команда — как будто её сказали."""
    import scenarios
    print(f"🗓 По расписанию: {p['text']}")
    if p["kind"] == "remind":
        _on_timer(scenarios.reminder_phrase(p["text"], p.get("late_min")))     # «Опоздала на …», если ПК спал
        return
    _notify_phone(f"🗓 По расписанию: {p['text']}")
    stt.PAUSE_WAKE.set()                 # микрофон свободен для «да/нет», если команда спросит подтверждение
    try:
        with _handle_lock:
            handle(p["text"])
    except Exception as e:
        print(f"❌ Расписание «{p['text']}»: {type(e).__name__}: {e}")
    finally:
        stt.PAUSE_WAKE.clear()


TIMERS = TimerManager(_on_timer, storage_path=os.path.join(settings.PROJECT_DIR, "timers.json"))
CTX = ActionContext(config, confirm=confirm, timers=TIMERS, remote=_remote_channel)


# ==================== ГОРЯЧИЕ КЛАВИШИ ====================
def _interrupt(_=None):
    """Num Lock / Pause: перебить речь, остановить запись, остановить агента."""
    audio.stop_playback()
    stt.stop_recording()


def _install_hotkeys():
    for key in ("num lock", "pause"):
        try:
            keyboard.on_press_key(key, _interrupt)
        except Exception as e:
            print(f"⚠️ Hotkey {key}: {e}")


# ==================== ОСНОВНОЙ ЦИКЛ ====================
def _get_intent(text):
    return intents.get_intent(
        text,
        library_lookup=find_work_in_library,
        code_analyzer=llm.analyze_and_extract,
        llm_router=llm.llm_route if config.get("use_llm_router", True) else None,
    )


def handle(text):
    global VOICE_MODE, _current_intent
    intent, task = _get_intent(text)
    _current_intent = intent
    print(f"🧭 Intent: {intent}")
    latency.mark("intent")
    latency.note(intent)

    if intent == "[ACTION_STOP]":
        if _bot_reply():                 # из Telegram: включить Рокси обратно с телефона нельзя
            print("📱 «Стоп» из Telegram — не выключаюсь.")
            _bot_reply()("Выключить Рокси можно только с компьютера или голосом.")
            return
        print("👋 СТОП.")
        LIVE2D.emotion("sad", 3000)
        if getattr(_sink, "flush", None):
            _sink.reply("Выключаюсь. Пока!")
            _sink.flush()
        try:
            # прощание голосом Piper: офлайн и без RVC — выключение не ждёт интернета
            audio.speak("Выключаюсь. Пока!", "piper")
        except Exception:
            pass
        shutdown()

    visible = gui.is_visible_wanted()
    if visible and intent not in ("[ACTION_SHOW_GUI]", "[ACTION_HIDE_GUI]"):
        gui.hide_temporarily()
        time.sleep(0.25)

    try:
        if intent in ("[MODE_FAST]", "[MODE_ANIME]"):
            VOICE_MODE = "fast" if intent == "[MODE_FAST]" else "anime"
            config["voice_mode"] = VOICE_MODE
            update_config("voice_mode", VOICE_MODE)
            if VOICE_MODE == "anime" and config.get("rvc_worker", True):
                from rvc_client import WORKER
                WORKER.start()
            say("[happy] Переключаюсь на быстрый режим." if VOICE_MODE == "fast" else "[happy] Возвращаю аниме-голос.")
        elif intent == "[ACTION_MOAN]":
            LIVE2D.emotion("shy", 4000)
            if not audio.play_random_sound(MOANS_DIR):
                say("Не могу найти звуки.")
        elif intent == "[ACTION_PURR]":
            say("[happy] Мур мур мур~")
        elif intent in intents.ACTION_INTENTS or intent in ("[ACTION_WEATHER]", "[ACTION_READ]", "[ACTION_SCREEN]"):
            t0 = time.monotonic()
            result = execute_pc_action(intent, text, task, CTX) or "Не удалось."
            dt = time.monotonic() - t0
            if dt > 1.5:
                print(f"⏱ {intent} выполнялся {dt:.1f} c")
            emotion, clean = extract_emotion(result)
            print(f"✅ {clean}")
            if intent not in intents._SILENT_INTENTS:
                say(result)
            elif _bot_reply():
                _bot_reply()(clean)                          # молча — для голоса; в чат всё же ответим
            elif emotion:
                LIVE2D.emotion(emotion)
            llm._push_history("user", text)
            llm._push_history("assistant", clean)
        elif intent == "[CHAT]":
            say_stream(llm.think_llm_stream(text, intent))     # говорит с первого готового предложения
        else:
            say(llm.think_llm(text, intent))
    finally:
        latency.done()                                    # молчаливая команда — замер не «прилипнет» к следующему звуку
        if visible and intent not in ("[ACTION_SHOW_GUI]", "[ACTION_HIDE_GUI]"):
            gui.set_visible(True, force_foreground=_force_foreground)


# ==================== ПИСЬМЕННЫЙ ВВОД ====================
_handle_lock = threading.Lock()          # голос и текст обрабатываются по очереди, не одновременно
_typed = queue.Queue()


_RE_TYPED_WAKE = re.compile(r"^\s*(?:эй\s*,?\s*)?рокси\b[\s,!.:;—-]*", re.IGNORECASE)


def on_typed(text):
    """Из поля ввода (над головой Рокси или в консоли): написанное — как сказанное голосом.
    Обращение «Рокси, …» в начале убираем: при письме звать её не нужно."""
    _typed.put(_RE_TYPED_WAKE.sub("", text, count=1).strip())


def _on_avatar_event(d):
    """События из окна аватара: написанное в поле над её головой; старый пункт трея — консоль."""
    if d.get("type") == "typed" and isinstance(d.get("text"), str) and d["text"].strip():
        on_typed(d["text"].strip()[:2000])
    elif d.get("type") == "ui" and d.get("action") == "type":
        gui.show_input(_force_foreground)


def typed_loop():
    while not _shutting_down.is_set():
        try:
            text = _typed.get(timeout=0.5)
        except queue.Empty:
            continue
        print(f"⌨️ Ты написал: {text or '(только имя)'}")
        if not text:                     # написали одно «Рокси» — отзываемся, как на голос
            say("Я тут. Напиши, что сделать.")
            continue
        stt.PAUSE_WAKE.set()             # микрофон свободен: подтверждение «да/нет» услышим как обычно
        try:
            audio.stop_playback()        # написала — перебиваем текущую речь, как Num Lock
            with _handle_lock:
                handle(text)
        except Exception as e:
            print(f"❌ Текст: {type(e).__name__}: {e}")
        finally:
            stt.PAUSE_WAKE.clear()


# ==================== TELEGRAM-БОТ ====================
def _bot_handle(text, reply, ask, flush, send_file=None):
    print(f"📱 Из Telegram: {text}")
    with _handle_lock:
        _sink.reply, _sink.ask, _sink.flush, _sink.send_file = reply, ask, flush, send_file
        try:
            handle(_RE_TYPED_WAKE.sub("", text, count=1).strip() or text)
        finally:
            _sink.reply = _sink.ask = _sink.flush = _sink.send_file = None


def _bot_set_owner(uid):
    if uid is None:
        config.pop("tg_bot_owner", None)
    else:
        config["tg_bot_owner"] = int(uid)
    update_config("tg_bot_owner", config.get("tg_bot_owner"))        # None — ключ уберётся


def _start_remote_account():
    """Чат с ботом (или «Избранное»), который Рокси читает через ТВОЙ аккаунт (Telethon) — Bot API не нужен."""
    chat = (config.get("tg_remote_chat", "RoxyAIAssistant_bot") or "").strip().lstrip("@")
    if not chat or not _TELEGRAM_OK or not _tg_session_exists() or not get_tg_credentials(config)[0]:
        return False
    try:
        from telegram_service import TG_SERVICE, _init_telegram_if_needed
        ok, why = _init_telegram_if_needed(config)
        if not ok:
            print(f"⚠️ Управление с телефона: Telegram не подключился ({why})")
            return False
        global REMOTE
        from tg_remote import TgRemote
        r = TgRemote(TG_SERVICE, chat, _bot_handle,
                     transcribe=stt.transcribe if config.get("tg_voice", True) else None)
        r.start()
        REMOTE = r
        return True
    except Exception as e:
        print(f"⚠️ Управление с телефона через аккаунт: {type(e).__name__}: {e}")
        return False


def _start_bot():
    def run():
        # сначала через свой аккаунт (работает и там, где Bot API отвечает 429); не вышло — через Bot API.
        # Одновременно оба не включаем: иначе одно сообщение боту выполнилось бы дважды.
        if _start_remote_account():
            return
        if not settings.TG_BOT_TOKEN:
            return
        try:
            from telegram_bot import RoxyBot
            RoxyBot(settings.TG_BOT_TOKEN, _bot_handle, lambda: config.get("tg_bot_owner"), _bot_set_owner).start()
        except Exception as e:
            print(f"⚠️ Telegram-бот: {e}")
    threading.Thread(target=run, daemon=True, name="tg-start").start()


def assistant_loop():
    print("=== ROXY ===")
    print(f"Голос: {VOICE_MODE.upper()}")
    print("Скажи 'Рокси' → команда. Стоп-слово: 'Рокси, стоп'. Перебить: Num Lock / Pause.")
    if _TELEGRAM_OK and get_tg_credentials(config)[0] and _tg_session_exists():
        print("💡 Telegram: сессия найдена.")
    if MISSING_SECRETS:
        print("⚠️ Не заданы ключи: " + ", ".join(MISSING_SECRETS) +
              ". Скопируй .env.example в .env и впиши значения (без них облачные функции не работают).")
    os.makedirs(MOANS_DIR, exist_ok=True)
    try:
        stt.MIC.start()
    except Exception as e:
        print(f"❌ Микрофон: {e}")
        return
    print(f"✋ Перебивание голосом: {'вкл — просто заговори, пока она отвечает' if bargein.ENABLED else 'выкл'}")
    while not _shutting_down.is_set():
        try:
            pre = bargein.take_pending()
            if pre is not None:              # перебили голосом — сразу записываем, без «Рокси»
                path = stt.record_command(pre, no_speech_timeout=4.0)
                text = stt.transcribe(path) if path else ""
                if text:
                    print(f"👤 Ты сказал: {text}")
            else:
                text = stt.listen(beep_fn=audio.beep)
            if text:
                with _handle_lock:
                    handle(text)
        except KeyboardInterrupt:
            shutdown()
        except Exception as e:
            print(f"❌ Цикл: {type(e).__name__}: {e}")
            time.sleep(1)


# ==================== ЗАПУСК / ЗАВЕРШЕНИЕ ====================
_TEMP_PREFIXES = ("rvc_", "tts_", "speak_", "bench_")


def _cleanup_temp(max_age_sec=3600):
    """Удаляет СТАРЫЕ рабочие папки синтеза из temp/ (их создаёт сам проект). os._exit пропускает finally,
    поэтому за годы работы там копился мусор. Чужие файлы и свежие папки не трогаем."""
    now = time.time()
    removed = 0
    try:
        for name in os.listdir(TEMP_DIR):
            path = os.path.join(TEMP_DIR, name)
            if not (os.path.isdir(path) and name.startswith(_TEMP_PREFIXES)):
                continue
            try:
                if now - os.path.getmtime(path) > max_age_sec:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
    except OSError:
        return
    if removed:
        print(f"🧹 temp/: удалено старых папок: {removed}")


def _warmup_models():
    if VOICE_MODE == "anime" and config.get("rvc_worker", True):
        from rvc_client import WORKER
        WORKER.start()
    models = [("Vosk", stt.get_vosk_model), ("Piper", audio.get_piper_voice)]
    if config.get("preload_whisper", True):
        models.append(("Whisper", stt.get_whisper_model))
    for name, fn in models:
        try:
            fn()
            print(f"🔥 {name} готов.")
        except Exception as e:
            print(f"⚠️ {name} warmup: {e}")


def _is_port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _kill_zombie_live2d():
    """Убивает старые процессы live2d_window.py. wmic в свежих Windows 11 выключен,
    поэтому сначала psutil, потом PowerShell."""
    killed = 0
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "live2d_window.py" in cmd and p.pid != os.getpid():
                try:
                    p.kill()
                    killed += 1
                except Exception:
                    pass
    except ImportError:
        try:
            ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*live2d_window.py*' } "
                  "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                               timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            killed = len([x for x in r.stdout.split() if x.isdigit()])
        except Exception:
            pass
    if killed:
        print(f"🧟 Убила зомби Live2D: {killed}")
        for _ in range(20):            # ждём, пока порт освободится
            if not _is_port_open("127.0.0.1", LIVE2D_PORT, 0.2):
                break
            time.sleep(0.15)


def _start_live2d():
    global _live2d_process
    _kill_zombie_live2d()
    if _is_port_open("127.0.0.1", LIVE2D_PORT):
        print("💡 Live2D уже запущен — подключаюсь к существующему окну.")
    else:
        print("Запускаю Live2D окно...")
        try:
            # Вывод окна Live2D (в т.ч. ошибки JavaScript) идёт в общую консоль И в logs/roxy.log
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            _live2d_process = subprocess.Popen(
                [sys.executable, LIVE2D_SCRIPT], cwd=settings.PROJECT_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")

            def _pump(p=_live2d_process):
                for line in p.stdout:
                    line = line.rstrip()
                    if line:
                        print(f"[Live2D] {line}")
                # «Выключить Рокси» в трее: окно выходит с кодом 42 — выключаемся целиком, с обычной уборкой
                try:
                    if p.wait(timeout=5) == 42:
                        print("👋 Выключение из трея.")
                        _cleanup()
                        os._exit(0)
                except Exception:
                    pass
            threading.Thread(target=_pump, daemon=True, name="live2d-log").start()
        except Exception as e:
            print(f"⚠️ Не удалось запустить Live2D: {e}")
    LIVE2D.on_event = _on_avatar_event
    LIVE2D.start()
    audio.set_lipsync_sink(LIVE2D.mouth)
    audio.set_viseme_sink(LIVE2D.viseme)        # губы по звукам: А/И/У/Э/О


def _cleanup():
    """Вызывается при ЛЮБОМ выходе: стоп-слово, кнопка X, Ctrl+C. Раньше Live2D оставался висеть."""
    TIMERS.stop()          # потоки останавливаем, а файл timers.json оставляем: напоминания переживут выключение
    try:
        import scenarios
        scenarios.get_planner().stop()
    except Exception:
        pass
    LIVE2D.stop()
    try:
        from rvc_client import WORKER
        WORKER.stop()
    except Exception:
        pass
    if _live2d_process is not None and _live2d_process.poll() is None:
        try:
            _live2d_process.terminate()
            _live2d_process.wait(timeout=3)
            print("🔌 Live2D закрыт вместе с main.py")
        except Exception:
            try: _live2d_process.kill()
            except Exception: pass
    llm.MEMORY.save()


def shutdown():
    if _shutting_down.is_set():
        os._exit(0)
    _shutting_down.set()
    try:
        _cleanup()
    finally:
        sys.stdout.flush()
        os._exit(0)


STOP_FLAG = os.path.join(TEMP_DIR, "roxy_stop.flag")


def _take_launcher_messages():
    """Поле «Написать Рокси» в лаунчере кладёт temp/roxy_typed_*.txt — читаем по порядку и удаляем."""
    try:
        names = sorted(n for n in os.listdir(TEMP_DIR) if n.startswith("roxy_typed_") and n.endswith(".txt"))
    except OSError:
        return
    for n in names:
        p = os.path.join(TEMP_DIR, n)
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read().strip()
            os.remove(p)
        except OSError:
            continue
        if text:
            on_typed(text[:2000])


def _watch_stop_flag():
    """Лаунчер (roxy_launcher.py) выключает Рокси мягко: кладёт temp/roxy_stop.flag — замечаем и выходим с уборкой.
    Заодно забираем то, что написали в лаунчере."""
    while not _shutting_down.is_set():
        _take_launcher_messages()
        if os.path.exists(STOP_FLAG):
            try:
                os.remove(STOP_FLAG)
            except OSError:
                pass
            print("👋 Выключение из лаунчера.")
            shutdown()
        time.sleep(0.5)


if __name__ == "__main__":
    atexit.register(_cleanup)
    try:
        if os.path.exists(STOP_FLAG):              # старый флаг от прошлого раза не должен сразу выключить
            os.remove(STOP_FLAG)
        for _n in os.listdir(TEMP_DIR):            # и старые недоставленные сообщения из лаунчера
            if _n.startswith("roxy_typed_") and _n.endswith(".txt"):
                os.remove(os.path.join(TEMP_DIR, _n))
    except OSError:
        pass
    threading.Thread(target=_watch_stop_flag, daemon=True, name="stop-flag").start()
    _cleanup_temp()
    _start_live2d()
    _install_hotkeys()
    threading.Thread(target=_warmup_models, daemon=True).start()
    threading.Thread(target=assistant_loop, daemon=True, name="assistant").start()
    threading.Thread(target=typed_loop, daemon=True, name="typed").start()
    _start_bot()
    # после запуска остального: просроченные напоминания озвучатся с паузой, когда голос уже прогрет
    _pending, _overdue = TIMERS.restore(startup_delay=10.0)
    if _pending or _overdue:
        print(f"⏰ Таймеры восстановлены: ждут {_pending}, просрочено (сработают сейчас) {_overdue}")
    try:
        import scenarios
        _pl = scenarios.get_planner()
        _pl.start(_on_plan_due)
        if _pl.plans or _pl.scenarios:
            print(f"🗓 Расписаний: {len(_pl.plans)}, сценариев: {len(_pl.scenarios)}")
    except Exception as plan_err:
        print(f"⚠️ Расписания не запустились: {plan_err}")
    try:
        gui.start_gui(on_close=shutdown, on_text=on_typed)
    except KeyboardInterrupt:
        print("Выход.")
    shutdown()
