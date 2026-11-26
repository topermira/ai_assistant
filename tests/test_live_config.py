import json
import os
import time

import settings


def _use(tmp_path, monkeypatch, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_FILE", str(p))
    monkeypatch.setattr(settings, "_live", {"mtime": None, "cfg": {}})
    return p


def test_live_config_rereads_on_change(tmp_path, monkeypatch):
    p = _use(tmp_path, monkeypatch, {"voice_volume": 0.5})
    assert settings.live_config("voice_volume", 1.0) == 0.5
    p.write_text(json.dumps({"voice_volume": 0.8}), encoding="utf-8")
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 2))
    assert settings.live_config("voice_volume", 1.0) == 0.8
    assert settings.live_config("missing", 7) == 7


def test_roxy_save_keeps_launcher_keys(tmp_path, monkeypatch):
    p = _use(tmp_path, monkeypatch, {"voice_volume": 0.3, "mic_device": "USB", "pet_autonomy": True})
    stale = {"voice_volume": 1.0, "mic_device": "", "pet_autonomy": False}     # копия в памяти Рокси
    settings.save_config(stale)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["voice_volume"] == 0.3 and d["mic_device"] == "USB" and d["pet_autonomy"] is False


def test_launcher_save_overrides(tmp_path, monkeypatch):
    p = _use(tmp_path, monkeypatch, {"voice_volume": 0.3})
    settings.save_config({"voice_volume": 1.2}, from_launcher=True)
    assert json.loads(p.read_text(encoding="utf-8"))["voice_volume"] == 1.2


def test_update_config_keeps_launcher_changes(tmp_path, monkeypatch):
    # лаунчер включил «Спрашивать перед опасным», пока Рокси работает; её копия в памяти — старая
    p = _use(tmp_path, monkeypatch, {"confirm_dangerous": True, "voice_mode": "anime", "tg_bot_owner": 7})
    monkeypatch.setattr(settings, "config", {"confirm_dangerous": False, "voice_mode": "anime", "tg_bot_owner": 7})
    settings.update_config("voice_mode", "fast")
    assert json.loads(p.read_text(encoding="utf-8")) == {"confirm_dangerous": True, "voice_mode": "fast",
                                                         "tg_bot_owner": 7}
    settings.update_config("tg_bot_owner", None)                 # None — ключ убирается
    assert json.loads(p.read_text(encoding="utf-8")) == {"confirm_dangerous": True, "voice_mode": "fast"}


def test_update_config_without_file_or_with_broken_file(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_FILE", str(p))
    monkeypatch.setattr(settings, "config", {"voice_mode": "anime"})
    settings.update_config("pet_autonomy", False)               # файла нет — основа из памяти
    assert json.loads(p.read_text(encoding="utf-8")) == {"voice_mode": "anime", "pet_autonomy": False}
    p.write_text("{битый", encoding="utf-8")
    settings.update_config("pet_autonomy", True)
    assert json.loads(p.read_text(encoding="utf-8")) == {"voice_mode": "anime", "pet_autonomy": True}
    assert (tmp_path / "config.json.broken").read_text(encoding="utf-8") == "{битый"
