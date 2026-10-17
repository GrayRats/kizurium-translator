"""Optional native tile reductions with a complete Python fallback at the caller.

Only L-mode probes and ordinary Python scalar thresholds use the extension.
Import/ABI failures disable the helper; errors in an available kernel propagate.
"""
from __future__ import annotations

import os

try:
    from kizurium_native import tile_reductions as _reduce
except (ImportError, OSError):
    _reduce = None


def tile_reductions(diff, *, tile_size: int, pixel_threshold: float):
    """Return row-major (mean, fraction) tiles, or None to use existing Python."""
    if (
        _reduce is None
        or os.environ.get("KIZURIUM_NATIVE", "1") == "0"
        or diff.mode != "L"
        or type(pixel_threshold) not in (int, float)
        or (type(pixel_threshold) is int and pixel_threshold.bit_length() > 63)
    ):
        return None
    try:
        import numpy  # noqa: F401
    except ImportError:
        # The original NumPy-less path uses a different mean-based fraction.
        return None
    width, height = diff.size
    tile = min(max(width, height, 4), max(4, int(tile_size)))
    return _reduce(diff.tobytes(), width, height, tile, pixel_threshold)
