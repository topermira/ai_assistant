import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# bargein импортирует audio/stt (звуковые библиотеки) — в тестах подменяем заглушками
for name in ("audio", "stt"):
    sys.modules.setdefault(name, types.ModuleType(name))
import bargein
from bargein import Detector


def run(det, blocks, thr=0.01):
    return [det.feed(m, p, v, thr) for m, p, v in blocks]


def test_echo_alone_never_triggers():
    d = Detector()
    # колонки: в микрофон приходит ~30 % её голоса, VAD считает это речью
    blocks = [(0.3 * p, p, True) for p in [0.1, 0.15, 0.2, 0.12, 0.18, 0.2, 0.25, 0.1, 0.22, 0.3] * 3]
    assert not any(run(d, blocks))


def test_person_louder_than_echo_triggers_after_two_blocks():
    d = Detector()
    run(d, [(0.3 * 0.15, 0.15, True)] * 10)          # выучили связь ≈ 0.3
    r = run(d, [(0.3, 0.15, True), (0.32, 0.15, True)])
    assert r == [False, True]


def test_single_loud_block_is_not_enough():
    d = Detector()
    run(d, [(0.03, 0.1, False)] * 10)
    assert run(d, [(0.4, 0.1, True), (0.02, 0.1, False), (0.4, 0.1, True)]) == [False, False, False]


def test_headphones_quiet_voice_triggers():
    d = Detector()
    run(d, [(0.001, 0.2, False)] * 10)               # в наушниках эха почти нет
    assert run(d, [(0.03, 0.2, True), (0.03, 0.2, True)])[-1]


def test_unvoiced_noise_does_not_trigger():
    d = Detector()
    run(d, [(0.001, 0.2, False)] * 10)
    assert not any(run(d, [(0.2, 0.2, False)] * 5))


def test_take_pending_clears():
    bargein._pending = ["a"]
    assert bargein.pending()
    assert bargein.take_pending() == ["a"]
    assert not bargein.pending() and bargein.take_pending() is None
