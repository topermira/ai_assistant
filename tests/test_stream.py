"""Потоковый ответ: нарезка на предложения, генератор с подставной моделью, пауза облачного STT."""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def llm_mod(monkeypatch):
    for name in ("openai", "groq"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    import importlib
    return importlib.import_module("llm")


def test_split_ready_sentences_merges_short_and_keeps_tail(llm_mod):
    ready, rest = llm_mod.split_ready_sentences("[happy] Привет! Как здорово, что ты зашёл ко мне. А ещё")
    assert ready == ["[happy] Привет! Как здорово, что ты зашёл ко мне."]
    assert rest.strip() == "А ещё"


def test_split_ready_sentences_newline_cuts(llm_mod):
    ready, rest = llm_mod.split_ready_sentences("Пункт один\nПункт два")
    assert ready == ["Пункт один"] and rest == "Пункт два"


def _fake_stream(parts):
    for p in parts:
        yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=p))])


def test_stream_yields_sentences_and_remembers(llm_mod, monkeypatch):
    client = object()
    monkeypatch.setattr(llm_mod, "ONLINE_MODELS", {"[CHAT]": {"client": client, "model": "m"}})
    monkeypatch.setattr(llm_mod, "GROQ_FALLBACK_MODELS", {"[CHAT]": {"client": None, "model": "x"}})
    monkeypatch.setattr(llm_mod, "LOCAL_MODELS", {"[CHAT]": {"client": None, "model": "x"}})
    monkeypatch.setattr(llm_mod, "_build_msgs", lambda t, i: [])
    monkeypatch.setattr(llm_mod, "_chat", lambda *a, **k: _fake_stream(
        ["[happy] Конечно, сейчас ", "расскажу тебе всё по порядку. ", "Сначала главное", " — не волнуйся!"]))
    pushed = []
    monkeypatch.setattr(llm_mod, "_push_history", lambda r, c: pushed.append((r, c)))
    out = list(llm_mod.think_llm_stream("привет"))
    assert out == ["[happy] Конечно, сейчас расскажу тебе всё по порядку.", "Сначала главное — не волнуйся!"]
    assert pushed[0] == ("user", "привет") and "[happy]" not in pushed[1][1]


def test_stream_falls_back_when_no_model(llm_mod, monkeypatch):
    for t in ("ONLINE_MODELS", "GROQ_FALLBACK_MODELS", "LOCAL_MODELS"):
        monkeypatch.setattr(llm_mod, t, {"[CHAT]": {"client": None, "model": "x"}})
    monkeypatch.setattr(llm_mod, "_build_msgs", lambda t, i: [])
    assert list(llm_mod.think_llm_stream("привет")) == ["[sad] Извини, не смогла сформулировать."]
