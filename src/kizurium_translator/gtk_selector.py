"""GTK4 + layer-shell region selector.

Run by :mod:`kizurium_translator.selector` as a separate process (a layer-shell
surface cannot be created from the application's worker threads)::

    python -m kizurium_translator.gtk_selector REQUEST.json

The request file and the answer file are the ones ``selector.py`` always used:
the answer is ``{"action", "geometry", "output", "done"}``. Nothing is captured
here. The screen is dimmed through a transparent surface and the selection is
cut out of the dimming, so the same code works on every compositor that has
wlr-layer-shell, KWin included, with no screenshot permission involved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MIN_SIDE = 10
TOOLBAR_W, TOOLBAR_H = 4 * 36 + 20, 120
HANDLE_MARGIN = 24

Rect = tuple[float, float, float, float]  # x, y, w, h in the monitor's own space


# ------------------------------------------------------------- pure geometry


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize(x0: float, y0: float, x1: float, y1: float, bounds: tuple[float, float]) -> Rect:
    """Rectangle spanned by two points, kept inside the monitor."""
    w, h = bounds
    left, right = sorted((clamp(x0, 0, w), clamp(x1, 0, w)))
    top, bottom = sorted((clamp(y0, 0, h), clamp(y1, 0, h)))
    return left, top, right - left, bottom - top


def is_valid(rect: Rect | None) -> bool:
    return rect is not None and rect[2] >= MIN_SIDE and rect[3] >= MIN_SIDE


def hit_mode(rect: Rect | None, mx: float, my: float, margin: float = HANDLE_MARGIN) -> str:
    """What a press at (mx, my) does: start a new box, move, or resize an edge."""
    if not is_valid(rect):
        return "new"
    x, y, w, h = rect  # type: ignore[misc]
    on_l, on_r = abs(mx - x) <= margin, abs(mx - (x + w)) <= margin
    on_t, on_b = abs(my - y) <= margin, abs(my - (y + h)) <= margin
    in_w = x - margin <= mx <= x + w + margin
    in_h = y - margin <= my <= y + h + margin
    if on_t and on_l:
        return "nw"
    if on_t and on_r:
        return "ne"
    if on_b and on_l:
        return "sw"
    if on_b and on_r:
        return "se"
    if on_t and in_w:
        return "n"
    if on_b and in_w:
        return "s"
    if on_l and in_h:
        return "w"
    if on_r and in_h:
        return "e"
    if x <= mx <= x + w and y <= my <= y + h:
        return "move"
    return "new"


def apply_drag(mode: str, start: Rect, origin: tuple[float, float], dx: float, dy: float,
               bounds: tuple[float, float]) -> Rect:
    """The rectangle after dragging ``dx, dy`` from the press at ``origin``."""
    x, y, w, h = start
    if mode == "new":
        return normalize(origin[0], origin[1], origin[0] + dx, origin[1] + dy, bounds)
    if mode == "move":
        return (clamp(x + dx, 0, bounds[0] - w), clamp(y + dy, 0, bounds[1] - h), w, h)
    left, top, right, bottom = x, y, x + w, y + h
    if "w" in mode:
        left += dx
    if "e" in mode:
        right += dx
    if "n" in mode:
        top += dy
    if "s" in mode:
        bottom += dy
    return normalize(left, top, right, bottom, bounds)


def toolbar_position(rect: Rect, mon_w: float, mon_h: float,
                     tw: float = TOOLBAR_W, th: float = TOOLBAR_H) -> tuple[int, int]:
    """Under the selection; above it when it does not fit; clamped to the output."""
    x, y, w, h = rect
    px = clamp(x + w / 2 - tw / 2, 10, max(10, mon_w - tw - 10))
    if y + h + th + 15 <= mon_h:
        py = y + h + 15
    elif y - th - 15 >= 0:
        py = y - th - 15
    else:
        py = mon_h - th - 15
    return int(px), int(py)


def to_geometry(rect: Rect, mon_x: int, mon_y: int) -> str:
    """``"x,y WxH"`` in layout coordinates, as ``--region`` expects."""
    x, y, w, h = rect
    return f"{round(x) + mon_x},{round(y) + mon_y} {round(w)}x{round(h)}"


def from_geometry(geom: str, mon_x: int, mon_y: int, bounds: tuple[float, float]) -> Rect | None:
    """A remembered region in monitor space, or None when it does not fit here."""
    try:
        pos, size = geom.split(" ")
        gx, gy = (int(v) for v in pos.split(","))
        gw, gh = (int(v) for v in size.split("x"))
    except ValueError:
        return None
    rect = (gx - mon_x, gy - mon_y, gw, gh)
    if rect[0] < 0 or rect[1] < 0 or rect[0] + gw > bounds[0] or rect[1] + gh > bounds[1]:
        return None
    return rect if is_valid(rect) else None


CURSORS = {
    "new": "crosshair", "move": "move", "n": "n-resize", "s": "s-resize", "e": "e-resize",
    "w": "w-resize", "nw": "nw-resize", "ne": "ne-resize", "sw": "sw-resize", "se": "se-resize",
}

CSS = b"""
window.kz-selector { background: transparent; }
.kz-panel { background-color: #1e1e2e; border-radius: 16px; border: 1px solid rgba(205,214,244,0.08); }
.kz-tool { background: #313244; color: #cdd6f4; border: none; border-radius: 8px;
           min-width: 36px; min-height: 36px; padding: 0; }
.kz-tool:hover { background: #3b3d52; }
.kz-close { background: #f38ba8; color: #11111b; }
.kz-close:hover { background: #f5a3ba; }
.kz-live { background: transparent; border: 2px solid rgba(88,91,112,0.8); border-radius: 28px;
           min-width: 52px; min-height: 52px; padding: 0; }
.kz-live-dot { background: #b4befe; border-radius: 18px; min-width: 36px; min-height: 36px; }
.kz-live:hover .kz-live-dot { background: #cbd2ff; }
.kz-line { background: rgba(205,214,244,0.10); border-radius: 2px; min-height: 4px; }
.kz-gear { background: transparent; color: rgba(205,214,244,0.55); border: none; padding: 0;
           min-width: 32px; min-height: 32px; }
.kz-gear:hover { color: #cdd6f4; }
"""


# ------------------------------------------------------------------- GTK part


def _load_gtk():
    # Before Gtk loads: gtk4-layer-shell has to be mapped ahead of libwayland.
    from .layer_shell_lib import preload

    preload()
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gdk, GLib, Gtk
    from gi.repository import Gtk4LayerShell as LayerShell

    return Gtk, Gdk, GLib, LayerShell


class Selector:
    def __init__(self, request: dict, Gtk, Gdk, GLib, LayerShell) -> None:
        self.Gtk, self.Gdk, self.GLib, self.LayerShell = Gtk, Gdk, GLib, LayerShell
        self.request = request
        self.auto = str(request.get("autoAction") or "")
        self.remembered = request.get("geometries") or {}
        self.windows: list[SelectorWindow] = []
        self.owner: SelectorWindow | None = None
        self.done = False
        self.app = Gtk.Application(application_id=None)
        self.app.connect("activate", self.on_activate)

    def run(self) -> int:
        code = self.app.run([])
        self.finish("")  # a window closed from outside is a cancel
        return code

    def on_activate(self, app) -> None:
        Gtk, Gdk, GLib = self.Gtk, self.Gdk, self.GLib
        if not self.LayerShell.is_supported():
            print("error: compositor does not support wlr-layer-shell", file=sys.stderr)
            self.finish("")
            app.quit()
            return
        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            self.windows.append(SelectorWindow(self, app, monitors.get_item(i)))
        for win in self.windows:
            win.restore()
        for win in self.windows:
            win.present()
        watchdog = int(self.request.get("watchdogMs") or 0)
        if watchdog:
            GLib.timeout_add(watchdog, lambda: self.finish("") or False)
        GLib.timeout_add(100, self.poll_cancel)

    def poll_cancel(self) -> bool:
        path = Path(str(self.request.get("cancel") or ""))
        if str(path) and path.exists():
            try:
                path.unlink()
            except OSError:
                pass
            self.finish("")
            return False
        return not self.done

    def claim(self, win: SelectorWindow) -> None:
        """One output owns the selection; the others show only the dimming."""
        self.owner = win
        for other in self.windows:
            if other is not win:
                other.rect = None
            other.refresh()

    def default_action(self) -> str:
        return self.auto or "live"

    def finish(self, action: str) -> None:
        if self.done:
            return
        owner = self.owner
        rect = owner.rect if owner else None
        if action and not is_valid(rect):
            action = ""  # a button press with no box behind it is a cancel
        geometry = output = ""
        if action and owner and rect:
            geometry = to_geometry(rect, owner.mon_x, owner.mon_y)
            output = owner.key
        self.done = True
        try:
            Path(str(self.request["result"])).write_text(
                json.dumps({"action": action, "geometry": geometry, "output": output, "done": True}),
                encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write result: {exc}", file=sys.stderr)
        self.app.quit()


class SelectorWindow:
    def __init__(self, sel: Selector, app, monitor) -> None:
        Gtk, Gdk, LayerShell = sel.Gtk, sel.Gdk, sel.LayerShell
        self.sel, self.monitor = sel, monitor
        geo = monitor.get_geometry()
        self.mon_x, self.mon_y, self.w, self.h = geo.x, geo.y, geo.width, geo.height
        self.key = f"{monitor.get_connector() or '?'}@{self.w}x{self.h}"
        self.rect: Rect | None = None
        self.maximized_from: Rect | None = None
        self.mode = "new"
        self.start: Rect = (0, 0, 0, 0)
        self.origin = (0.0, 0.0)
        self.dragging = False

        self.win = Gtk.ApplicationWindow(application=app)
        self.win.add_css_class("kz-selector")
        LayerShell.init_for_window(self.win)
        LayerShell.set_monitor(self.win, monitor)
        LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self.win, edge, True)
        LayerShell.set_exclusive_zone(self.win, -1)
        LayerShell.set_namespace(self.win, "kizurium-selector")
        LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.EXCLUSIVE)

        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_draw_func(self.draw)
        overlay = Gtk.Overlay()
        overlay.set_child(self.area)
        self.panel = self.build_panel()
        self.panel.set_halign(Gtk.Align.START)
        self.panel.set_valign(Gtk.Align.START)
        self.panel.set_visible(False)
        overlay.add_overlay(self.panel)
        self.win.set_child(overlay)

        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        drag.connect("drag-end", self.on_end)
        self.area.add_controller(drag)
        cancel = Gtk.GestureClick.new()
        cancel.set_button(3)
        cancel.connect("pressed", lambda *_: sel.finish(""))
        self.area.add_controller(cancel)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self.on_motion)
        self.area.add_controller(motion)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)
        self.cursor = Gdk.Cursor

    # -- layout --------------------------------------------------------------
    def build_panel(self):
        Gtk = self.sel.Gtk
        panel = Gtk.Fixed()
        panel.add_css_class("kz-panel")
        panel.set_size_request(TOOLBAR_W, TOOLBAR_H)

        def tool(icon: str, tip: str, action, css: str = ""):
            button = Gtk.Button.new_from_icon_name(icon)
            button.add_css_class("kz-tool")
            if css:
                button.add_css_class(css)
            button.set_tooltip_text(tip)
            button.connect("clicked", lambda _b: action())
            return button

        self.max_button = tool("view-fullscreen-symbolic", "Весь экран", self.toggle_maximize)
        row = (
            tool("edit-copy-symbolic", "Копировать текст (OCR)", lambda: self.sel.finish("ocr")),
            tool("preferences-desktop-locale-symbolic", "Окно перевода", lambda: self.sel.finish("text")),
            self.max_button,
            tool("window-close-symbolic", "Отмена", lambda: self.sel.finish(""), "kz-close"),
        )
        for i, button in enumerate(row):
            panel.put(button, 10 + 36 * i, 10)

        line = Gtk.Box()
        line.add_css_class("kz-line")
        line.set_size_request(14, 4)
        panel.put(line, 24, 120 - 12 - 28 - 2)
        live = Gtk.Button()
        live.add_css_class("kz-live")
        dot = Gtk.Box()
        dot.add_css_class("kz-live-dot")
        dot.set_halign(Gtk.Align.CENTER)
        dot.set_valign(Gtk.Align.CENTER)
        live.set_child(dot)
        live.set_tooltip_text("Автоперевод (Enter)")
        live.connect("clicked", lambda _b: self.sel.finish("live"))
        panel.put(live, (TOOLBAR_W - 56) // 2, 120 - 12 - 56)
        gear = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        gear.add_css_class("kz-gear")
        gear.set_tooltip_text("Settings / Настройки")
        gear.connect("clicked", lambda _b: self.sel.finish("settings"))
        panel.put(gear, TOOLBAR_W - 54 + 11, 120 - 12 - 44)
        return panel

    def restore(self) -> None:
        if self.sel.owner is not None:
            return
        remembered = self.sel.remembered.get(self.key)
        rect = from_geometry(remembered, self.mon_x, self.mon_y, (self.w, self.h)) if remembered else None
        if rect:
            self.rect = rect
            self.sel.claim(self)

    def present(self) -> None:
        self.win.present()
        self.refresh()

    def refresh(self) -> None:
        self.area.queue_draw()
        show = (is_valid(self.rect) and not self.dragging and not self.sel.auto
                and self.sel.owner is self)
        self.panel.set_visible(bool(show))
        if show:
            px, py = toolbar_position(self.rect, self.w, self.h)  # type: ignore[arg-type]
            self.panel.set_margin_start(px)
            self.panel.set_margin_top(py)

    # -- drawing -------------------------------------------------------------
    def draw(self, _area, cr, _w, _h) -> None:
        import cairo

        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()
        if not self.rect:
            return
        x, y, rw, rh = self.rect
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.rectangle(x, y, rw, rh)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_rgba(0.706, 0.745, 0.996, 1)
        cr.set_line_width(2)
        cr.rectangle(x, y, rw, rh)
        cr.stroke()
        if not self.sel.auto:
            for hx in (x, x + rw / 2, x + rw):
                for hy in (y, y + rh / 2, y + rh):
                    if (hx, hy) != (x + rw / 2, y + rh / 2):
                        cr.rectangle(hx - 3, hy - 3, 6, 6)
            cr.fill()

    # -- input ---------------------------------------------------------------
    def on_motion(self, _ctl, x, y) -> None:
        mode = hit_mode(self.rect if self.sel.owner is self else None, x, y)
        self.area.set_cursor_from_name(CURSORS[mode])

    def on_begin(self, _g, x, y) -> None:
        owner_rect = self.rect if self.sel.owner is self else None
        self.mode = hit_mode(owner_rect, x, y)
        self.origin = (x, y)
        self.start = owner_rect or (x, y, 0, 0)
        if self.mode == "new":
            self.sel.claim(self)
            self.rect = (x, y, 0, 0)
        self.dragging = True
        self.refresh()

    def on_update(self, _g, dx, dy) -> None:
        self.rect = apply_drag(self.mode, self.start, self.origin, dx, dy, (self.w, self.h))
        self.area.queue_draw()

    def on_end(self, _g, dx, dy) -> None:
        self.rect = apply_drag(self.mode, self.start, self.origin, dx, dy, (self.w, self.h))
        self.dragging = False
        self.refresh()
        if self.sel.auto and is_valid(self.rect):
            self.sel.finish(self.sel.auto)

    def toggle_maximize(self) -> None:
        if self.maximized_from is not None:
            self.rect, self.maximized_from = self.maximized_from, None
            self.max_button.set_icon_name("view-fullscreen-symbolic")
        else:
            self.maximized_from = self.rect
            self.rect = (0, 0, self.w, self.h)
            self.max_button.set_icon_name("view-restore-symbolic")
        self.refresh()

    def on_key(self, _ctl, keyval, _code, _state) -> bool:
        Gdk = self.sel.Gdk
        if keyval == Gdk.KEY_Escape:
            self.sel.finish("")
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.sel.owner is self:
            self.sel.finish(self.sel.default_action())
            return True
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m kizurium_translator.gtk_selector REQUEST.json", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        Gtk, Gdk, GLib, LayerShell = _load_gtk()
    except Exception as exc:  # noqa: BLE001 - reported to the caller's log
        print(f"error: selector cannot start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return Selector(request, Gtk, Gdk, GLib, LayerShell).run()


if __name__ == "__main__":
    raise SystemExit(main())
