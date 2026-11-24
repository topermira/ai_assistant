import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intents import get_intent, parse_yes_no  # noqa: E402

CASES = [
    # --- ложные «стоны» из старой версии
    ("установи программу", "!", "[ACTION_MOAN]"),
    ("погода в Астане", "[ACTION_WEATHER]", None),
    ("Рокси, остановись пожалуйста", "[ACTION_STOP]", None),
    ("постонай", "[ACTION_MOAN]", None),
    ("Рокси, стони", "[ACTION_MOAN]", None),
    ("постанай", "[ACTION_MOAN]", None),
    # --- Telegram: обычные слова больше не контакты
    ("напиши стих про осень", "[TEXT]", None),
    ("Напиши стих про осень", "[TEXT]", None),
    ("скажи сколько времени", "[ACTION_TIME]", "time"),
    ("Напиши Даниэлю то что я его люблю", "[ACTION_TG_SEND_MAYBE]", "Даниэлю|я его люблю"),
    ("напиши Константину привет", "!", "[ACTION_MOAN]"),
    ("напиши в телеграм @vasya привет", "[ACTION_TG_SEND]", "@vasya|привет"),
    ("прочитай телеграм Вася", "[ACTION_TG_READ]", "Вася"),
    # --- код и печать
    ("напиши код сортировки пузырьком", "[ACTION_CODE_FILE]", None),
    ("напиши скрипт для парсинга сайта", "[ACTION_CODE_FILE]", None),
    ("создай файл скрипт.py", "[ACTION_CREATE]", None),
    ("напиши привет как дела", "[ACTION_TYPE]", None),
    # --- хоткеи, которые раньше съедал ACTION_TYPE
    ("запиши экран", "[ACTION_HOTKEY]", "win+alt+r|Запись экрана"),
    ("вставь из буфера", "[ACTION_HOTKEY]", "ctrl+v|Вставляю"),
    ("вставь", "[ACTION_HOTKEY]", "ctrl+v|Вставляю"),
    ("открой новую вкладку", "[ACTION_HOTKEY]", "ctrl+t|Новая вкладка"),
    # --- режимы
    ("включи быстро музыку", "!", "[MODE_FAST]"),
    ("включи быстрый режим", "[MODE_FAST]", None),
    ("верни рокси", "[MODE_ANIME]", None),
    # --- новое
    ("поставь таймер на 5 минут", "[ACTION_TIMER]", None),
    ("напомни через 10 минут выключить чайник", "[ACTION_REMIND]", None),
    ("отмени таймер", "[ACTION_TIMER_CANCEL]", None),
    ("сколько осталось на таймере", "[ACTION_TIMER_LIST]", None),
    ("сделай громче", "[ACTION_VOLUME]", "change|10"),
    ("тише", "[ACTION_VOLUME]", "change|-10"),
    ("поставь на паузу", "[ACTION_MEDIA]", "play_pause"),
    ("следующий трек", "[ACTION_MEDIA]", "next"),
    # --- остальное как было
    ("удали файл отчёт", "[ACTION_DELETE_FILE]", None),
    ("Рокси, открой блокнот", "[ACTION_OPEN]", None),
    ("кликни на кнопку отправить", "[ACTION_VISION]", None),
    ("сверни все окна", "[ACTION_MINIMIZE_ALL]", None),
    ("закрой все окна", "[ACTION_CLOSE_ALL]", None),
]


@pytest.mark.parametrize("phrase,expected,extra", CASES)
def test_intent(phrase, expected, extra):
    intent, task = get_intent(phrase)
    if expected == "!":                       # «НЕ должно быть» extra
        assert intent != extra, (phrase, intent)
        return
    assert intent == expected, (phrase, intent, task)
    if extra is not None:
        assert task == extra


def test_library_lookup_used():
    work = {"id": "borodino", "title": "Бородино"}
    intent, task = get_intent("прочитай бородино", library_lookup=lambda q: work)
    assert (intent, task) == ("[ACTION_LIBRARY]", "borodino")


def test_router_called_only_as_fallback():
    calls = []

    def router(text):
        calls.append(text)
        return ("[ACTION_OPEN]", "")

    assert get_intent("открой блокнот", llm_router=router)[0] == "[ACTION_OPEN]"
    assert calls == []
    assert get_intent("мне бы глянуть что там с ютубом", llm_router=router)[0] == "[ACTION_OPEN]"
    assert calls == ["мне бы глянуть что там с ютубом"]


