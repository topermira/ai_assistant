import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from music import BeatTracker, RATE


def track(bpm, secs=8.0, noise=0.03, seed=0, offset=0.13):
    rng = np.random.default_rng(seed)
    tr = BeatTracker()
    period = 60.0 / bpm
    lvl = 0.0
    for i in range(int(secs * RATE)):
        t = i / RATE
        ph = ((t - offset) / period) % 1.0
        hit = 0.9 if ph < 1.0 / (period * RATE) else 0.0
        lvl = max(hit, lvl * 0.88) if hit else lvl * 0.88
        tr.feed(t, 0.15 + lvl * 0.6 + abs(rng.normal(0, noise)))
    return tr, t


def test_finds_tempo():
    for bpm in (90, 110, 128, 150, 174):
        tr, t = track(bpm)
        st = tr.analyze(t)
        assert st["on"], (bpm, st)
        # 174 ↔ 87 — та же музыка (танец через долю), ошибка на октаву допустима
        assert min(abs(st["bpm"] - bpm * k) for k in (0.5, 1, 2)) < 3, (bpm, st)


def test_finds_phase():
    tr, t = track(120, offset=0.13)
    st = tr.analyze(t)
    true_ph = ((t - 0.13) * 2.0) % 1.0
    d = abs(st["phase"] - true_ph)
    assert min(d, 1 - d) < 0.12, (st, true_ph)


def test_silence_and_noise_are_not_music():
    tr = BeatTracker()
    for i in range(800):
        tr.feed(i / RATE, 0.0)
    assert not tr.analyze()["on"]
    rng = np.random.default_rng(1)
    tr = BeatTracker()
    lvl = 0
    for i in range(800):                       # речь: всплески в случайные моменты
        if rng.random() < 0.04:
            lvl = rng.uniform(0.3, 0.9)
        lvl *= 0.9
        tr.feed(i / RATE, 0.05 + lvl)
    st = tr.analyze()
    assert not st["on"] or st["conf"] < 0.4
