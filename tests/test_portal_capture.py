"""Portal capture: what happens when the shared source does not fit the region."""

from __future__ import annotations

import pytest

from kizurium_translator import capture


def make_session(tmp_path, monkeypatch, streams):
    session = capture._PortalSession()
    session.streams = streams
    token = tmp_path / "token"
    token.write_text("remembered")
    monkeypatch.setattr(session, "_token_file", lambda: token)
    return session, token


def test_a_source_smaller_than_the_region_is_refused_and_the_choice_is_reset(tmp_path, monkeypatch):
    stream = {"pipe": None, "sink": None, "x": 0, "y": 0, "w": 967, "h": 107}
    session, token = make_session(tmp_path, monkeypatch, [stream])
    monkeypatch.setattr(session, "close", lambda: setattr(session, "streams", []))
    with pytest.raises(capture.CaptureError, match="967x107"):
        session.grab(100, 400, 800, 300)
    assert not token.exists(), "the remembered choice must be dropped so the dialog returns"
    assert session.streams == []


def test_a_region_with_no_stream_under_it_is_an_error_not_a_guess(tmp_path, monkeypatch):
    streams = [
        {"pipe": None, "sink": None, "x": 0, "y": 0, "w": 1920, "h": 1080},
        {"pipe": None, "sink": None, "x": 1920, "y": 0, "w": 1920, "h": 1080},
    ]
    session, _ = make_session(tmp_path, monkeypatch, streams)
    with pytest.raises(capture.CaptureError, match="not on any monitor"):
        session.grab(5000, 5000, 100, 100)


@pytest.mark.parametrize('source_type', [2, 4, None])
def test_saved_window_or_unknown_source_is_not_accepted_as_a_monitor(tmp_path, monkeypatch, source_type):
    session, token = make_session(tmp_path, monkeypatch, [])
    monkeypatch.setattr(session, 'close', lambda: None)
    props = {} if source_type is None else {'source_type': source_type}
    with pytest.raises(capture.CaptureError, match='not a monitor'):
        session._validate_sources([(42, props)], restored=True)
    assert not token.exists()


def test_saved_monitor_is_accepted_without_deleting_permission(tmp_path, monkeypatch):
    session, token = make_session(tmp_path, monkeypatch, [])
    session._validate_sources([(42, {'source_type': 1})], restored=True)
    assert token.exists()


def test_region_crossing_a_monitor_edge_is_not_stretched(tmp_path, monkeypatch):
    stream = {'pipe': None, 'sink': None, 'x': 0, 'y': 0, 'w': 2560, 'h': 1080}
    session, token = make_session(tmp_path, monkeypatch, [stream])
    with pytest.raises(capture.CaptureError, match='entirely within'):
        session.grab(2400, 0, 300, 100)
    assert token.exists()


def test_doctor_on_kde_requires_the_portal_not_grim(monkeypatch, capsys):
    from kizurium_translator import cli

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    cli.main(["--doctor"])
    out = capsys.readouterr().out
    assert "ScreenCast portal" in out
    grim = next(line for line in out.splitlines() if " grim " in line)
    assert "OK" in grim or "не нужен" in grim or "MISSING" not in grim.split("grim")[0]
