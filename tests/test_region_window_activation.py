"""Region translation must not implicitly present the text translator."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kizurium_translator import capture
from kizurium_translator.desktop import DesktopController


@pytest.fixture
def controller():
    c = DesktopController.__new__(DesktopController)
    c.live = None
    c.closing = c.pending_start = c._starting = c.selecting = False
    c.stopping = None
    c.text_window = None
    c.geometry = '0,0 100x100'
    c.message = Mock()
    c.sync_auto = Mock()
    return c


@pytest.mark.parametrize('active', ['live', 'pending_start', '_starting', 'selecting', 'closing'])
def test_incidental_activation_does_not_open_window_during_region_work(controller, active):
    controller.dispatch = Mock()
    setattr(controller, active, object() if active == 'live' else True)
    controller.activate()
    controller.dispatch.assert_not_called()


def test_idle_activation_still_opens_the_translator(controller):
    controller.dispatch = Mock()
    controller.activate()
    controller.dispatch.assert_called_once_with('show-window')


def test_explicit_show_window_still_works_during_live_translation(controller):
    controller.live = object()
    controller.text_window = SimpleNamespace(present=Mock())
    controller.dispatch('show-window')
    controller.text_window.present.assert_called_once_with()


@pytest.mark.parametrize('success', [True, False])
def test_capture_preparation_never_creates_or_hides_a_text_window(controller, monkeypatch, success):
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'KDE')
    monkeypatch.setattr(capture, 'portal_ready', lambda: False)
    monkeypatch.setattr(capture, 'set_portal_parent', Mock())
    def capture_frame(_):
        if not success:
            raise capture.CaptureError('denied')
    monkeypatch.setattr(capture, 'kde_capture', capture_frame)
    jobs = []
    controller.background = lambda work, done: jobs.append((work, done))
    controller._start_live_now = Mock()
    # An already-visible text window must also retain its visibility.
    text = SimpleNamespace(present=Mock(), set_visible=Mock())
    controller.text_window = text
    controller.start_live()
    assert controller.pending_start and controller._starting
    controller.start_live()  # repeated start while consent is pending is idempotent
    assert len(jobs) == 1
    work, done = jobs.pop()
    done(work())
    assert not controller.pending_start and not controller._starting
    assert controller.text_window is text
    text.present.assert_not_called()
    text.set_visible.assert_not_called()
    assert controller._start_live_now.call_count == int(success)
