"""Горячие клавиши и набор текста не зависят от раскладки (раньше в русской Ctrl+V не срабатывал)."""
import kbfix


def test_letters_get_real_vk_even_when_layout_has_none():
    # так выглядит таблица pyautogui в русской раскладке: латиницы нет (-1)
    m = {c: -1 for c in "abcxyzv.,;"}
    m["."] = 0xBF            # точка в русской раскладке — на другой клавише, это верно, не трогаем
    kbfix.layout_free_mapping(m)
    assert m["v"] == 0x56 and m["c"] == 0x43 and m["a"] == 0x41
    assert m["V"] == 0x156                    # Shift + V
    assert m["5"] == 0x35
    assert m["."] == 0xBF                     # существующий код сохранён
    assert m[";"] == 0xBA                     # отсутствующий — американская раскладка
    mods, vk = divmod(m["v"], 0x100)
    assert mods == 0 and vk == 0x56           # без Ctrl+Alt+Shift-мусора


def test_unicode_units_cyrillic_newline_emoji():
    u = kbfix._units("Гб\n😀")
    assert u[0] == ("u", ord("Г")) and u[1] == ("u", ord("б"))
    assert u[2] == ("vk", 0x0D)
    assert len(u) == 5 and all(k == "u" for k, _ in u[3:])   # эмодзи — две половинки UTF-16


def test_type_unicode_is_noop_off_windows():
    import os
    if os.name != "nt":
        assert kbfix.type_unicode("abc") is False
