"""Перебивание голосом: пока Рокси говорит, слушаем микрофон; заговорил человек — она замолкает и слушает.

Главная трудность — эхо: из колонок в микрофон попадает её собственный голос. Поэтому речь человека
засчитывается, только если микрофон громче, чем «должно быть» от её голоса:
    громкость_микрофона > порог_речи + K × связь × громкость_того_что_она_сейчас_говорит,
где «связь» (сколько её голоса доходит до микрофона) измеряется на ходу — по тихим для человека моментам.
В наушниках связь ≈ 0, и перебить можно сразу; с колонками нужно говорить заметно громче её.
Нужно 2 блока подряд (~0,5 с), первые 0,6 с её речи не слушаем (там бип/начало фразы).

Выключить: "barge_in": false в config.json.
"""
import collections
import threading
import time

import numpy as np

import audio
import stt
from settings import config

ENABLED = config.get("barge_in", True)
K_ECHO = 2.5           # во сколько раз голос человека должен перекрывать ожидаемое эхо
NEED_BLOCKS = 2        # подряд блоков по 0,25 с
QUIET_START = 0.6      # с начала её речи не слушаем


class Detector:
    """Чистая логика (без звука), чтобы её можно было проверить тестами."""

    def __init__(self, coupling=None):
        self.ratios = collections.deque(maxlen=60)    # громкость микрофона / громкость её голоса
        self.coupling0 = coupling
        self.hits = 0

    def coupling(self):
        if len(self.ratios) >= 6:
            return float(np.percentile(np.fromiter(self.ratios, dtype=np.float32), 40))
        return self.coupling0 if self.coupling0 is not None else 1.0     # пока не знаем — осторожно

    def feed(self, mic_rms, play_rms, voiced, thr):
        """Один блок. True — человек перебивает."""
        c = self.coupling()
        need = thr * 1.3 + K_ECHO * c * play_rms
        if voiced and mic_rms > need:
            self.hits += 1
        else:
            self.hits = 0
            if play_rms > 0.02:                          # её голос звучит, человек молчит — учимся связи
                self.ratios.append(mic_rms / play_rms)
        return self.hits >= NEED_BLOCKS


_det = Detector()
_pending = None              # блоки микрофона, с которых человек начал говорить (pre-roll для записи)
_lock = threading.Lock()


def pending():
    return _pending is not None


def take_pending():
    """Забрать pre-roll после перебивания (или None)."""
    global _pending
    with _lock:
        p, _pending = _pending, None
    return p


class Monitor:
    """with Monitor(): audio.speak(...) — слушает микрофон, пока внутри блока."""

    def __init__(self):
        self._stop = threading.Event()
        self._th = None

    def __enter__(self):
        if ENABLED:
            self._th = threading.Thread(target=self._run, daemon=True, name="barge-in")
            self._th.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        if self._th:
            self._th.join(timeout=1.0)
        return False

    def _run(self):
        global _pending
        stt.MIC.flush()
        t0 = time.monotonic()
        recent = collections.deque(maxlen=4)            # ~1 с — начало фразы человека
        _det.hits = 0
        while not self._stop.is_set():
            data = stt.MIC.read(timeout=0.3)
            if data is None:
                continue
            recent.append(data)
            if time.monotonic() - t0 < QUIET_START or not audio.is_playing():
                continue
            try:
                hit = _det.feed(stt._rms(data), audio.playback_rms(), stt._is_speech(data), stt.speech_threshold())
            except Exception as e:
                print(f"⚠️ barge-in: {e}")
                return
            if hit:
                print(f"✋ Перебили голосом — замолкаю и слушаю (эхо колонок ≈ {_det.coupling():.2f})")
                with _lock:
                    _pending = list(recent)
                audio.stop_playback()
                return
