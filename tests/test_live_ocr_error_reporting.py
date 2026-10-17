"""An unavailable OCR runtime must not leave an apparently running empty overlay."""
import threading
from types import SimpleNamespace

from PIL import Image

from kizurium_translator.live import session
from kizurium_translator.live.state import State


def test_worker_publishes_ocr_failure_and_restores_visibility(monkeypatch, tmp_path):
    lock = tmp_path / 'live.lock'
    lock.touch()
    monkeypatch.setattr(session, '_lock_paths', lambda: (lock, tmp_path / 'pid'))
    translator = object()
    monkeypatch.setattr(session, 'translator', lambda: translator)
    monkeypatch.setattr(session, 'decide_frame', lambda *a: SimpleNamespace(
        need_clean=True, force_ocr=False, hold_hidden=False, last_dirty=None,
        last_key='', motion_hits=0, pending_key='', pending_hits=0, closed=False))
    monkeypatch.setattr(session, 'soft_recheck', lambda *a: SimpleNamespace(
        last_clean_check=0, last_dirty=None, last_key='', pending_key='',
        pending_hits=0, hold_hidden=False, skip=False))
    image = Image.new('RGB', (200, 100))
    monkeypatch.setattr(session, 'clean_capture', lambda *a, **kw: SimpleNamespace(image=image))
    monkeypatch.setattr(session, 'skip_unchanged_pixels', lambda *a: SimpleNamespace(
        motion_hits=0, last_clean=None, skip=False))
    monkeypatch.setattr(session, 'read_dirty_regions', lambda *a: SimpleNamespace(
        tracked=[], handled=False))
    def unavailable(*a):
        raise RuntimeError('Missing OCR runtime')
    monkeypatch.setattr(session, 'read_frame', unavailable)
    stop = threading.Event()
    delays = []
    def end_after_failure(delay):
        delays.append(delay)
        stop.set()
    monkeypatch.setattr(stop, 'wait', end_after_failure)
    state = State()
    session.worker(state, '0,0 200x100', 0, 0, 200, 100, stop)
    assert not state.hidden
    assert 'Missing OCR runtime' in state.snapshot()[2]
    assert delays and delays[0] >= 1.0


def test_english_dialogue_after_a_colon_is_not_discarded_as_overlay_echo():
    from kizurium_translator.ocr.engine import is_overlay_echo_ocr
    assert not is_overlay_echo_ocr('Mika: I have the right to be angry!')
    assert not is_overlay_echo_ocr(
        'r Speed: 5 ms Temp: 20 C\nMika Morning, Ella. Sometimes I wonder '
        "if I'm in the right place — all this hauling back-and-forth gets tiresome "
        "and, quite frankly, I'm upset at it more than I should be.")
