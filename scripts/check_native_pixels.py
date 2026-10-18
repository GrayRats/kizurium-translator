#!/usr/bin/env python3
"""Synthetic parity/profile of production pixel helpers, including FFI copies.

Does not import GTK, capture or OCR engines. Build native/ in release mode first.
"""
from __future__ import annotations

import ast
import cProfile
import io
import json
import pstats
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kizurium_translator import native


def load_helpers(accelerated):
    namespace = {
        "dataclass": dataclass, "Image": Image, "ImageOps": ImageOps, "ImageStat": ImageStat,
        "CHANGE_PIXEL": 24, "CHANGE_AREA_MIN": 0.004, "CHANGE_EDGE_SPAN": 40,
        "PROBE_TILE_SIZE": 16,
        "outside_change_features": native.outside_change_features if accelerated else lambda *_args, **_kw: None,
        "ink_row_span": native.ink_row_span if accelerated else lambda *_args, **_kw: None,
    }
    for filename, names in [
        ("live/change.py", {"ChangeFeatures", "measure_outside_change"}),
        ("ocr/preprocess.py", {"estimate_ink_height"}),
    ]:
        path = ROOT / "src/kizurium_translator" / filename
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if getattr(n, "name", "") in names]
        exec(compile(ast.Module(nodes, []), str(path), "exec"), namespace)
    return namespace


def parity():
    if native._ink_span is None or native._outside_features is None:
        raise RuntimeError("Build/install the native 0.2 companion wheel first")
    reference, accelerated = load_helpers(False), load_helpers(True)
    rng = np.random.default_rng(27)
    cases = 0
    for width, height in [(1, 1), (1, 17), (19, 1), (2, 2), (17, 31), (320, 180)]:
        a = rng.integers(0, 256, (height, width), dtype=np.uint8)
        random = rng.integers(0, 256, (height, width), dtype=np.uint8)
        border = np.zeros_like(a)
        border[0, 0] = 255
        border[-1, -1] = 255
        for before, after in [(a, a), (a, random), (np.zeros_like(a), border),
                              (np.zeros_like(a), np.full_like(a, 80))]:
            before, after = Image.fromarray(before), Image.fromarray(after)
            blocks = [SimpleNamespace(box=(-3, -7, width, height)),
                      SimpleNamespace(box=(width * 2, height * 2, width * 3, height * 3))]
            for masked in [[], blocks, [SimpleNamespace(box=(2, 3, width, height))]]:
                for edge in [40, 255, -1, float("nan"), float("inf"), 39.9999999]:
                    reference["CHANGE_EDGE_SPAN"] = accelerated["CHANGE_EDGE_SPAN"] = edge
                    args = (before, after, masked, width * 2, height * 2)
                    expected = reference["measure_outside_change"](*args, temporal_persistence=3.5)
                    actual = accelerated["measure_outside_change"](*args, temporal_persistence=3.5)
                    assert asdict(actual) == asdict(expected), (width, height, edge, masked, expected, actual)
                    cases += 1
            assert reference["estimate_ink_height"](after) == accelerated["estimate_ink_height"](after)
            cases += 1
    # Bright background with ink in two nonadjacent rows, plus RGB conversion.
    for mode in ["L", "RGB", "RGBA"]:
        data = np.full((40, 700), 245, dtype=np.uint8)
        data[7, 120] = data[23, 500] = 15
        image = Image.fromarray(data).convert(mode)
        assert reference["estimate_ink_height"](image) == accelerated["estimate_ink_height"](image) == 17
        cases += 1
    return cases


def measure(function, repeats=15):
    for _ in range(3):
        function()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        function()
        samples.append((time.perf_counter_ns() - start) / 1e6)
    return {"median_ms": statistics.median(samples), "min_ms": min(samples)}


def main():
    cases = parity()
    rng = np.random.default_rng(27)
    a = Image.fromarray(rng.integers(0, 256, (180, 320), dtype=np.uint8))
    b = Image.fromarray(rng.integers(0, 256, (180, 320), dtype=np.uint8))
    crop = Image.new("L", (700, 40), 245)
    for x in range(30, 670, 15):
        for y in range(12, 21):
            crop.putpixel((x, y), 20)
    timings = {}
    for label, helpers in [("python", load_helpers(False)), ("rust", load_helpers(True))]:
        timings[label] = {
            "outside_noise": measure(partial(helpers["measure_outside_change"], a, b, [], 320, 180)),
            "outside_unchanged": measure(partial(helpers["measure_outside_change"], a, a, [], 320, 180)),
            "ink_height": measure(partial(helpers["estimate_ink_height"], crop)),
        }
    helpers = load_helpers(False)
    profile = cProfile.Profile()
    profile.runcall(helpers["measure_outside_change"], a, b, [], 320, 180)
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).sort_stats("cumtime").print_stats(8)
    print(json.dumps({"synthetic_only": True, "parity_cases": cases, "timings": timings,
                      "baseline_profile": stream.getvalue()}, indent=2))


if __name__ == "__main__":
    main()
