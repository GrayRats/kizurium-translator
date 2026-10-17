"""Limited optional-kernel parity and FFI validation; no OCR/capture imports."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("check_native_tiles", ROOT / "scripts/check_native_tiles.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
native = check.native


@pytest.mark.skipif(native._reduce is None, reason="optional extension not installed")
def test_synthetic_tile_parity():
    assert check.parity() == 192


@pytest.mark.skipif(native._reduce is None, reason="optional extension not installed")
@pytest.mark.parametrize("data,width,height,tile", [(b"", 1, 1, 4), (b"x", 0, 0, 4), (b"", 0, 0, 0), (b"", sys.maxsize, sys.maxsize, 4)])
def test_invalid_native_buffers_and_dimensions(data, width, height, tile):
    with pytest.raises(ValueError):
        native._reduce(data, width, height, tile, 24.0)


def test_disabled_and_unsupported_inputs_use_python(monkeypatch):
    monkeypatch.setenv("KIZURIUM_NATIVE", "0")
    assert native.tile_reductions(Image.new("L", (4, 4)), tile_size=4, pixel_threshold=24.0) is None
    monkeypatch.delenv("KIZURIUM_NATIVE")
    assert native.tile_reductions(Image.new("RGB", (4, 4)), tile_size=4, pixel_threshold=24.0) is None


def test_available_kernel_errors_are_visible(monkeypatch):
    def broken(*_args):
        raise ValueError("invalid kernel input")
    monkeypatch.delenv("KIZURIUM_NATIVE", raising=False)
    monkeypatch.setattr(native, "_reduce", broken)
    with pytest.raises(ValueError, match="invalid kernel input"):
        native.tile_reductions(Image.new("L", (4, 4)), tile_size=4, pixel_threshold=24.0)


def test_missing_extension_keeps_existing_results(monkeypatch):
    reference = check.load_helpers(lambda *args, **kwargs: None)
    monkeypatch.setattr(native, "_reduce", None)
    helpers = check.load_helpers(native.tile_reductions)
    image = Image.new("L", (17, 7), 25)
    kwargs = {"tile_size": 4, "pixel_threshold": 24.0}
    assert helpers["_tile_stats"](image, **kwargs) == reference["_tile_stats"](image, **kwargs)


def test_missing_numpy_preserves_mean_based_fraction(monkeypatch):
    import builtins

    original_import = builtins.__import__
    def without_numpy(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("NumPy unavailable")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_numpy)
    monkeypatch.setattr(native, "_reduce", lambda *args: pytest.fail("native must remain disabled"))
    helpers = check.load_helpers(native.tile_reductions)
    assert helpers["_tile_stats"](Image.new("L", (5, 3), 25), tile_size=4, pixel_threshold=24.0) == (25.0, 25 / 48)


@pytest.mark.skipif(native._reduce is None, reason="optional extension not installed")
def test_mutable_buffer_is_rejected():
    with pytest.raises(TypeError):
        native._reduce(bytearray(16), 4, 4, 4, 24.0)
