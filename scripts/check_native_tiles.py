#!/usr/bin/env python3
"""Isolated synthetic tile parity/timing; never imports OCR, GTK, capture or live.

Build/install native first. Runs the production helper bodies extracted by AST,
including ImageChops and the image-to-bytes conversion in measured dispatch.
"""
from __future__ import annotations

import ast
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image, ImageChops, ImageStat

from kizurium_translator import native


def load_helpers(reducer):
    path = ROOT / "src/kizurium_translator/live/change.py"
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    names = {"_tile_stats", "dirty_tile_coords", "tile_diff_metrics"}
    namespace = {
        "Image": Image,
        "ImageChops": ImageChops,
        "ImageStat": ImageStat,
        "PROBE_TILE_SIZE": 16,
        "DIRTY_PIXEL_THRESHOLD": 24.0,
        "DIRTY_FRACTION": 0.015,
        "tile_reductions": reducer,
    }
    nodes = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def parity():
    if native._reduce is None:
        raise RuntimeError("Build and install native/ before running native parity checks")
    reference = load_helpers(lambda *args, **kwargs: None)
    accelerated = load_helpers(native.tile_reductions)
    rng = np.random.default_rng(17)
    cases = 0
    for width, height in [(1, 1), (3, 2), (17, 31), (320, 180), (0, 8), (8, 0)]:
        for tile in [1, 4, 16, 1000]:
            a = Image.fromarray(rng.integers(0, 256, (height, width), dtype=np.uint8))
            b = Image.fromarray(rng.integers(0, 256, (height, width), dtype=np.uint8))
            diff = ImageChops.difference(a, b)
            for threshold in [-1.0, 0.0, 24.0, 24.9999999, 25.0, 255.0, float("nan"), float("inf")]:
                kwargs = {"tile_size": tile, "pixel_threshold": threshold}
                assert reference["_tile_stats"](diff, **kwargs) == accelerated["_tile_stats"](diff, **kwargs)
                assert reference["dirty_tile_coords"](a, b, **kwargs) == accelerated["dirty_tile_coords"](a, b, **kwargs)
                cases += 1
    # Explicit strict comparison and float32 scalar-rounding boundary.
    diff = Image.new("L", (5, 3), 25)
    assert accelerated["_tile_stats"](diff, tile_size=4, pixel_threshold=24.9999999) == (25.0, 0.0)
    # Unequal probes keep their established sentinel / empty behavior.
    a, b = Image.new("L", (5, 3)), Image.new("L", (4, 3))
    assert accelerated["tile_diff_metrics"](a, b) == (999.0, 1.0)
    assert accelerated["dirty_tile_coords"](a, b) == set()
    return cases


def measure(function, repeats=30):
    for _ in range(3):
        function()
    values = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        function()
        values.append((time.perf_counter_ns() - start) / 1e6)
    return {"median_ms": statistics.median(values), "min_ms": min(values)}


def main():
    cases = parity()
    reference = load_helpers(lambda *args, **kwargs: None)
    accelerated = load_helpers(native.tile_reductions)
    rng = np.random.default_rng(17)
    a = Image.fromarray(rng.integers(0, 256, (180, 320), dtype=np.uint8))
    b = Image.fromarray(rng.integers(0, 256, (180, 320), dtype=np.uint8))
    diff = ImageChops.difference(a, b)
    timings = {}
    for label, helpers in [("python", reference), ("native", accelerated)]:
        timings[label] = {
            "tile_stats": measure(lambda: helpers["_tile_stats"](diff, tile_size=16, pixel_threshold=24.0)),
            "dirty_tiles": measure(lambda: helpers["dirty_tile_coords"](a, b)),
        }
    print(json.dumps({"synthetic_only": True, "size": [320, 180], "parity_cases": cases, "timings": timings}, indent=2))


if __name__ == "__main__":
    main()
