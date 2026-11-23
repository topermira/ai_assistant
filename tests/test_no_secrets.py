"""Страховка перед GitHub: в исходниках не должно быть ключей, токенов и файлов сессий."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_secrets  # noqa: E402


def test_project_has_no_secrets():
    # .env и config.json — локальные, в .gitignore; проверяем только то, что реально уйдёт в репозиторий
    paths = [p for p in check_secrets.project_files()
             if os.path.basename(p) not in (".env", "config.json", "memory.json")]
    findings = check_secrets.scan_files(paths, ignore_secret_names=True)
    assert not findings, f"найдены секреты: {findings}"


def test_scanner_detects_openrouter_like_key():
    fake = "sk-or-v1-" + "a1b2c3d4" * 6              # собираем в рантайме, чтобы не засорять репозиторий
    assert check_secrets.scan_text(f'OPENROUTER_API_KEY = "{fake}"')


def test_scanner_detects_groq_and_hash():
    groq = "gsk_" + "Ab3dEf6h" * 5
    assert check_secrets.scan_text(f'KEY = "{groq}"')
    tg = "0123456789abcdef" * 2
    assert check_secrets.scan_text(f'"tg_api_hash": "{tg}"')


def test_scanner_ignores_placeholders_and_env_reads():
    assert not check_secrets.scan_text('OPENROUTER_API_KEY = get_secret("OPENROUTER_API_KEY")')
    assert not check_secrets.scan_text('api_key = "your-api-key-goes-here-123456"')
    assert not check_secrets.scan_text("OPENROUTER_API_KEY=")


def test_scanner_flags_session_and_env_files():
    assert check_secrets.scan_name("roxy_session.session")
    assert check_secrets.scan_name(".env")
    assert not check_secrets.scan_name(".env.example")


def test_dotenv_loader(tmp_path, monkeypatch):
    import settings
    f = tmp_path / ".env"
    f.write_text('# коммент\nA_KEY="quoted value"\nexport B_KEY=plain # tail\nEMPTY=\n', encoding="utf-8")
    monkeypatch.setattr(settings, "_DOTENV", {})
    settings._load_dotenv(str(f))
    assert settings._DOTENV["A_KEY"] == "quoted value"
    assert settings._DOTENV["B_KEY"] == "plain"
    assert settings._DOTENV["EMPTY"] == ""
    monkeypatch.delenv("A_KEY", raising=False)
    assert settings.get_secret("A_KEY") == "quoted value"
    monkeypatch.setenv("A_KEY", "from-env")
    assert settings.get_secret("A_KEY") == "from-env"      # реальное окружение приоритетнее .env


def test_mask_secret():
    import settings
    assert settings.mask_secret("sk-or-v1-abcdef") == "sk-o…"
    assert "abcdef" not in settings.mask_secret("sk-or-v1-abcdef")
    assert settings.mask_secret("") == ""
