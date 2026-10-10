"""The GTK selector: selection geometry and the guarantees the overlay relies on.

The geometry functions are pure, so they are tested directly. What needs a real
compositor (the surfaces themselves) is not exercised here.
"""

from __future__ import annotations

import json
from pathlib import Path

from kizurium_translator import gtk_selector as gs

BOUNDS = (1000.0, 600.0)
SOURCE = Path(gs.__file__).read_text(encoding="utf-8")


class TestGeometry:
    def test_new_box_is_normalized_whatever_the_drag_direction(self):
        assert gs.apply_drag("new", (0, 0, 0, 0), (300, 200), -100, -50, BOUNDS) == (200, 150, 100, 50)

    def test_new_box_is_clamped_to_the_output(self):
        x, y, w, h = gs.apply_drag("new", (0, 0, 0, 0), (900, 500), 400, 400, BOUNDS)
        assert (x + w, y + h) == BOUNDS

    def test_move_keeps_the_size_and_stays_inside(self):
        assert gs.apply_drag("move", (100, 100, 200, 100), (0, 0), 5000, -5000, BOUNDS) == (800, 0, 200, 100)

    def test_edge_resize_moves_only_that_edge(self):
        assert gs.apply_drag("e", (100, 100, 200, 100), (0, 0), 50, 999, BOUNDS) == (100, 100, 250, 100)
        assert gs.apply_drag("n", (100, 100, 200, 100), (0, 0), 999, -40, BOUNDS) == (100, 60, 200, 140)

    def test_resizing_past_the_opposite_edge_flips_instead_of_going_negative(self):
        assert gs.apply_drag("e", (100, 100, 200, 100), (0, 0), -300, 0, BOUNDS) == (0, 100, 100, 100)

    def test_hit_modes(self):
        rect = (200, 200, 300, 200)
        assert gs.hit_mode(None, 5, 5) == "new"
        assert gs.hit_mode(rect, 350, 300) == "move"
        assert gs.hit_mode(rect, 202, 202) == "nw"
        assert gs.hit_mode(rect, 498, 398) == "se"
        assert gs.hit_mode(rect, 350, 203) == "n"
        assert gs.hit_mode(rect, 900, 550) == "new"

    def test_a_tiny_box_is_not_a_selection(self):
        assert not gs.is_valid((10, 10, 9, 200))
        assert gs.is_valid((10, 10, 10, 10))
        assert gs.hit_mode((10, 10, 5, 5), 12, 12) == "new"

    def test_toolbar_sits_under_the_selection_when_it_fits(self):
        px, py = gs.toolbar_position((300, 100, 200, 100), 1000, 600)
        assert py == 100 + 100 + 15
        assert 10 <= px <= 1000 - gs.TOOLBAR_W - 10

    def test_toolbar_moves_above_when_there_is_no_room_below(self):
        _, py = gs.toolbar_position((300, 400, 200, 190), 1000, 600)
        assert py == 400 - gs.TOOLBAR_H - 15

    def test_toolbar_stays_on_the_output_when_neither_side_fits(self):
        _, py = gs.toolbar_position((0, 0, 1000, 600), 1000, 600)
        assert py == 600 - gs.TOOLBAR_H - 15

    def test_toolbar_is_clamped_horizontally(self):
        px, _ = gs.toolbar_position((960, 100, 40, 40), 1000, 600)
        assert px == 1000 - gs.TOOLBAR_W - 10

    def test_geometry_round_trips_through_the_layout_offset(self):
        rect = (120.4, 80.2, 640.0, 360.0)
        geom = gs.to_geometry(rect, 2560, 0)
        assert geom == "2680,80 640x360"
        assert gs.from_geometry(geom, 2560, 0, (2560, 1080)) == (120, 80, 640, 360)

    def test_a_remembered_region_that_does_not_fit_is_dropped(self):
        assert gs.from_geometry("2000,0 800x400", 0, 0, (1920, 1080)) is None
        assert gs.from_geometry("nonsense", 0, 0, (1920, 1080)) is None
        assert gs.from_geometry("0,0 4x4", 0, 0, (1920, 1080)) is None


class TestGuarantees:
    def test_nothing_is_captured(self):
        for word in ("grim", "ScreenShot2", "subprocess", "ScreenCast"):
            assert word not in SOURCE, word

    def test_layer_shell_is_preloaded_before_gtk_is_imported(self):
        load = SOURCE[SOURCE.index("def _load_gtk"):SOURCE.index("class Selector")]
        assert load.index("preload()") < load.index("require_version")

    def test_a_window_is_created_for_every_output(self):
        activate = SOURCE[SOURCE.index("def on_activate"):SOURCE.index("def poll_cancel")]
        assert "get_n_items()" in activate and "SelectorWindow(" in activate

    def test_an_unsupported_compositor_is_reported_not_faked(self):
        assert "is_supported()" in SOURCE
        assert "does not support wlr-layer-shell" in SOURCE

    def test_a_press_with_no_box_behind_it_is_a_cancel(self):
        finish = SOURCE[SOURCE.index("    def finish"):SOURCE.index("class SelectorWindow")]
        assert "is_valid(rect)" in finish

    def test_settings_button_is_in_the_toolbar_and_returns_settings(self):
        assert "preferences-system-symbolic" in SOURCE
        assert 'finish("settings")' in SOURCE
        assert "Settings / Настройки" in SOURCE

    def test_return_confirms_live_and_escape_cancels(self):
        assert 'return self.auto or "live"' in SOURCE
        assert "KEY_Escape" in SOURCE and "KEY_Return" in SOURCE

    def test_only_the_owning_output_shows_a_selection(self):
        claim = SOURCE[SOURCE.index("    def claim"):SOURCE.index("    def default_action")]
        assert "other.rect = None" in claim


class TestAnswerFile:
    """finish() writes the file selector.py reads; exercised without any GTK."""

    class FakeApp:
        def __init__(self):
            self.quit_calls = 0

        def quit(self):
            self.quit_calls += 1

    def make(self, tmp_path, rect, auto=""):
        sel = gs.Selector.__new__(gs.Selector)
        sel.request = {"result": str(tmp_path / "r.json"), "autoAction": auto}
        sel.app = self.FakeApp()
        sel.done = False
        sel.owner = type("W", (), {"rect": rect, "mon_x": 2560, "mon_y": 0, "key": "DP-2@1920x1080"})()
        return sel

    def test_action_geometry_and_output_are_written(self, tmp_path):
        sel = self.make(tmp_path, (10, 20, 300, 100))
        sel.finish("ocr")
        data = json.loads((tmp_path / "r.json").read_text())
        assert data == {"action": "ocr", "geometry": "2570,20 300x100", "output": "DP-2@1920x1080", "done": True}
        assert sel.app.quit_calls == 1

    def test_an_action_without_a_valid_box_becomes_a_cancel(self, tmp_path):
        sel = self.make(tmp_path, (10, 20, 3, 3))
        sel.finish("live")
        data = json.loads((tmp_path / "r.json").read_text())
        assert data["action"] == "" and data["geometry"] == ""

    def test_finishing_twice_writes_once(self, tmp_path):
        sel = self.make(tmp_path, (10, 20, 300, 100))
        sel.finish("ocr")
        sel.finish("")
        assert json.loads((tmp_path / "r.json").read_text())["action"] == "ocr"
        assert sel.app.quit_calls == 1


def test_main_without_a_request_file_is_a_usage_error(capsys):
    assert gs.main([]) == 2
    assert "usage" in capsys.readouterr().err
