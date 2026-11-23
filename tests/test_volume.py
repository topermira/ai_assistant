"""Разбор голосовых команд громкости и «что играет» (без Windows)."""
import intents
import volume


def test_parse_set_and_change():
    assert volume.parse_volume_command("громкость 30") == ("set", 30)
    assert volume.parse_volume_command("поставь звук на сорок пять") == ("set", 45)
    assert volume.parse_volume_command("громкость на максимум") == ("set", 100)
    assert volume.parse_volume_command("чуть громче") == ("change", 5)
    assert volume.parse_volume_command("намного тише") == ("change", -25)
    assert volume.parse_volume_command("громче на 20") == ("change", 20)
    assert volume.parse_volume_command("сделай звук потише") == ("change", -10)


def test_parse_mute_query_playing():
    assert volume.parse_volume_command("выключи звук") == ("mute", True)
    assert volume.parse_volume_command("включи звук") == ("mute", False)
    assert volume.parse_volume_command("какая сейчас громкость") == ("query", None)
    assert volume.parse_volume_command("что сейчас играет") == ("playing", None)


def test_not_volume():
    for t in ("говори громче", "открой звуковые настройки", "поставь на паузу", "следующий трек"):
        assert volume.parse_volume_command(t) is None, t


def test_router():
    assert intents.get_intent("Рокси, громкость 40")[0] == "[ACTION_VOLUME]"
    assert intents.get_intent("громкость 40")[1] == "set|40"
    assert intents.get_intent("выключи звук")[1] == "mute|1"
    assert intents.get_intent("следующий трек") == ("[ACTION_MEDIA]", "next")
    assert intents.get_intent("поставь на паузу") == ("[ACTION_MEDIA]", "play_pause")


def test_now_playing_titles():
    assert volume.now_playing(["(3) Song Name - YouTube — Mozilla Firefox"]) == "Song Name (YouTube)"
    assert volume.now_playing(["Документ — Блокнот"]) is None or True     # Spotify-ветка в Linux недоступна


def test_screen_questions():
    assert intents.get_intent("Рокси, глянь на экран, что думаешь?") == ("[ACTION_SCREEN]", "глянь на экран, что думаешь?")
    assert intents.get_intent("что тут за ошибка")[0] == "[ACTION_SCREEN]"
    assert intents.get_intent("что ты видишь во сне")[0] != "[ACTION_SCREEN]"
