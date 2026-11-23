"""Клиент к rvc_worker.py: держит один процесс RVC с загруженными моделями.
Если воркер не стартовал или упал — audio.py сам вернётся к старому вызову rvc-next.exe."""
import os
import json
import time
import queue
import threading
import subprocess

from settings import PROJECT_DIR, TEMP_DIR, LOG_DIR, RVC_PYTHON, RVC_VOICE_NAME, RVC_PITCH

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WORKER_SCRIPT = os.path.join(PROJECT_DIR, "rvc_worker.py")


class RvcWorker:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()          # одна конвертация за раз
        self._ready = threading.Event()
        self._failed = threading.Event()
        self._responses = queue.Queue()
        self._next_id = 1
        self._log = None

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def failed(self):
        return self._failed.is_set()

    def start(self):
        if self._proc is not None:
            return
        if not os.path.exists(RVC_PYTHON) or not os.path.exists(WORKER_SCRIPT):
            print(f"⚠️ RVC-воркер: нет {RVC_PYTHON if not os.path.exists(RVC_PYTHON) else WORKER_SCRIPT}")
            self._failed.set()
            return
        log_path = os.path.join(LOG_DIR, "rvc_worker.log")
        try:                                           # лог не растёт бесконечно: больше 2 МБ — отправляем в .1
            if os.path.exists(log_path) and os.path.getsize(log_path) > 2_000_000:
                os.replace(log_path, log_path + ".1")
        except OSError:
            pass
        self._log = open(log_path, "a", encoding="utf-8", errors="replace")
        print("🔄 Запускаю RVC-воркер (модель голоса грузится один раз)...")
        try:
            self._proc = subprocess.Popen(
                [RVC_PYTHON, "-I", WORKER_SCRIPT, "--voice", RVC_VOICE_NAME, "--pitch", str(RVC_PITCH)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                cwd=TEMP_DIR, creationflags=_NO_WINDOW,
            )
        except Exception as e:
            print(f"⚠️ RVC-воркер не запустился: {e}")
            self._failed.set()
            return
        threading.Thread(target=self._reader, daemon=True, name="rvc-reader").start()

    def _reader(self):
        for line in self._proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "ready" in msg:
                if msg["ready"]:
                    print(f"🔥 RVC-воркер готов за {msg.get('load_s')} c.")
                    self._ready.set()
                else:
                    print(f"❌ RVC-воркер: {msg.get('error')} (подробности в logs/rvc_worker.log)")
                    self._failed.set()
            else:
                self._responses.put(msg)
        if not self._ready.is_set():
            self._failed.set()
        print("🔌 RVC-воркер завершился.")

    def wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready.is_set():
                return True
            if self._failed.is_set() or (self._proc is not None and self._proc.poll() is not None):
                return False
            time.sleep(0.1)
        return self._ready.is_set()

    def convert(self, in_wav, out_wav, timeout=120.0, ready_timeout=90.0):
        """True — готово; False — воркер недоступен/ошибка (тогда можно звать CLI)."""
        if self._proc is None:
            self.start()
        if not self.wait_ready(ready_timeout) or not self.alive:
            return False
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            try:
                self._proc.stdin.write(json.dumps({"id": rid, "in": in_wav, "out": out_wav}, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except Exception as e:
                print(f"⚠️ RVC-воркер: {e}")
                return False
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    msg = self._responses.get(timeout=0.5)
                except queue.Empty:
                    if not self.alive:
                        return False
                    continue
                if msg.get("id") != rid:
                    continue
                if msg.get("ok"):
                    return os.path.exists(out_wav)
                print(f"❌ RVC-воркер: {msg.get('error')}")
                return False
            print("⏱ RVC-воркер не ответил — перезапущу при следующей фразе.")
            self.stop()
            self.__init__()
            return False

    def stop(self):
        p = self._proc
        if p is None:
            return
        try:
            p.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
            p.stdin.flush()
            p.wait(timeout=5)
        except Exception:
            try: p.kill()
            except Exception: pass
        try:
            if self._log: self._log.close()
        except Exception:
            pass


WORKER = RvcWorker()
