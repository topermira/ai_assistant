"""Вторая половина install_roxy.bat (её запускает уже новый .venv): пакеты, аниме-голос, Tesseract, ярлык."""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
RVC_SRC = os.path.join(os.path.dirname(ROOT), "rvc_tool")         # рядом с распакованной папкой Roxy
RVC_DST = os.path.join(HOME, "rvc_tool")                          # где его ждёт settings.py
TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def run(cmd, **kw):
    print("   $ " + " ".join(cmd))
    return subprocess.run(cmd, **kw).returncode == 0


def torch_index(lock_text):
    """torch==2.x+cu124 с PyPI не ставится — нужен индекс PyTorch под ту же версию CUDA."""
    m = re.search(r"^torch==[^+\s]+\+(cu\d+|cpu)\s*$", lock_text, re.M)
    return f"https://download.pytorch.org/whl/{m.group(1)}" if m else None


def pip_install(python, lock_path, fallback=None):
    txt = open(lock_path, encoding="utf-8", errors="ignore").read() if os.path.exists(lock_path) else ""
    if txt.strip():
        cmd = [python, "-m", "pip", "install", "-r", lock_path]
        idx = torch_index(txt)
        if idx:
            cmd += ["--extra-index-url", idx]
        if run(cmd):
            return True
        print("⚠️ Точные версии не встали целиком.")
    if fallback and os.path.exists(fallback):
        print("   Ставлю по списку без точных версий…")
        return run([python, "-m", "pip", "install", "-r", fallback])
    return False


def find_py(ver):
    for cmd in (["py", f"-{ver}"], ["python"]):
        try:
            out = subprocess.run(cmd + ["-c", "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode == 0 and (not ver or out.stdout.strip() == ver):
                return cmd
        except Exception:
            pass
    return None


def setup_rvc():
    if not os.path.isdir(RVC_SRC) and not os.path.isdir(RVC_DST):
        print("ℹ️ Аниме-голоса (rvc_tool) в архиве нет — будет быстрый голос Piper.")
        return
    if os.path.isdir(RVC_SRC) and os.path.abspath(RVC_SRC) != os.path.abspath(RVC_DST):
        if os.path.isdir(RVC_DST):
            print(f"ℹ️ {RVC_DST} уже есть — не перезаписываю.")
        else:
            print(f"📁 Копирую аниме-голос в {RVC_DST} …")
            shutil.copytree(RVC_SRC, RVC_DST)
    vpy = os.path.join(RVC_DST, "venv", "Scripts", "python.exe")
    if not os.path.exists(vpy):
        ver = open(os.path.join(RVC_DST, "python_version.txt"), encoding="utf-8").read().strip() \
            if os.path.exists(os.path.join(RVC_DST, "python_version.txt")) else ""
        py = find_py(ver) or find_py("")
        if not py:
            print(f"⚠️ Для аниме-голоса нужен Python {ver or '3.x'} (python.org) — поставь и запусти установщик ещё раз.")
            return
        print("🐍 Создаю окружение для аниме-голоса…")
        if not run(py + ["-m", "venv", os.path.join(RVC_DST, "venv")]):
            return
    run([vpy, "-m", "pip", "install", "--upgrade", "pip"])
    if pip_install(vpy, os.path.join(RVC_DST, "rvc_lock.txt")):
        print("✅ Аниме-голос готов.")
    else:
        print("⚠️ Аниме-голос не встал — Рокси будет говорить быстрым голосом. Пришли этот вывод, разберёмся.")


def setup_tesseract():
    if os.path.exists(TESSERACT):
        return
    print("🔤 Tesseract (чтение текста с экрана) не найден — пробую поставить через winget…")
    if not run(["winget", "install", "-e", "--id", "UB-Mannheim.TesseractOCR", "--accept-source-agreements",
                "--accept-package-agreements"]):
        print("⚠️ Поставь Tesseract вручную: https://github.com/UB-Mannheim/tesseract/wiki (путь по умолчанию).")


def shortcut():
    desk = os.path.join(HOME, "Desktop")
    lnk = os.path.join(desk, "Рокси.lnk")
    ico = os.path.join(ROOT, "live2d_assets", "roxy.ico")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{os.path.join(ROOT, 'roxy_app.bat')}';$s.WorkingDirectory='{ROOT}';"
          + (f"$s.IconLocation='{ico}';" if os.path.exists(ico) else "") + "$s.WindowStyle=7;$s.Save()")
    if run(["powershell", "-NoProfile", "-Command", ps]):
        print(f"🖥 Ярлык «Рокси» на рабочем столе.")


def main():
    py = sys.executable
    print("📦 Пакеты Рокси (несколько минут)…")
    run([py, "-m", "pip", "install", "--upgrade", "pip"])
    if not pip_install(py, os.path.join(ROOT, "req_lock.txt"), os.path.join(ROOT, "requirements.txt")):
        print("❌ Пакеты Рокси не встали — пришли этот вывод.")
        return 1
    setup_rvc()
    setup_tesseract()
    shortcut()
    if not os.path.exists(os.path.join(ROOT, ".env")):
        print("🔑 Нет файла .env с ключами: скопируй его со старого ПК или сделай из .env.example.")
    print("✅ Готово! Запускай ярлык «Рокси» на рабочем столе.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
