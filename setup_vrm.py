"""Один раз: скачивает three.js и three-vrm (официальные npm-пакеты через jsDelivr)
в live2d_assets/js/three/ — для 3D-Рокси (vrm.html).

    python setup_vrm.py
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(ROOT, "live2d_assets", "js", "three")
CDN = "https://cdn.jsdelivr.net/npm/"

THREE_VERSIONS = ["0.169.0", "0.168.0", "0.165.0"]
VRM_VERSIONS = ["3.1.0", "3.0.0"]

THREE_FILES = {
    "three.module.min.js": "build/three.module.min.js",
    os.path.join("addons", "loaders", "GLTFLoader.js"): "examples/jsm/loaders/GLTFLoader.js",
    os.path.join("addons", "utils", "BufferGeometryUtils.js"): "examples/jsm/utils/BufferGeometryUtils.js",
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "roxy-setup"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    if len(data) < 1000:
        raise ValueError(f"слишком маленький ответ ({len(data)} байт)")
    return data


def _save(rel, data):
    path = os.path.join(DST, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    print(f"  ✅ {rel} ({len(data) // 1024} КБ)")


def main():
    print(f"Качаю в {DST}")
    for ver in THREE_VERSIONS:
        try:
            files = {rel: _get(f"{CDN}three@{ver}/{src}") for rel, src in THREE_FILES.items()}
        except Exception as e:
            print(f"  three@{ver}: {e}")
            continue
        for rel, data in files.items():
            _save(rel, data)
        print(f"three.js {ver} готов")
        break
    else:
        sys.exit("❌ Не скачался three.js — проверь интернет и запусти ещё раз.")

    for ver in VRM_VERSIONS:
        try:
            data = _get(f"{CDN}@pixiv/three-vrm@{ver}/lib/three-vrm.module.min.js")
        except Exception as e:
            print(f"  three-vrm@{ver}: {e}")
            continue
        _save("three-vrm.module.min.js", data)
        print(f"three-vrm {ver} готов")
        break
    else:
        sys.exit("❌ Не скачался three-vrm — проверь интернет и запусти ещё раз.")

    models = os.path.join(ROOT, "live2d_assets", "models")
    vrms = [f for _, _, fs in os.walk(models) for f in fs if f.lower().endswith(".vrm")]
    if vrms:
        print(f"\n✅ Всё готово. Модель: {vrms[0]}. Запускай main.py — появится 3D-Рокси.")
    else:
        print("\n⚠️ Библиотеки есть, но нет модели: положи roxi.vrm в live2d_assets/models/")
    print('Вернуть Live2D: в config.json "avatar": "live2d"')


if __name__ == "__main__":
    main()
