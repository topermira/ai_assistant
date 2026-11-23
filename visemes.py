"""Форма рта по звуку: грубая оценка гласной по первым двум формантам (пикам огибающей спектра).

Гласные различаются положением формант F1 и F2 (ориентиры для женского голоса):
    А ~ (850, 1400) Гц    О ~ (550, 950)    У ~ (370, 800)    Э ~ (550, 2000)    И ~ (330, 2600)
Точного распознавания не нужно: модель получает плавную смесь пяти форм рта, и этого хватает, чтобы губы
перестали открываться одним и тем же «А». Только numpy — функции легко тестировать.
"""
import numpy as np

VOWELS = ("aa", "ih", "ou", "ee", "oh")          # имена выражений VRM: А, И, У, Э, О
_PROTO = {"aa": (850, 1400), "ih": (330, 2600), "ou": (370, 800), "ee": (550, 2000), "oh": (550, 950)}


def formants(seg, sr):
    """(F1, F2) в Гц по сглаженной огибающей спектра или None для тишины/шума."""
    seg = np.asarray(seg, dtype=np.float64)
    if len(seg) < 128 or np.sqrt(np.mean(seg ** 2)) < 1e-4:
        return None
    seg = np.append(seg[0], seg[1:] - 0.7 * seg[:-1])          # лёгкое предыскажение: поднимаем верх
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=4096))
    freqs = np.fft.rfftfreq(4096, 1.0 / sr)
    df = freqs[1] - freqs[0]
    k = max(1, int(round(220 / df)))                            # сглаживание ~220 Гц: гармоники → огибающая
    env = np.convolve(np.log(spec + 1e-9), np.ones(k) / k, mode="same")

    m1 = (freqs >= 250) & (freqs <= 1000)
    f1 = float(freqs[m1][np.argmax(env[m1])])
    # F2 — ПЕРВЫЙ заметный пик выше F1 (не самый высокий: у А/О/У он близко к F1, а дальше стоит F3)
    m = (freqs >= max(650.0, f1 + 250.0)) & (freqs <= 3200)
    if not m.any():
        return None
    ef, ff = env[m], freqs[m]
    top = ef.max()
    for i in range(1, len(ef) - 1):
        if ef[i] >= ef[i - 1] and ef[i] >= ef[i + 1] and ef[i] >= top - 1.2:
            return (f1, float(ff[i]))
    return (f1, float(ff[np.argmax(ef)]))


def viseme_weights(seg, sr, sharp=6.0):
    """→ dict {aa, ih, ou, ee, oh} с суммой 1 (без учёта громкости) или None для тишины."""
    f = formants(seg, sr)
    if f is None:
        return None
    l1, l2 = np.log(f[0]), np.log(f[1])
    d = np.array([(l1 - np.log(p[0])) ** 2 * 1.4 + (l2 - np.log(p[1])) ** 2 for p in (_PROTO[v] for v in VOWELS)])
    z = np.exp(-sharp * d / max(1e-6, d.min() + 0.02))
    w = z / z.sum()
    return {v: float(x) for v, x in zip(VOWELS, w)}
