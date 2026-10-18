# Native pixel analysis kernels

This separate maturin/PyO3 package exposes three kernels. The main application
remains a Python/hatchling package and runs without it. Arch packages include
the native wheel; `install.sh --user` builds it when Cargo is available.

- `tile_reductions`: row-major `(mean, fraction_over_threshold)` tiles.
- `outside_change_features`: local 3×3 contrast, four-neighbor connected components,
  edge density and dirty tile count for a supplied change mask.
- `ink_row_span`: first-to-last row containing a pixel below a supplied threshold.

Python retains thresholds, change classification, masks, geometry, tracking,
upscale decisions, OCR and translation. `KIZURIUM_NATIVE=0` selects the existing
Python implementation at runtime. Unsupported image modes, absent symbols in an
older companion wheel, or unavailable imports also retain that implementation.
NumPy-less tile behavior remains in Python; ink span does not require NumPy.

`lib.rs` owns the Python boundary; `image.rs` validates shared grayscale views;
`tiles.rs`, `change.rs` and `ink.rs` contain pure Rust computation. Immutable
Python byte arguments own the borrowed buffers throughout `Python::detach`.
Rust retains no Python objects, forbids unsafe code, and returns owned primitive
values. Dimensions and buffers are checked before work. Contrast neighborhoods
wrap like NumPy roll; component neighbors do not wrap. Comparisons retain the
Python implementation's strict thresholds and float32 rounding where applicable.

Build with `maturin build --release --locked --manifest-path native/Cargo.toml`
and install the resulting wheel into the application's Python environment.
`cargo test --locked --manifest-path native/Cargo.toml` tests pure kernels.
`scripts/check_native_tiles.py` and `scripts/check_native_pixels.py` check
synthetic parity and time production helper bodies including dispatch, copies
and result conversion. The latter also profiles the Python reference. Neither
script captures a display or invokes OCR. Timings describe these helpers only.
