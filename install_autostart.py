"""Автозапуск Рокси вместе с Windows (ярлык в папке «Автозагрузка» текущего пользователя).

    python install_autostart.py           # добавить
    python install_autostart.py --remove  # убрать

Ярлык запускает start_roxy.bat из папки проекта. Ничего, кроме этого ярлыка, не создаётся и не меняется.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(ROOT, "start_roxy.bat")
STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
LNK = os.path.join(STARTUP, "Roxy.lnk")


def _ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def add():
    if os.name != "nt":
        print("Автозапуск настраивается только в Windows.")
        return 1
    if not os.path.exists(BAT):
        print(f"Нет файла {BAT}")
        return 1
    os.makedirs(STARTUP, exist_ok=True)
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(LNK)});"
          f"$s.TargetPath={_ps_quote(BAT)};$s.WorkingDirectory={_ps_quote(ROOT)};$s.WindowStyle=7;"
          f"$s.Description='Roxy';$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(LNK):
        print("Не получилось создать ярлык:", (r.stderr or r.stdout).strip())
        return 1
    print(f"✅ Рокси будет запускаться вместе с Windows: {LNK}")
    return 0


def remove():
    if os.path.exists(LNK):
        os.remove(LNK)
        print("✅ Убрала Рокси из автозапуска.")
    else:
        print("Ярлыка автозапуска нет — убирать нечего.")
    return 0


if __name__ == "__main__":
    sys.exit(remove() if "--remove" in sys.argv else add())
