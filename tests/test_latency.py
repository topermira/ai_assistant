import time
import latency


def test_breakdown_printed(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(latency, "_LOG", str(tmp_path / "latency.log"))
    latency.start(time.monotonic() - 1.3, 1.3)
    latency.mark("stt"); latency.mark("intent"); latency.note("[CHAT]")
    latency.first_sound()
    out = capsys.readouterr().out
    assert "тишина 1.3" in out and "всего" in out and "[CHAT]" in out
    latency.first_sound()                     # второй звук той же команды — без второй строки
    assert capsys.readouterr().out == ""
    assert (tmp_path / "latency.log").read_text(encoding="utf-8").count("всего") == 1
