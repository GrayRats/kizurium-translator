//! One optional pixel reduction. OCR decisions remain in Python.
#![forbid(unsafe_code)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Borrow immutable Python bytes while detached; no Python object enters the kernel.
#[pyfunction]
fn tile_reductions(
    py: Python<'_>,
    data: &Bound<'_, PyBytes>,
    width: usize,
    height: usize,
    tile_size: usize,
    pixel_threshold: f64,
) -> PyResult<Vec<(f64, f64)>> {
    let length = width
        .checked_mul(height)
        .ok_or_else(|| PyValueError::new_err("image dimensions overflow"))?;
    if data.as_bytes().len() != length {
        return Err(PyValueError::new_err(
            "buffer length must equal width * height",
        ));
    }
    if tile_size < 4 {
        return Err(PyValueError::new_err("tile_size must be at least 4"));
    }
    // Every tile sum fits u64; each pixel contributes at most 255.
    if length > (u64::MAX / 255) as usize {
        return Err(PyValueError::new_err("image is too large"));
    }
    let pixels = data.as_bytes();
    // NumPy compares a float32 array to a Python scalar using float32 rounding.
    let threshold = pixel_threshold as f32;
    Ok(py.detach(|| reduce_tiles(pixels, width, height, tile_size, threshold)))
}

fn reduce_tiles(
    pixels: &[u8],
    width: usize,
    height: usize,
    tile_size: usize,
    threshold: f32,
) -> Vec<(f64, f64)> {
    let mut output = Vec::new();
    if pixels.is_empty() {
        return output;
    }
    for y in (0..height).step_by(tile_size) {
        for x in (0..width).step_by(tile_size) {
            let x_end = x.saturating_add(tile_size).min(width);
            let y_end = y.saturating_add(tile_size).min(height);
            let mut sum = 0_u64;
            let mut changed = 0_usize;
            for row in y..y_end {
                for &value in &pixels[row * width + x..row * width + x_end] {
                    sum += u64::from(value);
                    changed += usize::from(f32::from(value) > threshold);
                }
            }
            let count = (x_end - x) * (y_end - y);
            output.push((sum as f64 / count as f64, changed as f64 / count as f64));
        }
    }
    output
}

#[pymodule]
fn kizurium_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(tile_reductions, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::reduce_tiles;

    #[test]
    fn partial_edge_tiles_use_actual_pixel_count() {
        let data = [0, 0, 0, 0, 25, 0, 0, 0, 0, 50];
        let result = reduce_tiles(&data, 5, 2, 4, 24.0);
        assert_eq!(result, vec![(0.0, 0.0), (37.5, 1.0)]);
    }

    #[test]
    fn strict_threshold_obeys_float32_rounding() {
        let data = [25; 16];
        let result = reduce_tiles(&data, 4, 4, 4, 24.9999999_f64 as f32);
        assert_eq!(result, vec![(25.0, 0.0)]);
    }

    #[test]
    fn empty_dimensions_produce_no_tiles() {
        assert!(reduce_tiles(&[], 0, 10, 4, 24.0).is_empty());
        assert!(reduce_tiles(&[], 10, 0, 4, 24.0).is_empty());
    }
}
