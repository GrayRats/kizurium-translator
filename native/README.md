# Optional native tile helper

This separate maturin package exposes `kizurium_native.tile_reductions`.
The main application remains a Python/hatchling package and runs without it.
See the repository architecture document for installation and phase boundaries.

The only kernel reduces already computed grayscale pixel differences into
row-major `(mean, fraction_over_threshold)` tiles. Python retains all thresholds,
change classification, masking, geometry, tracking, OCR and translation.
`KIZURIUM_NATIVE=0` selects the existing Python implementation at runtime.
Unsupported image modes and absent NumPy also retain that implementation.

Pillow creates one immutable `bytes` buffer. PyO3 borrows that buffer while the
kernel runs with `Python::detach`; the Python argument owns it throughout the
call. Rust retains no Python objects, uses no unsafe code, and returns owned
primitive values. Dimension/buffer checks precede detached work. The threshold
rounds to float32 to match NumPy's comparison against a Python scalar.

`python scripts/check_native_tiles.py` checks isolated synthetic parity and
measures the production helper bodies including Python dispatch, result
conversion and `Image.tobytes()`. Its timings describe these helpers only.
The kernel never invokes OCR, captures a display or changes recognition rules.