@pytest.mark.parametrize("ans,expected", [
    ("да", True), ("Да, удаляй.", True), ("давай", True), ("ага", True),
    ("нет", False), ("да нет, не надо", False), ("отмена", False),
    ("эээ", None), ("", None),
])
def test_yes_no(ans, expected):
    assert parse_yes_no(ans) is expected


@pytest.mark.parametrize("phrase,expected", [
    ("Роксен. Вставь сюда программу которая выведет числа от 1 до 100.", "[ACTION_CODE_FILE]"),
    ("Рокси. Напиши сюда код, который выведет числа от 1 до 100.", "[ACTION_CODE_FILE]"),
    ("Рокси. Удали всё из этого блокнота.", "[ACTION_CLEAR_TEXT]"),
    ("вставь из буфера", "[ACTION_HOTKEY]"),
    ("Вставь сюда привет мир", "[ACTION_TYPE]"),
])
def test_from_real_log(phrase, expected):
    assert get_intent(phrase)[0] == expected


def test_repeated_command_is_not_yes():
    # из реального лога: на вопрос «Стереть весь текст?» распозналось повторение команды
    assert parse_yes_no("Рокси. Удали весь текст из блокнота.") is None


@pytest.mark.parametrize("phrase,name", [
    ("Рокси, прыгни", "jump"), ("подпрыгни", "jump"),
    ("сядь на окно", "perch"), ("залезь на окно", "perch"),
    ("слезь", "floor"), ("иди сюда", "come"), ("погуляй", "walk"),
    ("стой на месте", "autonomy_off"), ("гуляй сама", "autonomy_on"),
    ("кивни", "nod"),
])
def test_pet(phrase, name):
    assert get_intent(phrase) == ("[ACTION_PET]", name)


def test_pet_does_not_break_others():
    assert get_intent("стоп")[0] == "[ACTION_STOP]"
    assert get_intent("закрой окно")[0] == "[ACTION_WINDOW]"
    assert get_intent("Ставь сюда код, который выдаст числа от 1 до 100.")[0] == "[ACTION_CODE_FILE]"


@pytest.mark.parametrize("phrase,name", [
    ("Рокси. Отпрыгни.", "jump"), ("Рокси. Сяди на окно.", "perch"), ("Рокси. Сядьте на окно.", "perch"),
    ("садись на окно", "perch"), ("запрыгни на окно", "perch"), ("потанцуй", "dance"),
    ("покрутись", "spin"), ("поклонись", "bow"), ("потянись", "stretch"),
    ("наколдуй что-нибудь", "magic"), ("Рокси, покажи магию", "magic"), ("поколдуй", "magic"),
    ("сотвори заклинание", "magic"), ("покажи водную магию", "magic"),
    ("толкни окно", "push"), ("подвинь это окно", "push"), ("спрячься", "hide"), ("поиграй со мной в прятки", "hide"),
    ("выгляни из-за края", "peek"),
    ("наколдуй лёд", "magic_ice"), ("ледяное заклинание", "magic_ice"), ("вызови дождь", "magic_rain"),
    ("наколдуй тучку", "magic_rain"), ("сделай фонтан", "magic_fountain"), ("наколдуй водный шар", "magic_orb"),
    ("потанцуй под музыку", "dance_music"), ("Рокси, станцуй под эту песню", "dance_music"),
    ("подвигайся в такт", "dance_music"), ("хватит танцевать", "dance_stop"), ("перестань танцевать", "dance_stop"),
    ("потанцуй", "dance"),
    ("встань на руки", "handstand"), ("Рокси, сделай стойку на руках", "handstand"),
    ("сделай колесо", "cartwheel"), ("Рокси, колесо", "cartwheel"), ("пройдись колесом", "cartwheel"),
    ("сделай сальто", "flip"), ("Рокси, крутани сальто назад", "flip"), ("прыгни", "jump"),
    ("покрути посох", "staff_twirl"), ("обопрись на посох", "staff_lean"),
])
def test_pet_more(phrase, name):
    assert get_intent(phrase) == ("[ACTION_PET]", name)
