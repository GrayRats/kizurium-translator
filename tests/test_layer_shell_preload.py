"""gtk4-layer-shell must be mapped before GTK, in every process that draws a layer.

If GTK loads first, init_for_window() fails ("GtkWindow is not a layer surface")
and the overlay opens as an ordinary window: wrong place, decorated, not click-through.
The application now runs as one GApplication process whose GTK comes from ui._gtk(),
so that is where the preload has to be.
"""

from __future__ import annotations

from pathlib import Path

from kizurium_translator import gtk_selector, ui


def _body(module, name):
    source = Path(module.__file__).read_text(encoding="utf-8")
    start = source.index(f"def {name}")
    return source[start:source.index("\ndef ", start + 1)]


def test_the_shared_gtk_import_preloads_layer_shell_first():
    body = _body(ui, "_gtk")
    assert "preload()" in body
    assert body.index("preload()") < body.index("import gi")


def test_the_selector_process_preloads_it_too():
    body = _body(gtk_selector, "_load_gtk")
    assert body.index("preload()") < body.index("import gi")
