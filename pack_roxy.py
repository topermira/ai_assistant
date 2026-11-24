"""Собрать Рокси для переноса на ДРУГОЙ СВОЙ компьютер.

Запуск на этом компьютере (из папки проекта):
    .venv\\Scripts\\python.exe pack_roxy.py

На рабочем столе появится Roxy_transfer_<дата>.zip. Перенеси его на второй ПК, распакуй куда угодно
(лучше в путь без русских букв, например C:\\Roxy) и запусти install_roxy.bat — он сам поставит всё нужное.

Что попадает в архив:
  * весь проект, модели (VRM, Whisper, Vosk, Piper), звуки, библиотека, твои настройки (config.json, memory.json);
  * аниме-голос: папка %USERPROFILE%\\rvc_tool без её venv (venv на другом ПК собирается заново);
  * точные списки пакетов этого компьютера (req_lock.txt, rvc_lock.txt) — чтобы там стали те же версии.
Что НЕ попадает (секреты): .env с ключами и сессия Telegram (*.session) — их перенеси сам, если нужно,
или впиши ключи заново по .env.example.

Архив только для СВОИХ компьютеров: модель с Booth и голос нельзя раздавать другим людям.
"""
import os
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
RVC = os.path.join(os.path.expanduser("~"), "rvc_tool")
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", ".pytest_cache", ".idea", "temp", "logs", "_to_delete",
             "Claude outputs", "node_modules"}
SKIP_FILES = {".env"}
SKIP_EXT = {".session", ".session-journal", ".pyc", ".log"}
STORE_EXT = {".bin", ".onnx", ".pth", ".pt", ".index", ".vrm", ".safetensors", ".zip", ".png", ".jpg", ".wav",
             ".mp3", ".ogg", ".fst", ".mdl", ".exe", ".dll", ".ckpt"}      # уже сжатое/большое — без сжатия, быстрее


def freeze(python):
    try:
        out = subprocess.run([python, "-m", "pip", "freeze", "--exclude-editable"], capture_output=True,
                             text=True, timeout=300)
        return out.stdout if out.returncode == 0 else ""
    except Exception as e:
        print(f"⚠️ pip freeze ({python}): {e}")
        return ""


def add_tree(z, src, arc_root, stats):
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES or os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            arc = os.path.join(arc_root, os.path.relpath(full, src))
            ext = os.path.splitext(fn)[1].lower()
            try:
                z.write(full, arc, compress_type=zipfile.ZIP_STORED if ext in STORE_EXT else zipfile.ZIP_DEFLATED)
                stats[0] += 1
                stats[1] += os.path.getsize(full)
            except OSError as e:
                print(f"⚠️ пропускаю {full}: {e}")
            if stats[0] % 500 == 0:
                print(f"   … {stats[0]} файлов, {stats[1] / 1e9:.1f} ГБ")


def main():
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desk):
        desk = ROOT
    out = os.path.join(desk, time.strftime("Roxy_transfer_%Y%m%d_%H%M.zip"))
    print("📦 Списки пакетов…")
    lock = freeze(sys.executable)
    rvc_py = os.path.join(RVC, "venv", "Scripts", "python.exe")
    rvc_lock = freeze(rvc_py) if os.path.exists(rvc_py) else ""
    stats = [0, 0]
    print(f"📦 Собираю {out} (это займёт несколько минут — там модели на гигабайты)")
    with zipfile.ZipFile(out, "w", allowZip64=True) as z:
        add_tree(z, ROOT, "Roxy", stats)
        z.writestr("Roxy/req_lock.txt", lock or "")
        z.writestr("Roxy/python_version.txt", f"{sys.version_info.major}.{sys.version_info.minor}\n")
        if os.path.isdir(RVC):
            add_tree(z, RVC, "rvc_tool", stats)
            z.writestr("rvc_tool/rvc_lock.txt", rvc_lock or "")
            rvc_ver = subprocess.run([rvc_py, "-c", "import sys;print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                                     capture_output=True, text=True).stdout.strip() if os.path.exists(rvc_py) else ""
            z.writestr("rvc_tool/python_version.txt", (rvc_ver or "") + "\n")
        else:
            print("⚠️ Папки rvc_tool нет — аниме-голоса в архиве не будет (быстрый голос Piper работает и так).")
    print(f"✅ Готово: {stats[0]} файлов, {stats[1] / 1e9:.1f} ГБ → {out}")
    print("   Перенеси на второй ПК, распакуй и запусти Roxy\\install_roxy.bat")
    if not lock:
        print("⚠️ Не получилось снять список пакетов — на втором ПК поставлю по requirements.txt")


if __name__ == "__main__":
    main()
