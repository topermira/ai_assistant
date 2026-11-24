"""Слушает, что играет в системе, и находит такт — чтобы Рокси танцевала в ритм.

Звук не записывается: берём только пиковый уровень выхода Windows (IAudioMeterInformation — тот же индикатор,
что в микшере громкости), ~100 раз в секунду. Из него — сила «ударов» (рост громкости), из её автокорреляции —
темп (70–180 ударов в минуту), из «гребёнки» по ударам — где сейчас доля.

BeatTracker — чистая логика (её проверяют тесты), Meter — Windows-часть, MusicWatcher — поток, который
каждые полсекунды отдаёт состояние: {"on": играет ли музыка, "bpm", "phase" (0..1 внутри доли), "energy"}.
"""
import collections
import threading
import time

import numpy as np

RATE = 100.0                     # опросов в секунду
WIN = 6.0                        # окно анализа, с
BPM_MIN, BPM_MAX = 70.0, 180.0


class BeatTracker:
    def __init__(self, rate=RATE):
        self.rate = rate
        n = int(WIN * rate)
        self.env = collections.deque(maxlen=n)
        self.t = collections.deque(maxlen=n)
        self.bpm = 0.0
        self.conf = 0.0
        self.beat_t = None           # время последней найденной доли
        self.on_since = None

    def feed(self, t, level):
        self.t.append(t)
        self.env.append(float(level))

    def analyze(self, now=None):
        """Пересчитать темп и долю. Возвращает dict состояния."""
        if len(self.env) < self.rate * 3:
            return self._state(now, False)
        e = np.asarray(self.env, dtype=np.float64)
        ts = np.asarray(self.t, dtype=np.float64)
        now = ts[-1] if now is None else now
        loud = float(np.sqrt(np.mean(e[-int(self.rate * 3):] ** 2)))
        # сила удара: рост логарифма громкости относительно недавнего фона
        le = np.log(e + 1e-4)
        k = 4
        base = np.convolve(le, np.ones(k) / k, mode="full")[:len(le)]
        o = np.zeros_like(le)
        o[1:] = np.maximum(0.0, le[1:] - base[:-1])
        o -= o.mean()
        if not np.any(o > 0):
            return self._state(now, False)
        # темп: автокорреляция силы ударов по запаздываниям 70–180 уд/мин, с мягким предпочтением ~120
        lo, hi = int(self.rate * 60 / BPM_MAX), int(self.rate * 60 / BPM_MIN)
        ac = np.array([np.dot(o[:-L], o[L:]) / (len(o) - L) for L in range(lo, hi + 1)])
        lags = np.arange(lo, hi + 1)
        bpms = 60 * self.rate / lags
        weight = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.7) ** 2)
        score = ac * weight
        i = int(np.argmax(score))
        if 0 < i < len(score) - 1:                              # параболическая доводка запаздывания
            a, b, c = score[i - 1], score[i], score[i + 1]
            d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        else:
            d = 0.0
        lag = lags[i] + max(-0.5, min(0.5, d))
        conf = float(ac[i] / (np.dot(o, o) / len(o) + 1e-12))
        bpm = 60 * self.rate / lag
        # доля: фаза гребёнки с шагом lag, дающая наибольшую сумму ударов
        P = lag
        n = len(o)
        best, best_ph = -1e9, 0.0
        for ph in np.arange(0, P, 1.0):
            idx = np.arange(n - 1 - ph, -1, -P).astype(int)
            s = o[idx].sum()
            if s > best:
                best, best_ph = s, ph
        beat_t = ts[-1] - best_ph / self.rate
        playing = loud > 0.02 and conf > 0.25
        if playing:
            if self.bpm and abs(bpm - self.bpm) / self.bpm < 0.04:
                bpm = 0.7 * self.bpm + 0.3 * bpm                # сглаживаем мелкие колебания
            self.bpm, self.conf, self.beat_t = bpm, conf, beat_t
            if self.on_since is None:
                self.on_since = now
        else:
            self.on_since = None
            self.conf = conf
        return self._state(now, playing, loud)

    def _state(self, now, playing, loud=0.0):
        ph = 0.0
        if playing and self.beat_t is not None and self.bpm > 0:
            ph = ((now - self.beat_t) * self.bpm / 60.0) % 1.0
        return {"on": bool(playing), "bpm": round(float(self.bpm), 1) if playing else 0.0, "phase": round(float(ph), 3),
                "energy": round(float(min(1.0, loud * 3)), 3), "conf": round(float(self.conf), 2),
                "for": round(float(now - self.on_since), 1) if playing and self.on_since is not None else 0.0}


class Meter:
    """Пиковый уровень выхода Windows по умолчанию (0..1) или None, если недоступно."""

    def __init__(self):
        self.m = None
        try:
            import ctypes
            import comtypes
            from ctypes import HRESULT, POINTER, c_float
            from comtypes import COMMETHOD, GUID, IUnknown
            import volume

            class IAudioMeterInformation(IUnknown):
                _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
                _methods_ = [COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "pfPeak"))]

            try:
                comtypes.CoInitialize()
            except OSError:
                pass
            enum = comtypes.CoCreateInstance(volume._CLSID_ENUM, volume.IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
            dev = enum.GetDefaultAudioEndpoint(0, 1)
            unk = dev.Activate(ctypes.byref(IAudioMeterInformation._iid_), comtypes.CLSCTX_ALL, None)
            self.m = unk.QueryInterface(IAudioMeterInformation)
        except Exception as e:
            print(f"🎵 уровень звука системы недоступен — танца под музыку не будет ({e})", flush=True)

    def level(self):
        if self.m is None:
            return None
        try:
            return float(self.m.GetPeakValue())
        except Exception:
            return None


class MusicWatcher(threading.Thread):
    """Поток: опрашивает уровень и раз в 0.5 с вызывает on_state(dict)."""

    def __init__(self, on_state):
        super().__init__(daemon=True, name="music")
        self.on_state = on_state
        self.stop_ev = threading.Event()

    def run(self):
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)               # точнее sleep(0.01)
        except Exception:
            pass
        meter = Meter()
        if meter.m is None:
            return
        tr = BeatTracker()
        next_an = time.monotonic() + 0.5
        fails = 0
        while not self.stop_ev.is_set():
            lv = meter.level()
            now = time.monotonic()
            if lv is None:
                fails += 1
                if fails > 200:                                 # сменили устройство вывода — переподключиться
                    meter = Meter(); fails = 0
                    if meter.m is None:
                        time.sleep(5)
            else:
                fails = 0
                tr.feed(now, lv)
            if now >= next_an:
                next_an = now + 0.5
                try:
                    self.on_state(tr.analyze(now))
                except Exception as e:
                    print(f"🎵 music: {e}", flush=True)
            time.sleep(1.0 / RATE)
