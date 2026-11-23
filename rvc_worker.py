"""Фоновый процесс RVC. Запускается Python'ом из venv rvc_tool (НЕ из venv Рокси):

    <папка rvc_tool>\\venv\\Scripts\\python.exe -I rvc_worker.py --voice roxy_e660_s4620 --pitch 8

Зачем: `rvc-next.exe convert` на каждую фразу заново импортирует torch и грузит HuBERT, RMVPE
и модель голоса. Здесь всё грузится один раз (Runtime в rvc-next кэширует модели),
дальше каждая фраза — только сама конвертация.

Протокол — JSON-строки:
  stdin : {"id": 1, "in": "C:/.../piper.wav", "out": "C:/.../out.wav"}
  stdout: {"ready": true, "load_s": 12.3}            — один раз после загрузки
          {"id": 1, "ok": true, "secs": 0.8}          — на каждый запрос
          {"id": 1, "ok": false, "error": "..."}
Всё остальное (логи rvc-next, torch) уходит в stderr, чтобы не ломать протокол.

Работает тем же кодом, что и команда `rvc-next convert` (rvc-next 0.2.16).
"""
import os
import sys

# Протокол — на копии stdout; сам fd 1 перенаправляем в stderr, чтобы print() из библиотек
# (и из C-кода) не попадал в протокол.
_proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr

# Не даём папке Рокси (audio.py, settings.py, ...) перекрыть модули из venv rvc_tool
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _here]

import json      # noqa: E402
import time      # noqa: E402
import shutil    # noqa: E402
import argparse  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402


def _no_throttling():
    """Windows 11 считает процесс без окна «фоновым» (EcoQoS): режет частоту и на гибридных
    процессорах гонит его на энергоэффективные ядра. Отключаем это и чуть поднимаем приоритет."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class PPTI(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        # Типы аргументов обязательны: без них ctypes передавал псевдо-дескриптор процесса (-1) как 32-битное int,
        # на 64-битной Windows это НЕ дескриптор, и вызов тихо возвращал False («power throttling off: False» в логах).
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetProcessInformation.restype = wintypes.BOOL
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        h = k32.GetCurrentProcess()
        info = PPTI(1, 0x1, 0x0)   # EXECUTION_SPEED: управляем мы, троттлинг выключен
        ok = k32.SetProcessInformation(h, 4, ctypes.byref(info), ctypes.sizeof(info))  # ProcessPowerThrottling
        err = ctypes.get_last_error() if not ok else 0
        ok2 = k32.SetPriorityClass(h, 0x00008000)   # ABOVE_NORMAL_PRIORITY_CLASS
        print(f"power throttling off: {bool(ok)}" + (f" (код ошибки {err})" if err else "")
              + f", приоритет выше обычного: {bool(ok2)}", file=sys.stderr)
    except Exception as e:
        print(f"power throttling: {e}", file=sys.stderr)


def send(obj):
    _proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _proto.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", required=True)
    ap.add_argument("--pitch", type=float, default=0.0)
    ap.add_argument("--no-warmup", action="store_true")
    args = ap.parse_args()

    _no_throttling()
    t0 = time.monotonic()
    try:
        from rvc_next.cli.params import build_params
        from rvc_next.core.audio.models import AudioRef
        from rvc_next.core.context import build_services
        from rvc_next.core.conversion.models import ConvertRequest

        services = build_services()
        voice = services.models.resolve(args.voice)
        params = build_params(services.presets.default_for(voice.id).params, pitch=args.pitch)
    except Exception as e:
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1

    work = Path(tempfile.mkdtemp(prefix="rvc_worker_"))

    def convert(src, dst):
        out_dir = work / f"job_{time.monotonic_ns()}"
        out_dir.mkdir()
        try:
            req = ConvertRequest(
                inputs=[AudioRef(kind="path", path=str(Path(src).resolve()))],
                voice_id=voice.id, params=params, output_format="wav",
                output_dir=str(out_dir), overwrite=True,
            )
            job = services.conversion.convert(req, foreground=True)
            outputs = (job.result or {}).get("outputs", [])
            if not outputs:
                raise RuntimeError(f"нет результата (job status: {getattr(job, 'status', '?')})")
            shutil.move(outputs[0], dst)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    # Прогрев: первая конвертация грузит HuBERT, RMVPE и голос
    if not args.no_warmup:
        try:
            import numpy as np
            import soundfile as sf
            sr = 16000
            t = np.arange(int(sr * 1.0)) / sr
            tone = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
            src = work / "warmup.wav"
            sf.write(str(src), tone, sr)
            convert(src, work / "warmup_out.wav")
        except Exception as e:
            print(f"warmup failed: {e}", file=sys.stderr)

    send({"ready": True, "load_s": round(time.monotonic() - t0, 2), "voice": voice.id})
    print(f"ready in {time.monotonic() - t0:.1f}s", file=sys.stderr)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            if req.get("cmd") == "quit":
                break
            rid = req.get("id")
            t1 = time.monotonic()
            try:
                convert(req["in"], req["out"])
                secs = round(time.monotonic() - t1, 2)
                send({"id": rid, "ok": True, "secs": secs})
                print(f"converted {os.path.basename(req['in'])} in {secs}s", file=sys.stderr)
            except Exception as e:
                send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})
    finally:
        try:
            services.close()
        except Exception:
            pass
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.exit(main())
