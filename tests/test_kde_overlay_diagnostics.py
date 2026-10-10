"""Visible failure states and clean capture must coexist without OCR-ing the banner."""
import threading
import time

import pytest

from kizurium_translator import capture
from kizurium_translator.live import snap
from kizurium_translator.live.state import State


def test_status_update_preserves_cards_and_does_not_bump_translation_revision():
    from kizurium_translator.translation.scheduler import SCHEDULER
    state = State()
    block = {"text": "перевод", "x": 0, "y": 0}
    state.set([block], (0, 0, 100, 100))
    revision = SCHEDULER.capture()
    state.set_status("Ошибка захвата")
    version = state.version()
    state.set_status("Ошибка захвата")
    assert state.version() == version
    assert state.peek_blocks() == [block]
    assert SCHEDULER.capture() == revision
    state.hide()
    assert state.snapshot() == ([], (0, 0, 100, 100), "")
    state.show()
    assert state.snapshot()[2] == "Ошибка захвата"


def test_error_banner_is_drawn_and_hidden_for_capture():
    cairo = pytest.importorskip("cairo")
    from kizurium_translator.render.overlay import draw_blocks
    state = State()
    state.set([], (0, 0, 400, 120), "Перевод недоступен: автономный режим")
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 400, 120)
    cr = cairo.Context(surface)
    draw_blocks(cr, *state.snapshot())
    surface.flush()
    assert any(surface.get_data())
    state.hide()
    draw_blocks(cr, *state.snapshot())
    surface.flush()
    assert not any(surface.get_data())


def test_capture_failure_reaches_the_worker(monkeypatch):
    def fail(_):
        raise capture.CaptureError("Permission denied")
    monkeypatch.setattr(capture, "capture_image", fail)
    state = State()
    got = snap.clean_capture(state, "0,0 100x100", paint_timeout=0, safety_sleep=0)
    assert got.image is None
    assert got.error == "Permission denied"


def test_static_stream_does_not_wait_three_seconds():
    sample = object()
    stream = {"condition": threading.Condition(), "last": sample, "received_at": 1.0}
    start = time.monotonic()
    assert capture._PortalSession._latest_sample(stream) is sample
    assert time.monotonic() - start < 0.1


def test_clean_capture_rejects_a_stale_queued_frame(monkeypatch):
    monkeypatch.setattr(capture, "_FRAME_TIMEOUT_S", 0.02)
    stream = {"condition": threading.Condition(), "last": object(), "received_at": 1.0}
    with pytest.raises(capture.CaptureError, match="fresh frame"):
        capture._PortalSession._latest_sample(stream, after=2.0)


def test_clean_capture_wakes_for_a_new_frame():
    sample = object()
    stream = {"condition": threading.Condition(), "last": object(), "received_at": 1.0}
    def produce():
        time.sleep(0.01)
        with stream["condition"]:
            stream.update(last=sample, received_at=3.0)
            stream["condition"].notify_all()
    thread = threading.Thread(target=produce)
    thread.start()
    try:
        assert capture._PortalSession._latest_sample(stream, after=2.0) is sample
    finally:
        thread.join()


def test_freshness_requirement_is_scoped_to_one_capture():
    assert getattr(capture._frame_request, "after", None) is None
    with capture.fresh_frame_after(123):
        assert capture._frame_request.after == 123
    assert capture._frame_request.after is None
