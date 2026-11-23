import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_tts import preprocess_text_for_tts as pp, extract_emotion, split_for_pipeline  # noqa: E402
from timers import parse_duration, parse_reminder  # noqa: E402
from library import make_work_id, find_work  # noqa: E402
from telegram_service import match_score, pick_best  # noqa: E402


@pytest.mark.parametrize("src,expected", [
    ("5-й день", "5-й день"),
    ("через 2-3 дня", "через от 2 до 3 дня"),
    ("COVID-19", "COVID-19"),
    ("все люди", "все люди"),
    ("война 1812-1815", "война с 1812 по 1815"),
    ("ощущается как -7°C", "ощущается как минус 7°C"),
    ("5 - 3 = 2", "5 минус 3 = 2"),
    ("2+2", "2 плюс 2"),
    ("скорость 60 км/ч", "скорость 60 километров в час"),
    ("да/нет", "да или нет"),
    ("сегодня 19-11-2026", "сегодня 19.11.2026"),
    ("что-то пошло не так", "что-то пошло не так"),
    ("**Важно**: `код`", "Важно: код"),
])
def test_preprocess(src, expected):
    assert pp(src) == expected


def test_url_slash_kept():
    assert "site.com/page" in pp("открой site.com/page")


def test_emotion_tag():
    assert extract_emotion("[happy] Привет!") == ("happy", "Привет!")
    assert extract_emotion("Просто текст") == (None, "Просто текст")
    assert pp("[smug] Конечно знаю.") == "Конечно знаю."


def test_pipeline_split():
    assert split_for_pipeline("Привет. Как дела? Всё хорошо.") == ["Привет.", "Как дела? Всё хорошо."]
    assert split_for_pipeline("Одно предложение") == ["Одно предложение"]


@pytest.mark.parametrize("text,secs", [
    ("поставь таймер на 5 минут", 300),
    ("через полчаса", 1800),
    ("на час и 10 минут", 4200),
    ("таймер на двадцать пять секунд", 25),
    ("полтора часа", 5400),
    ("напомни через минуту", 60),
    ("таймер на 2 часа", 7200),
    ("опять таймер", None),
])
def test_duration(text, secs):
    assert parse_duration(text)[0] == secs


def test_reminder():
    assert parse_reminder("напомни через 10 минут выключить чайник") == (600, "выключить чайник")
    assert parse_reminder("напомни мне через 5 минут позвонить маме") == (300, "позвонить маме")


def test_work_id():
    assert make_work_id("Бородино") == "borodino"
    assert make_work_id("Мцыри") == "mtsyri"
    assert make_work_id("Бородино") != make_work_id("Парус")
    assert make_work_id("!!!").startswith("work_")


def test_find_work_prefers_longest():
    works = [
        {"id": "borodino", "title": "Бородино", "aliases": ["лермонтов"]},
        {"id": "parus", "title": "Парус", "aliases": ["лермонтов парус"]},
    ]
    assert find_work("прочитай лермонтов парус", works)["id"] == "parus"
    assert find_work("расскажи бородино", works)["id"] == "borodino"
    assert find_work("прочитай что-нибудь", works) is None


def test_tg_matching():
    assert match_score("Даниэлю", "Даниэль Иванов") > 0
    assert match_score("васе", "Вася") > 0
    assert match_score("маша", "Максим") == 0
    assert match_score("стих", "Стас") == 0
    assert match_score("@vasya", "Кто-то", "vasya") == 100
    obj, label = pick_best([(80, "Вася Петров", 1), (70, "Вася Сидоров", 2)])
    assert obj == 1
    obj, labels = pick_best([(80, "Вася Петров", 1), (80, "Вася Сидоров", 2)])
    assert obj is None and len(labels) == 2


def test_format_duration():
    from timers import format_duration
    assert format_duration(300) == "5 минут"
    assert format_duration(61) == "1 минута 1 секунда"
    assert format_duration(3900) == "1 час 5 минут"
    assert format_duration(150) == "2 минуты 30 секунд"


def test_pipeline_split_fine():
    text = "Привет. " + " ".join(["Это довольно длинное предложение номер %d." % i for i in range(10)])
    parts = split_for_pipeline(text, fine=True, group_max=120)
    assert parts[0] == "Привет."
    assert all(len(p) <= 120 for p in parts[1:])
    assert " ".join(parts) == text
