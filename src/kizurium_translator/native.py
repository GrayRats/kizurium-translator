"""Optional pixel kernels with a complete Python fallback at each caller.

Only L-mode probes and ordinary Python scalar thresholds use the extension.
Import/ABI failures disable the helper; errors in an available kernel propagate.
"""
from __future__ import annotations

import os

try:
    import kizurium_native as _kernels
except (ImportError, OSError):
    _kernels = None

# Probe symbols independently: an older companion wheel can still reduce tiles.
_reduce = getattr(_kernels, "tile_reductions", None)
_ink_span = getattr(_kernels, "ink_row_span", None)
_outside_features = getattr(_kernels, "outside_change_features", None)


def _enabled(kernel):
    return kernel is not None and os.environ.get("KIZURIUM_NATIVE", "1") != "0"


def ink_row_span(gray, cutoff: float):
    """Return the ink span, or None when the existing Python scan is needed."""
    if not _enabled(_ink_span) or getattr(gray, "mode", None) != "L":
        return None
    return _ink_span(gray.tobytes(), *gray.size, cutoff)


def outside_change_features(gray, outside, *, edge_span: float, tile_size: int):
    """Reduce a caller-masked change array; masking and classification stay Python."""
    if not _enabled(_outside_features) or getattr(gray, "mode", None) != "L":
        return None
    width, height = gray.size
    # Only the production bool mask is supported. Rejecting other formats here
    # keeps their existing fallback semantics, including NumPy-less operation.
    if outside.shape != (height, width) or outside.dtype != bool:
        return None
    tile = min(max(width, height, 4), max(4, int(tile_size)))
    return _outside_features(gray.tobytes(), outside.tobytes(), width, height, edge_span, tile)


def tile_reductions(diff, *, tile_size: int, pixel_threshold: float):
    """Return row-major (mean, fraction) tiles, or None to use existing Python."""
    if (
        not _enabled(_reduce)
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
