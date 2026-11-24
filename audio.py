"""Звук: загрузка (всегда моно), воспроизведение с липсинком и прерыванием, синтез речи.

Используется и main.py, и build_library.py. У каждого вызова синтеза свои временные файлы,
поэтому сборка библиотеки больше не ломает речь, если обе программы работают одновременно.
"""
import os
import time
import shutil
import asyncio
import tempfile
import threading
import queue
import subprocess

import numpy as np
from visemes import viseme_weights
import sounddevice as sd
import soundfile as sf
import miniaudio
import edge_tts

from settings import TEMP_DIR, PIPER_MODEL_PATH, RVC_EXE, RVC_VOICE_NAME, RVC_PITCH, config, live_config
from text_tts import preprocess_text_for_tts, split_for_pipeline

# ==================== ПРЕРЫВАНИЕ ====================
_stop = threading.Event()
_speech_lock = threading.RLock()     # чтобы таймер не заговорил поверх ответа
_lipsync_sink = None                 # функция(value 0..1); main.py подставляет Live2D


def set_lipsync_sink(fn):
    global _lipsync_sink
    _lipsync_sink = fn


_viseme_sink = None                  # функция(dict aa/ih/ou/ee/oh) — форма рта по звуку (губы А/И/У/Э/О)


def set_viseme_sink(fn):
    global _viseme_sink
    _viseme_sink = fn


def stop_playback():
    """Перебить Рокси: останавливает текущий звук и все следующие куски."""
    _stop.set()
    try:
        sd.stop()
    except Exception:
        pass


_now = [None]                        # (data, sr, t0) того, что играет сейчас — для перебивания голосом


def is_playing():
    return _now[0] is not None


def playback_rms(window=0.25, lag=0.12):
    """Громкость того, что Рокси говорит прямо сейчас (с задержкой колонки→микрофон). 0, если молчит."""
    cur = _now[0]
    if cur is None:
        return 0.0
    data, sr, t0 = cur
    t = time.monotonic() - t0 - lag
    a, b = int(max(0.0, t - window) * sr), int(max(0.0, t) * sr)
    seg = data[a:b]
    return float(np.sqrt(np.mean(seg.astype(np.float32) ** 2))) if len(seg) else 0.0


def reset_stop():
    _stop.clear()


def is_stopped():
    return _stop.is_set()


