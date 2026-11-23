"""Сборка аудио-библиотеки.

Изменения:
* id из транслита (раньше все русские названия превращались в 'work' и перетирали друг друга);
* в имени файла хэш текста чанка — если поправить текст, изменённые куски перегенерируются;
* старые файлы произведения, которых больше нет в списке, удаляются;
* не импортирует main.py и использует свои временные файлы (можно запускать параллельно с Рокси).
"""
import os
import sys
import json
import shutil
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from settings import LIBRARY_DIR, LIBRARY_TEXTS_DIR, LIBRARY_AUDIO_DIR, LIBRARY_INDEX
from text_tts import split_into_chunks, preprocess_text_for_tts
from library import make_work_id, chunk_hash
import audio


def build_work(text_path, title, author, aliases, mode="anime"):
    with open(text_path, encoding="utf-8") as f:
        text = f.read()
    work_id = make_work_id(title)
    chunks = split_into_chunks(text)
    print(f"📖 '{title}' (id={work_id}): {len(chunks)} чанков, {len(text)} символов")

    os.makedirs(LIBRARY_AUDIO_DIR, exist_ok=True)
    ext = ".wav" if mode == "anime" else ".mp3"
    audio_files, keep = [], set()

    for i, chunk in enumerate(chunks, 1):
        prepared = preprocess_text_for_tts(chunk)
        filename = f"{work_id}_{i:03d}_{chunk_hash(prepared, mode)}{ext}"
        out_path = os.path.join(LIBRARY_AUDIO_DIR, filename)
        rel_path = os.path.relpath(out_path, LIBRARY_DIR).replace("\\", "/")
        keep.add(filename)

        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            print(f"   ✓ {i}/{len(chunks)} (уже есть)")
            audio_files.append(rel_path)
            continue

        print(f"   🎤 {i}/{len(chunks)}: {chunk[:60]}...")
        try:
            res = audio.render_to_file(prepared, mode, out_path, preprocess=False, allow_fallback=False)
            if res:
                audio_files.append(rel_path)
            else:
                print(f"   ❌ Чанк {i} не сгенерирован")
        except Exception as e:
            print(f"   ❌ Ошибка на чанке {i}: {e}")

    # чистим устаревшие куски этого произведения
    for f in os.listdir(LIBRARY_AUDIO_DIR):
        if f.startswith(work_id + "_") and f not in keep:
            try:
                os.remove(os.path.join(LIBRARY_AUDIO_DIR, f))
                print(f"   🗑 устаревший {f}")
            except OSError:
                pass

    return {
        "id": work_id,
        "title": title,
        "author": author,
        "aliases": aliases or [title.lower()],
        "audio_files": audio_files,
        "text_file": os.path.relpath(text_path, LIBRARY_DIR).replace("\\", "/"),
        "language": "ru",
    }


def load_index():
    if os.path.exists(LIBRARY_INDEX):
        try:
            with open(LIBRARY_INDEX, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"works": []}


def _drop_legacy(index):
    """Записи с id 'work' — следствие старого бага с кириллицей. Их аудио переносим в _to_delete."""
    keep = []
    trash = os.path.join(os.path.dirname(LIBRARY_DIR), "_to_delete", "library_audio_old")
    for w in index.get("works", []):
        if w.get("id") != "work":
            keep.append(w)
            continue
        os.makedirs(trash, exist_ok=True)
        for rel in w.get("audio_files", []):
            src = os.path.join(LIBRARY_DIR, rel)
            if os.path.exists(src):
                shutil.move(src, os.path.join(trash, os.path.basename(src)))
        print(f"🧹 Старая запись '{w.get('title')}' (id=work) убрана, её аудио → _to_delete/library_audio_old")
    index["works"] = keep


def update_index(new_works):
    index = load_index()
    _drop_legacy(index)
    for w in new_works:
        index["works"] = [x for x in index.get("works", []) if x.get("id") != w["id"]]
        index["works"].append(w)
    os.makedirs(os.path.dirname(LIBRARY_INDEX), exist_ok=True)
    with open(LIBRARY_INDEX, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Обработать все .txt в library/texts")
    parser.add_argument("--text", help="Путь к .txt файлу")
    parser.add_argument("--title", help="Название произведения")
    parser.add_argument("--author", default="", help="Автор")
    parser.add_argument("--aliases", default="", help="Алиасы через запятую (бородино,лермонтов бородино)")
    parser.add_argument("--fast", action="store_true", help="edge-tts вместо RVC")
    parser.add_argument("--list", action="store_true", help="Показать содержимое библиотеки")
    args = parser.parse_args()

    if args.list:
        works = []
        if os.path.exists(LIBRARY_INDEX):
            with open(LIBRARY_INDEX, encoding="utf-8") as f:
                works = json.load(f).get("works", [])
        if not works:
            print("Библиотека пуста.")
            return
        for w in works:
            print(f"  • {w['title']} ({w.get('author') or '—'}) — {len(w.get('audio_files', []))} файлов, id={w['id']}")
        return

    mode = "fast" if args.fast else "anime"
    works = []
    if args.all:
        os.makedirs(LIBRARY_TEXTS_DIR, exist_ok=True)
        files = [f for f in sorted(os.listdir(LIBRARY_TEXTS_DIR)) if f.endswith(".txt")]
        if not files:
            print(f"Нет .txt файлов в {LIBRARY_TEXTS_DIR}")
            return
        # название/автор/алиасы берём из index.json, если произведение там уже есть
        known = {w.get("text_file"): w for w in load_index().get("works", [])}
        for fname in files:
            meta = known.get(f"texts/{fname}", {})
            title = meta.get("title") or os.path.splitext(fname)[0].replace("_", " ").strip().capitalize()
            print(f"\n📖 Обрабатываю: {fname} → '{title}'")
            works.append(build_work(os.path.join(LIBRARY_TEXTS_DIR, fname), title, meta.get("author", ""),
                                    meta.get("aliases") or [title.lower()], mode))
    elif args.text:
        if not os.path.exists(args.text):
            print(f"Файл не найден: {args.text}")
            return
        title = args.title or os.path.splitext(os.path.basename(args.text))[0]
        aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]
        works.append(build_work(args.text, title, args.author, aliases, mode))
    else:
        parser.print_help()
        return

    update_index(works)
    print(f"\n✅ Готово. Обновлено произведений: {len(works)}")


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            from rvc_client import WORKER
            WORKER.stop()
        except Exception:
            pass
