import os
import zipfile

import install_helper
import pack_roxy


def test_torch_index():
    assert install_helper.torch_index("numpy==2.0\ntorch==2.5.1+cu124\n") == "https://download.pytorch.org/whl/cu124"
    assert install_helper.torch_index("torch==2.5.1\n") is None


def test_pack_skips_secrets_and_venv(tmp_path):
    src = tmp_path / "proj"
    (src / ".venv" / "Lib").mkdir(parents=True)
    (src / ".venv" / "Lib" / "x.py").write_text("x")
    (src / "temp").mkdir()
    (src / "temp" / "a.wav").write_text("x")
    (src / "main.py").write_text("print(1)")
    (src / ".env").write_text("KEY=secret")
    (src / "roxy_session.session").write_text("s")
    (src / "models").mkdir()
    (src / "models" / "m.vrm").write_bytes(b"\0" * 10)
    out = tmp_path / "o.zip"
    with zipfile.ZipFile(out, "w") as z:
        pack_roxy.add_tree(z, str(src), "Roxy", [0, 0])
    names = set(zipfile.ZipFile(out).namelist())
    assert names == {os.path.join("Roxy", "main.py"), os.path.join("Roxy", "models", "m.vrm")}
