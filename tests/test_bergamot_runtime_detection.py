"""Bindings outside the app venv can use the existing isolated worker."""
from pathlib import Path

from kizurium_translator.translation import bergamot as b


def test_system_runtime_selected_when_app_and_private_runtime_missing(monkeypatch):
    monkeypatch.setattr(b, 'in_process_available', lambda: False)
    monkeypatch.setattr(b, 'helper_python', lambda: None)
    monkeypatch.setattr(b, 'system_python', lambda: Path('/usr/bin/python3'))
    monkeypatch.setattr(b, 'validate_files', lambda *args: ({}, None))
    calls = []
    monkeypatch.setattr(b, 'HelperRuntime', lambda root, python: calls.append((root, python)))
    assert b.runtime_kind() == 'system'
    b.load_runtime(Path('/model'), 'en', 'ru')
    assert calls == [(Path('/model'), Path('/usr/bin/python3'))]


def test_private_install_without_marker_is_detected(tmp_path, monkeypatch):
    python = tmp_path / 'venv/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(b, 'runtime_dir', lambda: tmp_path)
    monkeypatch.setattr(b, '_probe_python', lambda *args: True)
    assert b.helper_python() == python


def test_broken_private_binding_is_unavailable(tmp_path, monkeypatch):
    python = tmp_path / 'venv/bin/python'
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / 'ok').touch()
    monkeypatch.setattr(b, 'runtime_dir', lambda: tmp_path)
    monkeypatch.setattr(b, '_probe_python', lambda *args: False)
    assert b.helper_python() is None
