"""
Проверка проекта на утечки секретов ПЕРЕД публикацией на GitHub.

    python check_secrets.py              # проверить файлы проекта (то, что попадёт в git)
    python check_secrets.py --staged     # проверить только то, что в индексе git (для pre-commit)
    python check_secrets.py --install-hook   # поставить это как git pre-commit хук

Код возврата 1 — найдено что-то похожее на ключ/токен/сессию. Найденное печатается в замаскированном виде.
Скрипт ничего не удаляет и никуда не отправляет.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# (название, регулярка). Подбирались под сервисы, которые использует Рокси, плюс самые частые «в мире».
PATTERNS = [
    ("OpenRouter key", re.compile(r"sk-or-v1-[0-9a-fA-F]{20,}")),
    ("Groq key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
    # api_hash Telegram: 32 hex рядом со словом hash/key/secret/token
    ("32-hex secret", re.compile(r"(?i)(?:hash|key|secret|token)\w*[\"']?\s*[:=]\s*[\"'][0-9a-f]{32}[\"']")),
    # присваивание длинной строки-литерала переменной с «секретным» именем
    ("secret assignment", re.compile(
        r"(?i)\b\w*(?:api[_-]?key|secret|passw(?:or)?d|token|api[_-]?hash)\w*\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']")),
]
PLACEHOLDER = re.compile(r"(?i)your|example|placeholder|changeme|xxxx|\.\.\.|<.+>|\$\{|%\(|os\.environ|getenv|get_secret")

SECRET_FILE_NAMES = (".env",)
SECRET_FILE_SUFFIXES = (".session", ".session-journal", ".pem", ".key", ".pfx", ".p12")
SECRET_FILE_ALLOWED = (".env.example",)

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".idea", ".vscode", ".pytest_cache", ".ruff_cache",
             "node_modules", "piper_voice", "whisper_medium_model", "vosk_model_ru", "library", "sounds",
             "logs", "temp", "rvc_output", "live2d_assets", "Claude outputs", "_to_delete"}
TEXT_SUFFIXES = (".py", ".md", ".txt", ".json", ".html", ".js", ".yml", ".yaml", ".toml", ".ini", ".cfg",
                 ".bat", ".ps1", ".sh", ".example", ".env", "")
MAX_BYTES = 2_000_000


def _mask(s):
    s = s.strip()
    return (s[:4] + "…" + f"({len(s)} симв.)") if len(s) > 8 else "***"


def scan_text(text, label="<text>"):
    """Список находок [(label, line_no, kind, masked)] в строке текста."""
    found = []
    for no, line in enumerate(text.splitlines(), 1):
        if "noqa: secret" in line:
            continue
        for kind, rx in PATTERNS:
            m = rx.search(line)
            if not m:
                continue
            value = m.group(1) if m.groups() else m.group(0)
            if PLACEHOLDER.search(line) or PLACEHOLDER.search(value):
                continue
            found.append((label, no, kind, _mask(value)))
            break
    return found


def scan_name(rel):
    base = os.path.basename(rel)
    if base in SECRET_FILE_ALLOWED:
        return []
    if base in SECRET_FILE_NAMES or base.startswith(".env.") or base.endswith(SECRET_FILE_SUFFIXES):
        return [(rel, 0, "файл с секретами не должен быть в репозитории", base)]
    return []


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _in_git():
    try:
        return _git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    except OSError:
        return False


def project_files():
    """Файлы, которые попадут в git: если это репозиторий — отслеживаемые и неигнорируемые, иначе обход папок."""
    if _in_git():
        r = _git("ls-files", "-co", "--exclude-standard")
        return [p for p in r.stdout.splitlines() if p and os.path.isfile(os.path.join(ROOT, p))]
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
    return files


def scan_files(rel_paths, ignore_secret_names=False):
    findings = []
    for rel in rel_paths:
        if not ignore_secret_names:
            findings += scan_name(rel)
        full = os.path.join(ROOT, rel)
        if not rel.lower().endswith(TEXT_SUFFIXES):
            continue
        try:
            if os.path.getsize(full) > MAX_BYTES:
                continue
            with open(full, "r", encoding="utf-8", errors="ignore") as f:
                findings += scan_text(f.read(), rel)
        except OSError:
            continue
    return findings


def scan_staged():
    r = _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    findings = []
    for rel in [p for p in r.stdout.splitlines() if p]:
        findings += scan_name(rel)
        blob = _git("show", f":{rel}")
        if len(blob.stdout) <= MAX_BYTES and rel.lower().endswith(TEXT_SUFFIXES):
            findings += scan_text(blob.stdout, rel)
    return findings


def install_hook():
    git_dir = os.path.join(ROOT, ".git")
    if not os.path.isdir(git_dir):
        print("❌ Это не git-репозиторий (нет папки .git). Сначала: git init")
        return 1
    hooks = os.path.join(git_dir, "hooks")
    os.makedirs(hooks, exist_ok=True)
    path = os.path.join(hooks, "pre-commit")
    py = sys.executable.replace("\\", "/")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f'#!/bin/sh\n"{py}" check_secrets.py --staged || exit 1\n')
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    print("✅ pre-commit хук установлен:", path)
    return 0


def main(argv):
    if "--install-hook" in argv:
        return install_hook()
    findings = scan_staged() if "--staged" in argv else scan_files(project_files())
    if not findings:
        print("✅ Секретов не найдено.")
        return 0
    print("❌ НАЙДЕНО похожее на секреты — НЕ публикуй, пока не уберёшь:")
    for label, no, kind, masked in findings:
        where = f"{label}:{no}" if no else label
        print(f"  • {where}  [{kind}]  {masked}")
    print("\nКлючи вынеси в .env, а если они уже где-то публиковались — выпусти новые (старые считай утёкшими).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
