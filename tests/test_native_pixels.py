"""Parity and boundary validation for the optional Rust pixel kernels."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("check_native_pixels", ROOT / "scripts/check_native_pixels.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
native = check.native
requires_native = pytest.mark.skipif(
    native._ink_span is None or native._outside_features is None,
    reason="native 0.2 extension not installed",
)


@requires_native
def test_synthetic_pixel_parity():
    assert check.parity() == 459


@requires_native
@pytest.mark.parametrize("data,width,height", [(b"", 1, 1), (b"x", 0, 0), (b"", sys.maxsize, 2)])
def test_ink_rejects_invalid_dimensions(data, width, height):
    with pytest.raises(ValueError):
        native._ink_span(data, width, height, 140.0)


@requires_native
@pytest.mark.parametrize("data,mask,width,height,tile", [
    (b"", b"", 1, 1, 4), (b"x", b"", 1, 1, 4),
    (b"x", b"\x02", 1, 1, 4), (b"x", b"\x01", 1, 1, 0),
    (b"", b"", sys.maxsize, sys.maxsize, 4),
])
def test_features_reject_invalid_buffers_and_parameters(data, mask, width, height, tile):
    with pytest.raises(ValueError):
        native._outside_features(data, mask, width, height, 40.0, tile)


@requires_native
def test_mutable_buffers_cannot_be_borrowed_during_detached_work():
    with pytest.raises(TypeError):
        native._ink_span(bytearray(1), 1, 1, 140.0)
    with pytest.raises(TypeError):
        native._outside_features(b"x", bytearray(1), 1, 1, 40.0, 4)


@requires_native
def test_empty_native_buffers():
    assert native._ink_span(b"", 0, 7, 140.0) == 0
    assert native._outside_features(b"", b"", 0, 7, 40.0, 4) == (0, 0, 0.0, 0.0)


def test_disabled_or_missing_kernels_preserve_python_results(monkeypatch):
    a = Image.new("L", (17, 19), 200)
    b = Image.new("L", (17, 19), 0)
    reference = check.load_helpers(False)
    accelerated = check.load_helpers(True)
    for disabled in [True, False]:
        monkeypatch.setenv("KIZURIUM_NATIVE", "0" if disabled else "1")
        if not disabled:
            monkeypatch.setattr(native, "_ink_span", None)
            monkeypatch.setattr(native, "_outside_features", None)
        assert accelerated["estimate_ink_height"](b) == reference["estimate_ink_height"](b)
        assert asdict(accelerated["measure_outside_change"](a, b, [], 17, 19)) == asdict(
            reference["measure_outside_change"](a, b, [], 17, 19))


def test_unsupported_image_or_mask_uses_existing_path():
    gray = Image.new("L", (2, 2))
    assert native.ink_row_span(gray.convert("RGB"), 140.0) is None
    assert native.outside_change_features(gray, np.zeros((2, 2), dtype=np.uint8),
                                          edge_span=40.0, tile_size=4) is None


def test_kernel_errors_are_not_hidden(monkeypatch):
    def broken(*args):
        raise ValueError("kernel failure")
    monkeypatch.setenv("KIZURIUM_NATIVE", "1")
    monkeypatch.setattr(native, "_ink_span", broken)
    monkeypatch.setattr(native, "_outside_features", broken)
    with pytest.raises(ValueError, match="kernel failure"):
        native.ink_row_span(Image.new("L", (2, 2)), 140.0)
    with pytest.raises(ValueError, match="kernel failure"):
        native.outside_change_features(Image.new("L", (2, 2)), np.ones((2, 2), dtype=bool),
                                       edge_span=40.0, tile_size=4)


def test_array_probes_retain_the_existing_numpy_path():
    reference, accelerated = check.load_helpers(False), check.load_helpers(True)
    a = np.zeros((7, 9), dtype=np.uint8)
    b = np.full((7, 9), 80, dtype=np.uint8)
    assert asdict(accelerated["measure_outside_change"](a, b, [], 9, 7)) == asdict(
        reference["measure_outside_change"](a, b, [], 9, 7))
