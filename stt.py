"""Микрофон, wake-word и распознавание речи.

Изменения:
* Один непрерывный поток с микрофона. Раньше после «Рокси» поток открывался заново,
  и начало фразы «Рокси, открой блокнот» (сказанной на одном дыхании) терялось.
  Теперь последняя секунда перед срабатыванием (pre-roll) идёт в запись.
* Бип «слушаю» после wake-word.
* VAD: webrtcvad, если установлен (pip install webrtcvad-wheels), иначе порог громкости.
* Запись можно остановить Num Lock / Pause (stop_recording()).
"""
import os
import sys
import json
import time
import queue
import socket
import threading
import collections

import numpy as np
import sounddevice as sd
import soundfile as sf
import vosk
from groq import Groq

from settings import TEMP_DIR, VOSK_MODEL_PATH, WHISPER_MODEL_PATH, GROQ_API_KEY

try:
    import webrtcvad
    _VAD = webrtcvad.Vad(2)
except Exception:
    _VAD = None

SR = 16000
BLOCK = 4000                       # 0.25 c
WAKE_WORDS = ("рокси", "рокса", "роксик", "рокс")
_VOSK_GRAMMAR = json.dumps(list(WAKE_WORDS) + ["[unk]"], ensure_ascii=False)
TEMP_MIC_WAV = os.path.join(TEMP_DIR, "input_mic.wav")

# 8 c: при плохом интернете лучше быстрее уйти на локальный Whisper, чем ждать 2×15 c
STT_TIMEOUT = 5.0          # короткие фразы Groq распознаёт за ~1 с; дольше ждать нет смысла — есть локальный Whisper
groq_stt_client = Groq(api_key=GROQ_API_KEY, timeout=STT_TIMEOUT, max_retries=0) if GROQ_API_KEY else None

_STT_PROMPT_FULL = (
    "Рокси. Открой блокнот. Открой калькулятор. Открой проводник. Постонай. Стони. Помурчи. Быстрый режим. "
    "Теорема синусов. Математика. Код. Создай файл. Создай папку. Создай скрипт. Сверни окно. Разверни окно. "
    "Погода в Москве. Открой папку на рабочем столе. Стоп. Остановись. Выключись. Пока. Удали текст из блокнота. "
    "Удали файл. Удали всё из папки. Закрой вкладку. Вставь сюда текст. Напиши текст. Сверни все окна. Скопируй. "
    "Вставь. Сохрани. Отмени. Открой PyCharm. Закрой все окна. Раскрой окно во весь экран. Поставь таймер на 5 минут. "
    "Напомни через 10 минут. Сделай громче. Ответь Васе в телеграм что я опоздаю. Напиши в телеграм @username привет. "
    "Прочитай телеграм. Напиши Даниэлю то что я его люблю."
)


