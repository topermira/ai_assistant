"""WebSocket-клиент к live2d_window.py. Переподключается сам, пока программа работает
(раньше была одна попытка через 3 секунды — если Qt грузился дольше, липсинк молча не работал)."""
import json
import time
import threading

import websocket  # websocket-client

from settings import LIVE2D_URL


class Live2DClient:
    def __init__(self, url=LIVE2D_URL, retry_every=2.0):
        self.url = url
        self.retry_every = retry_every
        self._ws = None
        self._lock = threading.Lock()
        self._running = False
        self._was_connected = False
        self._waiters = {}            # имя поведения → {"ev": Event, "res": dict} — ждём ответ окна («села» / «не на что»)

    @property
    def connected(self):
        return self._ws is not None

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="live2d-ws").start()

    def stop(self):
        self._running = False
        with self._lock:
            if self._ws is not None:
                try: self._ws.close()
                except Exception: pass
            self._ws = None

    def _loop(self):
        while self._running:
            if self._ws is None:
                try:
                    c = websocket.WebSocket()
                    # явный Origin: окно Live2D пускает только клиентов без Origin или с этим (чужие сайты — нет)
                    c.connect(self.url, timeout=2, origin=self.url.replace("ws://", "http://", 1))
                    with self._lock:
                        self._ws = c
                    print("✅ Подключено к Live2D окну")
                    self._was_connected = True
                    threading.Thread(target=self._reader, args=(c,), daemon=True, name="live2d-recv").start()
                except Exception:
                    pass
            time.sleep(self.retry_every)

    on_event = None      # функция(dict): события из окна аватара (например, «открыть ввод» из трея)

    def _reader(self, c):
        # Таймаут 2 с из connect() остаётся на сокете: recv() без событий кидал таймаут, и поток приёма молча
        # завершался — написанное в поле над головой Рокси больше не доходило до main. Таймаут — не обрыв.
        while self._running and self._ws is c:
            try:
                msg = c.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not msg:
                continue
            try:
                data = json.loads(msg)
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("type") == "behavior_result":
                w = self._waiters.pop(data.get("name"), None)
                if w:
                    w["res"] = data
                    w["ev"].set()
                continue
            cb = self.on_event
            if cb and isinstance(data, dict):
                try:
                    cb(data)
                except Exception as e:
                    print(f"⚠️ событие окна: {e}")

    def send(self, cmd_type, **kwargs):
        ws = self._ws
        if ws is None:
            return False
        payload = json.dumps({"type": cmd_type, **kwargs})
        with self._lock:
            try:
                ws.send(payload)
                return True
            except Exception:
                if self._ws is ws:
                    self._ws = None
                    if self._was_connected:
                        print("🔌 Live2D отключился, буду переподключаться.")
                return False

    def behavior_result(self, name, timeout=2.5):
        """Послать поведение и дождаться ответа окна: {'ok': bool, 'reason': str} или None, если окно не ответило
        (старая версия окна, оно занято, нет связи). Нужен для честного «села» / «не получилось»."""
        w = {"ev": threading.Event(), "res": None}
        self._waiters[name] = w
        if not self.send("behavior", name=name):
            self._waiters.pop(name, None)
            return None
        if not w["ev"].wait(timeout):
            self._waiters.pop(name, None)
            return None
        return w["res"]

    # удобные обёртки
    def mouth(self, value):
        return self.send("mouth", value=round(float(value), 3))

    def viseme(self, weights):
        return self.send("viseme", **{k: round(float(weights.get(k, 0.0)), 2) for k in ("aa", "ih", "ou", "ee", "oh")})

    def emotion(self, name, duration_ms=6000):
        return self.send("emotion", name=name, duration=int(duration_ms))


LIVE2D = Live2DClient()
