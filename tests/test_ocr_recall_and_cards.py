"""Regressions found while restoring OCR recall on the 1080p game frames.

Each test pins one defect that was measured on real frames (test-game/EN) and then
fixed; the fixtures here are synthetic so they run anywhere.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from kizurium_translator.core import active
from kizurium_translator.core import text as core_text
from kizurium_translator.live.build import _split_words_by_weight
from kizurium_translator.live.prepare import drop_paragraph_echoes
from kizurium_translator.ocr import engine


class TestDetectorSide:
    """RapidOCR's detector shrinks anything larger than this: at 640 a 1080p frame loses its small text."""

    def _with(self, monkeypatch, options):
        current = active.Active()
        current.ocr_options = options
        monkeypatch.setattr(active, "_ACTIVE", current)

    def test_default_is_the_measured_better_value(self, monkeypatch):
        self._with(monkeypatch, {})
        assert engine.det_side_len() == 1280

    def test_fast_keeps_the_old_640(self, monkeypatch):
        self._with(monkeypatch, {"det_side_len": "fast"})
        assert engine.det_side_len() == engine.DET_SIDE_LEN == 640

    def test_explicit_value_is_clamped(self, monkeypatch):
        self._with(monkeypatch, {"det_side_len": 99999})
        assert engine.det_side_len() == 2560
        self._with(monkeypatch, {"det_side_len": 10})
        assert engine.det_side_len() == 320

    def test_garbage_falls_back_to_the_default(self, monkeypatch):
        self._with(monkeypatch, {"det_side_len": "banana"})
        assert engine.det_side_len() == engine.DET_SIDE_AUTO


class TestTranslationSplit:
    """A translation with no line breaks is shared between blocks by source length, not one word at a time."""

    def test_proportional_and_in_order(self):
        words = "a b c d e f g h i j".split()
        assert _split_words_by_weight(words, [1, 1]) == ["a b c d e", "f g h i j"]
        parts = _split_words_by_weight(words, [62, 111])
        assert " ".join(parts).split() == words
        assert 2 <= len(parts[0].split()) < len(parts[1].split())

    def test_every_block_gets_a_word(self):
        parts = _split_words_by_weight(["x", "y", "z"], [1, 1000, 1])
        assert all(p.split() for p in parts) and " ".join(parts) == "x y z"

    def test_an_orphan_word_is_not_stretched_into_a_block(self):
        # The defect: a 100-word paragraph in two blocks left the second with ONE word.
        words = [f"w{i}" for i in range(20)]
        counts = [len(p.split()) for p in _split_words_by_weight(words, [62, 95])]
        assert min(counts) >= 6


def _par(box, text, line_boxes=None):
    par = {"box": box, "text": text}
    if line_boxes is not None:
        par["line_boxes"] = line_boxes
    return par


class TestParagraphEcho:
    ENGLISH = "If you run out of things you can investigate talk to suspects in the holding cells"

    def test_a_line_that_is_a_piece_of_the_paragraph_is_dropped(self):
        paragraph = _par((10, 70, 310, 260), "NIGHT: 1 Buy film " + self.ENGLISH,
                         [{"box": (10, 70, 310, 140), "text": "NIGHT: 1 Buy film"},
                          {"box": (10, 160, 310, 260), "text": self.ENGLISH}])
        echo = _par((16, 169, 309, 259), self.ENGLISH)
        kept = drop_paragraph_echoes([paragraph, echo])
        assert kept == [paragraph]

    def test_the_same_line_read_with_a_letter_different_is_still_an_echo(self):
        # Measured: the paragraph read "ean investigate", the standalone line "can investigate".
        paragraph = _par((10, 70, 310, 260), "NIGHT: 1 " + self.ENGLISH.replace(" can ", " ean "),
                         [{"box": (10, 160, 310, 260), "text": self.ENGLISH.replace(" can ", " ean ")}])
        echo = _par((16, 169, 309, 259), self.ENGLISH)
        assert drop_paragraph_echoes([paragraph, echo]) == [paragraph]

    def test_a_different_line_in_the_same_place_is_kept(self):
        paragraph = _par((10, 70, 310, 260), "NIGHT: 1 Buy film " + self.ENGLISH,
                         [{"box": (10, 160, 310, 260), "text": self.ENGLISH}])
        other = _par((16, 169, 309, 259), "Completely different text that is long enough to matter here")
        assert len(drop_paragraph_echoes([paragraph, other])) == 2

    def test_short_labels_are_never_treated_as_echoes(self):
        paragraph = _par((0, 0, 300, 100), "Save Load Options Quit",
                         [{"box": (0, 0, 300, 100), "text": "Save Load Options Quit"}])
        assert len(drop_paragraph_echoes([paragraph, _par((0, 0, 60, 20), "Save")])) == 2

    def test_lines_elsewhere_are_untouched(self):
        paragraph = _par((10, 70, 310, 260), self.ENGLISH, [{"box": (10, 70, 310, 260), "text": self.ENGLISH}])
        far = _par((900, 800, 1200, 860), self.ENGLISH)
        assert len(drop_paragraph_echoes([paragraph, far])) == 2


def _panel(size, panel_rgb, ink_rgb, outside_rgb=None, seed=1):
    """A noisy panel with ink strokes; optionally a flat strip of another colour along the border."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.clip(np.array(panel_rgb, float) + rng.normal(0, 18, (h, w, 3)), 0, 255)
    for x in range(8, w - 8, 14):  # ink strokes
        img[h // 3: 2 * h // 3, x: x + 4] = np.clip(np.array(ink_rgb, float) + rng.normal(0, 6, (h // 3, 4, 3)), 0, 255)
    if outside_rgb is not None:
        # A thin flat rim (about 10% of the box), as on the real banner: thick enough to be the
        # single biggest colour bucket, too thin to count as "what surrounds the box".
        rim = max(1, h // 20)
        img[:rim] = outside_rgb
        img[-rim:] = outside_rgb
    return Image.fromarray(img.astype("uint8"))


class TestCoverColour:
    def test_textured_cream_banner_is_not_painted_black(self):
        # A flat black strip beside a noisy cream panel used to win as "the biggest colour".
        img = _panel((400, 60), (222, 208, 180), (20, 20, 20), outside_rgb=(1, 1, 1))
        bg, fg = core_text.sample_ocr_cover_colors(img, (0, 0, 400, 60))
        assert sum(bg[:3]) / 3 > 0.6, bg
        assert sum(fg[:3]) / 3 < 0.25, fg

    def test_bold_light_letters_on_a_dark_strip_keep_the_dark_panel(self):
        # Bold light letters can cover more of a small box than the dark strip behind them;
        # the brightness split must not hand the card to the letters.
        img = _panel((300, 40), (8, 4, 2), (180, 150, 120))
        arr = np.array(img)
        arr[10:30, 5:295] = np.clip(arr[10:30, 5:295].astype(int) + 140, 0, 255)  # letters dominate
        bg, _ = core_text.sample_ocr_cover_colors(Image.fromarray(arr.astype("uint8")), (0, 0, 300, 40))
        assert sum(bg[:3]) / 3 < 0.6, bg

    def test_flat_dark_panel_is_unchanged(self):
        img = _panel((300, 40), (10, 10, 12), (230, 230, 230))
        bg, fg = core_text.sample_ocr_cover_colors(img, (0, 0, 300, 40))
        assert sum(bg[:3]) / 3 < 0.2 and sum(fg[:3]) / 3 > 0.7
