"""Веб-камера и микрофон по команде («сними с веб-камеры», «что ты слышишь»).

Приватность: по умолчанию ВЫКЛЮЧЕНО. Включается отдельной командой «разреши камеру и микрофон» (Рокси спросит
«да/нет») и выключается «запрети камеру и микрофон». Состояние хранится в config.json («allow_media»).
Каждый снимок и запись пишутся в консоль; у камеры горит индикатор.

Команды:
  «сними с веб-камеры», «сфоткай камерой», «что видит камера»   → фото с камеры (в Telegram — снимком)
  «что ты слышишь», «послушай», «послушай 15 секунд»              → запись микрофона, распознанный текст
  «запиши звук 10 секунд»                                         → то же + сам звук файлом в Telegram
Нужен пакет opencv-python (для камеры); для микрофона — sounddevice/soundfile, они у Рокси уже есть.
"""
import os
import re
import time

MIC_DEFAULT_SEC = 8
MIC_MAX_SEC = 30
SILENCE_RMS = 0.004          # тише — считаем, что слушать нечего (иначе распознавалка «слышит» выдуманные фразы)

_CAM = r"(?:веб-?\s?камер\w*|вебк\w*|камер\w*)"
_RE_CAM = re.compile(rf"(?:\b(?:сними|снимай|сфоткай|сфотографируй|сфотай|сделай\s+(?:снимок|фото|фотку)|фото\w*|снимок)\b.*{_CAM}"
                     rf"|{_CAM}.*\b(?:сними|сфоткай|сфотографируй|снимок|фото\w*)\b"
                     rf"|\bчто\s+(?:сейчас\s+)?(?:видит|показывает)\s+{_CAM}|\bпокажи\s+(?:мне\s+)?{_CAM})")
_RE_SCREENISH = re.compile(r"экран|скриншот|окно")
_RE_MIC = re.compile(r"^\s*(?:рокси\W+)?(?:что\s+(?:ты\s+)?слышишь|что\s+(?:сейчас\s+)?слышно|послушай(?:\s+(?:микрофон|вокруг|комнату|что\s+происходит))?|"
                     r"прослушай\s+(?:микрофон|комнату|звук)|что\s+происходит\s+вокруг)"
                     r"(?:\s+(?:в\s+течение|в\s+течении|на|за)?\s*(\d{1,3})\s*(?:сек\w*|с))?\s*[?.!]*\s*$")
_RE_REC = re.compile(r"^\s*(?:рокси\W+)?(?:запиши|записывай)\s+(?:звук|микрофон|комнату|что\s+слышно)"
                     r"(?:\s+(?:на|в\s+течение|за)?\s*(\d{1,3})\s*(?:сек\w*|с))?\s*[?.!]*\s*$")
_MEDIA_NOUN = r"(?:веб-?\s?камер\w*|камер\w*|микрофон\w*)"
_RE_GRANT_ON = re.compile(rf"\b(?:разреши|разрешаю|дай|открой|включи)\s+(?:мне\s+)?(?:тебе\s+)?(?:доступ\s+(?:к\s+)?)?{_MEDIA_NOUN}(?:\s+и\s+{_MEDIA_NOUN})?")
_RE_GRANT_OFF = re.compile(rf"\b(?:запрети|запрещаю|закрой|отключи|выключи|убери)\s+(?:доступ\s+(?:к\s+)?)?{_MEDIA_NOUN}(?:\s+и\s+{_MEDIA_NOUN})?")


def parse(lower):
    """→ ('grant', 'on'|'off') | ('cam',) | ('mic', секунды, '0'|'1' — отдать звук файлом) | None."""
    if _RE_GRANT_OFF.search(lower) and re.search(r"доступ|запрети|запрещаю", lower):
        return "grant", "off"
    if _RE_GRANT_ON.search(lower) and re.search(r"разреши|разрешаю|доступ|дай", lower):
        return "grant", "on"
    m = _RE_REC.match(lower)
    if m:
        return "mic", min(MIC_MAX_SEC, max(2, int(m.group(1) or MIC_DEFAULT_SEC))), "1"
    m = _RE_MIC.match(lower)
    if m:
        return "mic", min(MIC_MAX_SEC, max(2, int(m.group(1) or MIC_DEFAULT_SEC))), "0"
    if _RE_CAM.search(lower) and not _RE_SCREENISH.search(lower):
        return ("cam",)
    return None


def allowed(config):
    return bool(config.get("allow_media", False))


# ---------------------------------------------------------------- железо (на Windows с камерой/микрофоном)
def capture_photo(index=0):
    """→ JPEG-байты. Бросает RuntimeError с понятным текстом."""
    try:
        import cv2
    except ImportError:
        raise RuntimeError("нет пакета opencv-python. Поставь: python -m pip install opencv-python")
    cap = None
    for backend in (getattr(cv2, "CAP_DSHOW", 0), 0):
        cap = cv2.VideoCapture(index, backend)
        if cap.isOpened():
            break
        cap.release()
        cap = None
    if cap is None:
        raise RuntimeError("камера не открывается: её нет или она занята другим приложением")
    try:
        for _ in range(10):                      # первые кадры тёмные — пока камера подстроит экспозицию
            cap.read()
            time.sleep(0.05)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("камера открылась, но кадр не получила")
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise RuntimeError("не смогла сжать кадр")
        return buf.tobytes()
    finally:
        cap.release()


def record_mic(seconds, path, samplerate=16000):
    """Пишет wav (16 кГц, моно). → громкость (RMS 0..1)."""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    try:
        data = sd.rec(int(seconds * samplerate), samplerate=samplerate, channels=1, dtype="float32")
        sd.wait()
    except Exception as e:
        raise RuntimeError(f"микрофон недоступен: {e}")
    sf.write(path, data, samplerate)
    return float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