# ==================== ЗАГРУЗКА ====================
def to_mono(data):
    data = np.asarray(data, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data


def normalize(data, peak=0.9):
    p = float(np.max(np.abs(data))) if len(data) else 0.0
    return data / p * peak if p > 0 else data


def load_audio(path):
    """→ (float32 моно -1..1, sample_rate). Раньше стерео-mp3 игрался как моно (вдвое медленнее и ниже)."""
    if path.lower().endswith(".mp3"):
        d = miniaudio.decode_file(path, nchannels=1)
        return np.frombuffer(d.samples, dtype=np.int16).astype(np.float32) / 32768.0, d.sample_rate
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        return to_mono(data), sr
    except Exception:
        d = miniaudio.decode_file(path, nchannels=1)
        return np.frombuffer(d.samples, dtype=np.int16).astype(np.float32) / 32768.0, d.sample_rate


# ==================== ВОСПРОИЗВЕДЕНИЕ ====================
def _lip_sync_thread(audio_data, sample_rate, t0, done):
    """Позиция берётся по реальному времени, а не накапливается sleep'ами → рот не отстаёт."""
    sink = _lipsync_sink
    if sink is None:
        return
    half = int(sample_rate * 0.025)
    n = len(audio_data)
    while not done.is_set() and not _stop.is_set():
        idx = int((time.monotonic() - t0) * sample_rate)
        if idx >= n:
            break
        seg = audio_data[max(0, idx - half): idx + half]
        rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
        try:
            sink(min(1.0, (rms - 0.02) * 10.0) if rms > 0.02 else 0.0)
        except Exception:
            pass
        vs = _viseme_sink
        if vs is not None and rms > 0.02:
            try:
                w = viseme_weights(seg, sample_rate)
                if w:
                    vs(w)
            except Exception:
                pass
        time.sleep(0.04)
    try:
        sink(0.0)
    except Exception:
        pass


def voice_volume():
    """Громкость её голоса из лаунчера (0…1.5), применяется сразу."""
    try:
        return max(0.0, min(1.5, float(live_config("voice_volume", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def play(data, sr, lipsync=True, do_normalize=True):
    """Блокирует до конца звука. Возвращает False, если перебили."""
    data = to_mono(data)
    if do_normalize:
        data = normalize(data)
    try:
        import latency
        latency.first_sound()
    except Exception:
        pass
    vol = voice_volume()
    if vol != 1.0:
        data = np.clip(data * vol, -1.0, 1.0).astype(np.float32)
    if _stop.is_set():
        return False
    done = threading.Event()
    sd.play(data, sr)
    t0 = time.monotonic()
    _now[0] = (data, sr, t0)
    if lipsync:
        threading.Thread(target=_lip_sync_thread, args=(data, sr, t0, done), daemon=True).start()
    end = t0 + len(data) / sr
    interrupted = False
    while time.monotonic() < end + 0.1:
        if _stop.is_set():
            interrupted = True
            try: sd.stop()
            except Exception: pass
            break
        time.sleep(0.03)
    if not interrupted:
        try: sd.wait()
        except Exception: pass
    _now[0] = None
    done.set()
    return not interrupted


def play_file(path, lipsync=True):
    data, sr = load_audio(path)
    return play(data, sr, lipsync=lipsync)


def play_random_sound(folder):
    import random
    if not os.path.isdir(folder):
        return False
    files = [f for f in os.listdir(folder) if f.lower().endswith((".wav", ".mp3", ".ogg", ".flac"))]
    if not files:
        return False
    fp = os.path.join(folder, random.choice(files))
    print(f"🔊 {os.path.basename(fp)}")
    try:
        reset_stop()
        play_file(fp, lipsync=False)
        return True
    except Exception as e:
        print(f"❌ {e}")
        return False


def beep(freq=880, dur=0.12, volume=0.25):
    """Короткий сигнал «слушаю»."""
    try:
        sr = 22050
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        tone = np.sin(2 * np.pi * freq * t).astype(np.float32)
        fade = np.minimum(1.0, np.minimum(t, dur - t) / 0.015).astype(np.float32)
        sd.play(tone * fade * volume * min(1.0, voice_volume()), sr)
        sd.wait()
    except Exception:
        pass


# ==================== СИНТЕЗ ====================
_piper_voice = None
_piper_lock = threading.Lock()


def get_piper_voice():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    with _piper_lock:
        if _piper_voice is None:
            from piper.voice import PiperVoice
            _piper_voice = PiperVoice.load(PIPER_MODEL_PATH)
    return _piper_voice


def synth_piper(text, out_wav):
    v = get_piper_voice()
    parts, sr = [], None
    with _piper_lock:
        for c in v.synthesize(text):
            parts.append(c.audio_int16_bytes)
            if sr is None:
                sr = c.sample_rate
    if not parts:
        return False
    sf.write(out_wav, np.frombuffer(b"".join(parts), dtype=np.int16), sr)
    return True


async def _edge_tts_save(text, path):
    c = edge_tts.Communicate(text, "ru-RU-SvetlanaNeural", rate="+50%")
    await c.save(path)


EDGE_TIMEOUT = 6.0


def synth_edge(text, out_mp3):
    """При плохом интернете edge-tts может висеть десятки секунд — ограничиваем, дальше Piper."""
    async def _run():
        await asyncio.wait_for(_edge_tts_save(text, out_mp3), timeout=EDGE_TIMEOUT + len(text) / 60)
    asyncio.run(_run())
    return os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 1000


def rvc_convert(in_wav, out_wav):
    """Сначала фоновый воркер (модели уже в памяти), если он недоступен — rvc-next.exe."""
    if config.get("rvc_worker", True):
        from rvc_client import WORKER
        t0 = time.monotonic()
        if WORKER.convert(os.path.abspath(in_wav), os.path.abspath(out_wav)):
            print(f"🎭 RVC (воркер): {time.monotonic() - t0:.1f} c")
            return True
        if not WORKER.failed and WORKER.alive:
            return False                     # воркер жив, но конкретная фраза не сконвертилась
        print("↩️ RVC-воркер недоступен — использую rvc-next.exe")
    return _rvc_convert_cli(in_wav, out_wav)


def _rvc_convert_cli(in_wav, out_wav):
    """Каждый вызов — свой выходной каталог (раньше общий RVC_OUTPUT_DIR чистился конкурентами)."""
    t0 = time.monotonic()
    out_dir = tempfile.mkdtemp(prefix="rvc_", dir=TEMP_DIR)
    cmd = [RVC_EXE, "convert", in_wav, "--voice", RVC_VOICE_NAME, "--pitch", RVC_PITCH, "-o", out_dir]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        for f in os.listdir(out_dir):
            if f.lower().endswith(".wav"):
                shutil.move(os.path.join(out_dir, f), out_wav)
                print(f"🎭 RVC (exe): {time.monotonic() - t0:.1f} c")
                return True
        print("❌ RVC не создал wav")
        return False
    except subprocess.CalledProcessError as e:
        print(f"❌ RVC: {(e.stderr or '')[-300:]}")
        return False
    except Exception as e:
        print(f"❌ RVC: {e}")
        return False
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def render_to_file(text, mode, out_path, preprocess=True, allow_fallback=True):
    """mode: 'anime' (Piper→RVC, wav) или 'fast' (edge-tts, mp3). Возвращает путь или None."""
    if preprocess:
        text = preprocess_text_for_tts(text)
    if not text:
        return None
    work = tempfile.mkdtemp(prefix="tts_", dir=TEMP_DIR)
    try:
        if mode == "fast":
            try:
                if synth_edge(text, out_path):
                    return out_path
            except Exception as e:
                print(f"❌ edge-tts: {type(e).__name__} {e}")
            if not allow_fallback:
                return None
            print("🗣️ Piper fallback...")
        piper_wav = os.path.join(work, "piper.wav")
        if not synth_piper(text, piper_wav):
            return None
        if mode == "anime":
            if rvc_convert(piper_wav, out_path):
                return out_path
            if not allow_fallback:
                return None
            print("🗣️ RVC упал — говорю голосом Piper")
        fallback = os.path.splitext(out_path)[0] + "_piper.wav"
        shutil.copy(piper_wav, fallback)
        return fallback
    except Exception as e:
        print(f"❌ TTS: {e}")
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def speak(text, mode="anime"):
    """Конвейер: пока играет первое предложение, синтезируется остальное.
    Возвращает False, если перебили."""
    clean = preprocess_text_for_tts(text)
    if not clean:
        return True
    fine = mode == "fast"
    if mode == "anime" and config.get("rvc_worker", True):
        from rvc_client import WORKER
        fine = WORKER.alive and not WORKER.failed
    parts = split_for_pipeline(clean, fine=fine)
    with _speech_lock:
        reset_stop()
        work = tempfile.mkdtemp(prefix="speak_", dir=TEMP_DIR)
        ext = ".mp3" if mode == "fast" else ".wav"
        results = [None] * len(parts)
        threads = [None] * len(parts)

        def synth(i):
            results[i] = render_to_file(parts[i], mode, os.path.join(work, f"part{i}{ext}"), preprocess=False)

        try:
            print(f"🎤 TTS {mode}: {len(parts)} часть(и)")
            synth(0)
            ok = True
            for i in range(len(parts)):
                if i + 1 < len(parts):
                    threads[i + 1] = threading.Thread(target=synth, args=(i + 1,), daemon=True)
                    threads[i + 1].start()
                if threads[i] is not None:
                    threads[i].join()
                if results[i] is None:
                    continue
                if not play_file(results[i]):
                    ok = False
                    break
            return ok
        finally:
            for t in threads:
                if t is not None:
                    t.join(timeout=60)
            shutil.rmtree(work, ignore_errors=True)


def speak_stream(sentences, mode="anime"):
    """Озвучка ответа, который ещё пишется: предложения приходят из генератора по одному.
    Поток-синтезатор готовит следующую часть, пока играет текущая; генератор тем временем дочитывает ответ.
    Возвращает False, если перебили (Num Lock / Pause)."""
    fine = mode == "fast"
    if mode == "anime" and config.get("rvc_worker", True):
        from rvc_client import WORKER
        fine = WORKER.alive and not WORKER.failed
    ext = ".mp3" if mode == "fast" else ".wav"
    with _speech_lock:
        reset_stop()
        work = tempfile.mkdtemp(prefix="speak_", dir=TEMP_DIR)
        ready = queue.Queue(maxsize=4)          # готовые файлы по порядку; None — конец
        n = [0]

        def producer():
            try:
                for sent in sentences:
                    if _stop.is_set():
                        break
                    clean = preprocess_text_for_tts(sent)
                    if not clean:
                        continue
                    for part in split_for_pipeline(clean, fine=fine):
                        if _stop.is_set():
                            break
                        path = render_to_file(part, mode, os.path.join(work, f"part{n[0]}{ext}"), preprocess=False)
                        n[0] += 1
                        if path:
                            ready.put(path)
            except Exception as e:
                print(f"⚠️ speak_stream: {e}")
            finally:
                ready.put(None)

        th = threading.Thread(target=producer, daemon=True, name="tts-stream")
        th.start()
        ok = True
        try:
            print(f"🎤 TTS {mode}: потоком")
            while True:
                path = ready.get()
                if path is None:
                    break
                if ok and not play_file(path):
                    ok = False                   # перебили: дальше не играем, но даём синтезатору закончиться
            return ok
        finally:
            th.join(timeout=60)
            shutil.rmtree(work, ignore_errors=True)