def _truncate_stt_prompt(prompt, limit=400):
    if len(prompt) <= limit:
        return prompt
    cut = prompt[:limit]
    for sep in (". ", ", ", " "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[:idx].strip()
    return cut.strip()


_STT_PROMPT = _truncate_stt_prompt(_STT_PROMPT_FULL, 400)

# ==================== МОДЕЛИ ====================
_whisper_model = None
_whisper_lock = threading.Lock()
_vosk_model = None
_vosk_lock = threading.Lock()


def get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            print("🔄 Загружаю Whisper...")
            _whisper_model = WhisperModel(WHISPER_MODEL_PATH, device="cpu", compute_type="int8", cpu_threads=8)
    return _whisper_model


def get_vosk_model():
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model
    with _vosk_lock:
        if _vosk_model is None:
            print("🔄 Загружаю Vosk...")
            _vosk_model = vosk.Model(VOSK_MODEL_PATH)
    return _vosk_model


# ==================== МИКРОФОН ====================
class Mic:
    """Постоянный поток с микрофона: блоки int16 по 0.25 c в очереди."""

    def __init__(self):
        self.q = queue.Queue(maxsize=400)   # ~100 c, дальше старое выкидывается
        self._stream = None
        self._lock = threading.Lock()
        self.last_block_at = time.monotonic()
        self._last_restart = 0.0

    def _cb(self, indata, frames, time_info, status):
        self.last_block_at = time.monotonic()
        if status:
            print(status, file=sys.__stderr__)
        # Пишем сырой звук. Усиление ×2 нужно только Vosk'у (см. wait_for_wake_word):
        # раньше усиливалось всё, шум вырастал выше порога тишины — и авто-стоп не срабатывал.
        a = np.frombuffer(indata, dtype=np.int16)
        try:
            self.q.put_nowait(a.tobytes())
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(a.tobytes())
            except Exception:
                pass

    @staticmethod
    def _device():
        """Микрофон из лаунчера (config «mic_device» — имя устройства); не нашли — системный по умолчанию."""
        try:
            from settings import live_config
            name = (live_config("mic_device", "") or "").strip()
        except Exception:
            name = ""
        if not name:
            return None
        try:
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0 and d.get("name") == name:
                    return i
            for i, d in enumerate(sd.query_devices()):     # имя могло чуть поменяться (другой порт USB)
                if d.get("max_input_channels", 0) > 0 and name.lower() in d.get("name", "").lower():
                    return i
        except Exception:
            pass
        print(f"⚠️ Микрофон «{name}» не найден — беру системный по умолчанию")
        return None

    def start(self):
        if self._stream is None:
            dev = self._device()
            try:
                self._stream = sd.RawInputStream(samplerate=SR, blocksize=BLOCK, channels=1, device=dev,
                                                 dtype="int16", callback=self._cb)
            except Exception as e:
                if dev is None:
                    raise
                print(f"⚠️ Выбранный микрофон не открылся ({e}) — беру системный по умолчанию")
                self._stream = sd.RawInputStream(samplerate=SR, blocksize=BLOCK, channels=1,
                                                 dtype="int16", callback=self._cb)
            self._stream.start()
            self.last_block_at = time.monotonic()

    def stalled(self, seconds=5.0):
        """Поток не отдаёт звук дольше `seconds` (отключили Bluetooth-наушники, сменилось устройство, сон ПК)."""
        now = time.monotonic()
        return now - self.last_block_at > seconds and now - self._last_restart > seconds

    def restart(self):
        """Пересоздать поток с микрофона. Раньше при его смерти ожидание «Рокси» крутилось вечно."""
        with self._lock:
            self._last_restart = time.monotonic()
            old, self._stream = self._stream, None
            if old is not None:
                try:
                    old.stop()
                    old.close()
                except Exception:
                    pass
            self.flush()
            try:
                self.start()
                return True
            except Exception as e:
                print(f"❌ Микрофон не перезапустился: {e}")
                return False

    def flush(self):
        """Выкинуть всё накопленное (например, пока Рокси сама говорила)."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def read(self, timeout=0.5):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


MIC = Mic()
_rec_stop = threading.Event()


def stop_recording():
    _rec_stop.set()


_noise = collections.deque(maxlen=80)     # громкость блоков, пока ждём «Рокси» (~20 c)
SPEECH_MIN_RMS = 0.006
_noise_reported = [False]


def _rms(block_bytes):
    a = np.frombuffer(block_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0


def noise_floor():
    if len(_noise) < 8:
        return None
    return float(np.percentile(np.fromiter(_noise, dtype=np.float32), 30))


def speech_threshold():
    """Порог речи подстраивается под шум комнаты: в 3 раза громче фона, но не ниже SPEECH_MIN_RMS."""
    f = noise_floor()
    return max(SPEECH_MIN_RMS, f * 3.0) if f is not None else 0.012


def _is_speech(block_bytes):
    if _rms(block_bytes) < speech_threshold():
        return False
    if _VAD is None:
        return True
    frame = int(SR * 0.03) * 2          # 30 мс, байты
    voiced = total = 0
    for i in range(0, len(block_bytes) - frame + 1, frame):
        total += 1
        try:
            if _VAD.is_speech(block_bytes[i:i + frame], SR):
                voiced += 1
        except Exception:
            pass
    return total > 0 and voiced / total >= 0.5


def _amplify(block_bytes, gain=2):
    a = np.frombuffer(block_bytes, dtype=np.int16).astype(np.int32) * gain
    return np.clip(a, -32768, 32767).astype(np.int16).tobytes()


# ==================== WAKE WORD ====================
PAUSE_WAKE = threading.Event()     # пока обрабатывается письменная команда — «Рокси» не слушаем (микрофон свободен для «да/нет»)


def wait_for_wake_word():
    """Ждёт «Рокси». Возвращает pre-roll (список блоков) или None при ошибке."""
    try:
        model = get_vosk_model()
    except Exception as e:
        print(f"❌ Vosk: {e}")
        return None
    rec = vosk.KaldiRecognizer(model, SR, _VOSK_GRAMMAR)
    preroll = collections.deque(maxlen=4)      # ~1 c
    MIC.flush()                                # не слушаем то, что говорила сама Рокси
    print("\n👂 Слушаю... (Скажи 'Рокси')")
    while True:
        if PAUSE_WAKE.is_set():
            time.sleep(0.1)
            MIC.flush()
            continue
        data = MIC.read(timeout=1.0)
        if data is None:
            if MIC.stalled():
                print("🎤 Микрофон молчит больше 5 секунд — перезапускаю поток.")
                if MIC.restart():
                    print("🎤 Поток микрофона перезапущен.")
            continue
        preroll.append(data)
        _noise.append(_rms(data))
        if not _noise_reported[0] and len(_noise) >= 40:
            _noise_reported[0] = True
            print(f"🎚 Шум микрофона: {noise_floor():.4f}, порог речи: {speech_threshold():.4f}")
        if rec.AcceptWaveform(_amplify(data)):
            t = json.loads(rec.Result()).get("text", "").lower()
        else:
            t = json.loads(rec.PartialResult()).get("partial", "").lower()
        if any(w in t for w in WAKE_WORDS):
            print("🎤 Услышала 'Рокси'!")
            return list(preroll)


# ==================== ЗАПИСЬ ====================
def record_command(preroll=None, silence_timeout=3.0, no_speech_timeout=8.0, max_record=30.0, beep_fn=None):
    """Пишет до 3 с тишины / Num Lock / лимита. Возвращает путь к wav или None."""
    _rec_stop.clear()
    if beep_fn:
        beep_fn()
    print("🔴 Говори! (Авто-стоп 3с тишины или Num Lock)")
    chunks = list(preroll or [])
    has_spoken = False
    start = last_voice = time.monotonic()
    vad_from = start + (0.4 if beep_fn else 0.0)   # бип не считается речью
    while not _rec_stop.is_set():
        data = MIC.read(timeout=0.3)
        now = time.monotonic()
        if data is not None:
            chunks.append(data)
            if now >= vad_from and _is_speech(data):
                has_spoken = True
                last_voice = now
        if has_spoken and now - last_voice > silence_timeout:
            print(f"⏱ {silence_timeout:.0f}с тишины — стоп.")
            break
        if not has_spoken and now - start > no_speech_timeout:
            print("🔇 Речь не обнаружена.")
            return None
        if now - start > max_record:
            print(f"⏱ Лимит {max_record:.0f}с.")
            break
    if _rec_stop.is_set():
        print("⌨️ Запись остановлена клавишей.")
    print("🛑 Запись остановлена.")
    if not chunks or not has_spoken and not _rec_stop.is_set():
        return None
    audio = np.frombuffer(b"".join(chunks), dtype=np.int16)
    if float(np.sqrt(np.mean((audio.astype(np.float32) / 32768.0) ** 2))) < 0.003:
        print("🔇 Микрофон не уловил голос.")
        return None
    sf.write(TEMP_MIC_WAV, audio, SR)
    return TEMP_MIC_WAV


# ==================== РАСПОЗНАВАНИЕ ====================
_inet_cache = {"ts": 0.0, "val": False}


def has_internet_connection(ttl=15.0):
    now = time.monotonic()
    if now - _inet_cache["ts"] < ttl:
        return _inet_cache["val"]
    ok = False
    for host, port in (("api.groq.com", 443), ("openrouter.ai", 443)):
        try:
            socket.create_connection((host, port), timeout=2.5).close()
            ok = True
            break
        except OSError:
            continue
    _inet_cache.update(ts=now, val=ok)
    return ok


_online_pause_until = 0.0     # после сбоя облака какое-то время распознаём сразу локально


def _pause_online(seconds, why):
    global _online_pause_until
    _online_pause_until = time.monotonic() + seconds
    print(f"💡 STT: {why} — следующие {int(seconds // 60)} мин распознаю локально, без ожидания облака.")


def transcribe_online(audio_path, use_prompt=True):
    """→ (текст | None, timed_out: bool)"""
    if groq_stt_client is None:
        return None, False
    print("🌐 STT онлайн (Groq)...")
    try:
        with open(audio_path, "rb") as file:
            t = groq_stt_client.audio.transcriptions.create(
                file=(os.path.basename(audio_path), file.read()),
                model="whisper-large-v3-turbo", language="ru", response_format="text",
                **({"prompt": _STT_PROMPT} if use_prompt else {}),
            )
        return (str(t).strip() or None), False
    except Exception as e:
        print(f"⚠️ STT online: {e}")
        msg = str(e).lower()
        if "forbidden" in msg or "403" in msg:
            _pause_online(600, "облако отказало в доступе (часто из-за VPN или региона)")
            return None, True                   # повторять бессмысленно
        timed_out = "timed out" in msg or "timeout" in type(e).__name__.lower()
        if timed_out:
            _pause_online(120, "облако не ответило вовремя")
        return None, timed_out


def transcribe_local(audio_path, use_prompt=True):
    print("💻 STT локально (Whisper)...")
    model = get_whisper_model()
    segments, _ = model.transcribe(audio_path, beam_size=1, language="ru", vad_filter=True,
                                   initial_prompt=_STT_PROMPT_FULL if use_prompt else None, without_timestamps=True)
    return "".join(s.text for s in segments).strip()


def transcribe(path, use_prompt=True):
    """use_prompt=False — для ответов «да/нет»: на тишине Whisper с подсказкой может «услышать»
    фразу из самой подсказки (так однажды «подтвердилось» удаление)."""
    text = None
    if time.monotonic() >= _online_pause_until and has_internet_connection():
        text, timed_out = transcribe_online(path, use_prompt)
        if not text and not timed_out:          # таймаут при плохом интернете не повторяем
            text, _ = transcribe_online(path, use_prompt)
    if not text:
        try:
            text = transcribe_local(path, use_prompt)
        except Exception as e:
            print(f"❌ Whisper: {e}")
    try:
        os.remove(path)
    except OSError:
        pass
    return (text or "").strip()


def listen(beep_fn=None):
    """Wake-word → запись → текст."""
    preroll = wait_for_wake_word()
    if preroll is None:
        time.sleep(1)
        return ""
    path = record_command(preroll, beep_fn=beep_fn)
    if not path:
        return ""
    text = transcribe(path)
    if text:
        print(f"👤 Ты сказал: {text}")
    return text


def listen_answer(timeout=6.0, beep_fn=None, preroll=None):
    """Короткий ответ без wake-word (для «да/нет»). preroll — начало ответа, если перебили вопрос голосом."""
    if preroll is None:
        MIC.flush()
    path = record_command(preroll, silence_timeout=1.5, no_speech_timeout=timeout, max_record=8.0, beep_fn=beep_fn)
    if not path:
        return ""
    text = transcribe(path, use_prompt=False)
    if text:
        print(f"👤 Ответ: {text}")
    return text
