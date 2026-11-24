import pcctl
from intents import get_intent


def test_mouse_parse():
    assert pcctl.parse_mouse("перенеси мышку на 100 пикселей вниз") == ("move", 0, 100)
    assert pcctl.parse_mouse("подвинь курсор на 50 вправо и на 20 вверх") == ("move", 50, -20)
    assert pcctl.parse_mouse("мышку в центр") == ("goto", "center")
    assert pcctl.parse_mouse("курсор в правый нижний угол") == ("goto", "br")
    assert pcctl.parse_mouse("кликни правой кнопкой мыши") == ("click", "right", 1)
    assert pcctl.parse_mouse("двойной клик") == ("click", "left", 2)
    assert pcctl.parse_mouse("прокрути вниз") == ("scroll", -5)
    assert get_intent("кликни на кнопку отправить")[0] == "[ACTION_VISION]"   # клик по цели — агенту


def test_brightness_parse():
    assert pcctl.parse_brightness("яркость 50") == ("set", 50)
    assert pcctl.parse_brightness("сделай ярче") == ("up", 15)
    assert pcctl.parse_brightness("темнее на 20") == ("down", 20)
    assert pcctl.parse_brightness("какая яркость") == ("query", 0)
    assert pcctl.parse_brightness("громче") is None
    assert get_intent("яркость на максимум") == ("[ACTION_BRIGHTNESS]", "set|100")


def test_brightness_relative(monkeypatch):
    monkeypatch.setattr(pcctl, "get_brightness", lambda: 40)
    got = []
    monkeypatch.setattr(pcctl, "set_brightness", lambda n: got.append(n) or True)
    assert pcctl.brightness(("up", 15)) == "Яркость 55%." and got == [55]
    monkeypatch.setattr(pcctl, "set_brightness", lambda n: False)
    assert "DDC/CI" in pcctl.brightness(("set", 10))


def test_type_target():
    from intents import type_target
    assert type_target("напиши привет") == "cursor"
    assert type_target("напиши в блокнот привет") == "notepad"
    assert type_target("напиши привет в блокноте") == "notepad"
    assert get_intent("напиши привет как дела")[0] == "[ACTION_TYPE]"


def test_click_buttons():
    p = pcctl.parse_mouse
    assert p("нажми пкм") == ("click", "right", 1)
    assert p("нажми лкм") == ("click", "left", 1)
    assert p("нажми колёсико") == ("click", "middle", 1)
    assert p("нажми левой кнопкой мыши") == ("click", "left", 1)
    assert p("нажми на правую кнопку мыши") == ("click", "right", 1)
    assert p("кликни правой два раза") == ("click", "right", 2)
    assert p("нажми") is None and p("левой") is None and p("нажми enter") is None
    assert get_intent("сделай колесо") == ("[ACTION_PET]", "cartwheel")
    assert get_intent("нажми на кнопку отправить")[0] == "[ACTION_VISION]"


def test_bare_move_and_number_words():
    p = pcctl.parse_mouse
    assert p("передвинь на 300 пикселей вниз") == ("move", 0, 300)       # без слова «мышку»
    assert p("передвинь на триста пятьдесят пикселей вниз") == ("move", 0, 350)
    assert p("перенеси файл вниз") is None                                # посторонний объект — не мышь
    assert pcctl.words_to_digits("кликни два раза") == "кликни два раза"
    assert pcctl.parse_brightness("яркость пятьдесят") == ("set", 50)
