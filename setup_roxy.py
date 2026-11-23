"""Одноразовая настройка после обновления. Запуск из папки проекта:  python setup_roxy.py

1. Переносит старые и ненужные файлы в _to_delete/ (ничего не удаляет — проверь и удали сам).
2. Делает текстуру модели 4096×4096 вместо 8192×8192 (в 4 раза меньше видеопамяти,
   быстрее загрузка окна). Оригинал 8192 остаётся на месте; вернуть — запусти с --texture-8192.
3. Проверяет, что установлены нужные пакеты.
"""
import os
import sys
import json
import shutil
import argparse
import importlib.util

ROOT = os.path.dirname(os.path.abspath(__file__))
TRASH = os.path.join(ROOT, "_to_delete")
MODEL_DIR = os.path.join(ROOT, "live2d_assets", "models", "roxy")
MODEL_JSON = os.path.join(MODEL_DIR, "Roxy_V1.model3.json")
TEX_8192 = "Roxy_V1.8192/texture_00.png"
TEX_4096 = "Roxy_V1.4096/texture_00.png"

JUNK = [
    # старые скрипты (их функции есть в новой версии)
    "setup_live2d.py",        # ОПАСЕН: удаляет live2d_assets целиком и качает Hiyori
    "telegram_agent.py", "telegram_module.py", "vision_agent.py",
    "test_brains.py", "test_ears.py", "test_voice.py",
    # временные звуки
    "test_voice.wav", "input_mic.wav", "temp_piper.wav",
    # папки
    "whisper_base_model",     # использовался только test_ears.py
    "rvc_output",             # новая версия его не использует
    "final_speech.wav", "klara_voice.wav",   # папки с остатками тестов RVC
    os.path.join("live2d_assets", "models", "hiyori"),   # тестовая модель из setup_live2d.py
    "__pycache__",
]


def _unique(dst):
    base, n = dst, 1
    while os.path.exists(dst):
        dst = f"{base}_{n}"
        n += 1
    return dst


def move_junk():
    moved = 0
    for rel in JUNK:
        src = os.path.join(ROOT, rel)
        if os.path.exists(src):
            dst = _unique(os.path.join(TRASH, rel))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            print(f"  → _to_delete/{rel}")
            moved += 1
    temp = os.path.join(ROOT, "temp")
    if os.path.isdir(temp):
        for f in os.listdir(temp):
            full = os.path.join(temp, f)
            if os.path.isfile(full):
                dst = _unique(os.path.join(TRASH, "temp", f))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(full, dst)
                moved += 1
    print(f"Перенесено: {moved}. Проверь _to_delete и удали папку целиком, когда убедишься, что всё работает.")


def set_texture(rel):
    with open(MODEL_JSON, encoding="utf-8") as f:
        data = json.load(f)
    data["FileReferences"]["Textures"] = [rel]
    with open(MODEL_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent="\t")
    print(f"model3.json → {rel}")


def make_texture_4096():
    from PIL import Image
    src = os.path.join(MODEL_DIR, TEX_8192)
    dst = os.path.join(MODEL_DIR, TEX_4096)
    if not os.path.exists(src):
        print(f"⚠️ Нет {src}")
        return
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        print("Уменьшаю текстуру 8192 → 4096 (полминуты)...")
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(src) as im:
            im = im.convert("RGBA")
            w, h = im.size
            im.resize((w // 2, h // 2), Image.LANCZOS).save(dst, optimize=True)
    set_texture(TEX_4096)


def check_packages():
    need = {"send2trash": "send2trash", "psutil": "psutil", "webrtcvad": "webrtcvad-wheels", "pytest": "pytest"}
    missing = [pip for mod, pip in need.items() if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"Установи: {sys.executable} -m pip install {' '.join(missing)}")
        if "send2trash" in missing:
            print("   (без send2trash Рокси НЕ будет удалять файлы)")
    else:
        print("Все пакеты на месте.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--texture-8192", action="store_true", help="вернуть исходную текстуру")
    ap.add_argument("--skip-junk", action="store_true")
    a = ap.parse_args()
    if a.texture_8192:
        set_texture(TEX_8192)
        sys.exit(0)
    print("1) Уборка"); a.skip_junk or move_junk()
    print("\n2) Текстура"); make_texture_4096()
    print("\n3) Пакеты"); check_packages()
    print("\nДальше:\n  python -m pytest tests\n  python build_library.py --all\n  python main.py")
