"""Письменный ввод: события из окна аватара доходят до main даже после пауз (раньше поток приёма умирал
через 2 секунды тишины из-за таймаута сокета)."""
import json

import websocket

import intents
from live2d_client import Live2DClient


class _FakeConn:
    def __init__(self, script):
        self.script = list(script)

    def recv(self):
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def test_reader_survives_timeouts():
    got = []
    cl = Live2DClient()
    cl.on_event = got.append
    conn = _FakeConn([websocket.WebSocketTimeoutException("idle"), websocket.WebSocketTimeoutException("idle"),
                      json.dumps({"type": "typed", "text": "покрутись"}), ConnectionError("closed")])
    cl._running = True
    cl._ws = conn
    cl._reader(conn)
    assert got == [{"type": "typed", "text": "покрутись"}]


def test_spin_synonyms():
    for t in ("покрутись", "крутись", "покружись", "вращайся"):
        assert intents.match_pet(t) == "spin", t
