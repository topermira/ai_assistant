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
