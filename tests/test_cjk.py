import cjk
import text_tts


def test_kana_and_kanji_japanese():
    assert cjk.to_russian("こんにちは、ロキシーです！").startswith("Коннитива")
    assert "Токё" in cjk.to_russian("東京へ行きます。")
    assert cjk.romaji_ru("sensei") == "сэнсэй"
    assert cjk.romaji_ru("gakkou") == "гакко"


def test_chinese_palladius():
    assert cjk.to_russian("你好") == "Ни хао"
    assert cjk.pinyin_ru("zhong") == "чжун" and cjk.pinyin_ru("xue") == "сюэ" and cjk.pinyin_ru("si") == "сы"


def test_no_cjk_untouched_and_tts_has_no_cjk():
    assert cjk.to_russian("Привет, мир") == "Привет, мир"
    out = text_tts.preprocess_text_for_tts("Это 「可愛い」, то есть милый")
    assert not cjk.has_cjk(out) and "Кавай" in out


def test_kana_without_library(monkeypatch):
    monkeypatch.setattr(cjk, "_kks", False)
    assert cjk.to_russian("すごい") == "Сугой"
