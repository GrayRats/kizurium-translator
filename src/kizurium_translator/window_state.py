"""Remember ordinary GTK window sizes; Wayland placement stays with the compositor."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base and Path(base).is_absolute() else Path.home() / ".local/state") / "kizurium-translator/windows.json"


def _read() -> dict:
    try:
        path = _path()
        if path.stat().st_size > 8192:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _bounds(window) -> tuple[int, int]:
    try:
        display = window.get_display()
        surface = window.get_surface()
        monitor = display.get_monitor_at_surface(surface) if surface else None
        if monitor is None:
            monitors = display.get_monitors()
            monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor:
            geometry = monitor.get_geometry()
            return max(1, geometry.width), max(1, geometry.height)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return 1920, 1080


def _fullscreen(window) -> bool:
    return bool(getattr(window, "is_fullscreen", lambda: False)())


def _track_normal_size(window) -> None:
    """Cache normal allocations in memory; allocation events never write state."""
    if getattr(window, "_kizurium_size_tracking", False):
        return
    window._kizurium_size_tracking = True
    width, height = window.get_default_size()
    if width > 0 and height > 0:
        window._kizurium_normal_size = (width, height)

    def on_layout(_surface, width, height):
        if width > 0 and height > 0 and not window.is_maximized() and not _fullscreen(window):
            window._kizurium_normal_size = (width, height)

    def on_realize(widget):
        surface = widget.get_surface()
        if surface is not None:
            surface.connect("layout", on_layout)

    window.connect("realize", on_realize)
    if window.get_realized():
        on_realize(window)


def restore(window, key: str) -> None:
    """Restore bounded dimensions and track the last normal size in memory."""
    _track_normal_size(window)
    state = _read().get(key)
    if not isinstance(state, dict):
        return
    width, height = state.get("width"), state.get("height")
    if type(width) is int and type(height) is int and width > 0 and height > 0:
        bound_w, bound_h = _bounds(window)
        size = min(bound_w, max(320, width)), min(bound_h, max(240, height))
        window.set_default_size(*size)
        window._kizurium_normal_size = size
    if state.get("maximized") is True:
        window.maximize()


def remember(window, key: str) -> None:
    """Persist on close/shutdown, never on each allocation notification."""
    temporary = None
    try:
        states = _read()
        maximized = bool(window.is_maximized())
        width, height = window.get_width(), window.get_height()
        previous = states.get(key, {})
        # Maximized allocations are not the user's normal window dimensions.
        if maximized or _fullscreen(window):
            width, height = window.get_default_size()
            if isinstance(previous, dict):
                width = previous.get("width", width)
                height = previous.get("height", height)
            width, height = getattr(window, "_kizurium_normal_size", (width, height))
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            return
        bound_w, bound_h = _bounds(window)
        states[key] = {"width": min(width, bound_w), "height": min(height, bound_h), "maximized": maximized}
        states = {name: states[name] for name in ("settings", "translator") if name in states}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".windows-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(states, stream)
            stream.write("\n")
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
