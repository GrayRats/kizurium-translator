//! Optional pixel kernels. Application policy and OCR decisions remain in Python.
#![forbid(unsafe_code)]

mod change;
mod image;
mod ink;
mod tiles;

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
    let image =
        image::GrayImage::new(data.as_bytes(), width, height).map_err(PyValueError::new_err)?;
    if tile_size < 4 {
        return Err(PyValueError::new_err("tile_size must be at least 4"));
    }
    // NumPy compares a float32 array to a Python scalar using float32 rounding.
    let threshold = pixel_threshold as f32;
    Ok(py.detach(|| tiles::reduce(image, tile_size, threshold)))
}

/// Vertical row span; empty images return zero, images without dark ink their height.
#[pyfunction]
fn ink_row_span(
    py: Python<'_>,
    data: &Bound<'_, PyBytes>,
    width: usize,
    height: usize,
    cutoff: f64,
) -> PyResult<usize> {
    let image =
        image::GrayImage::new(data.as_bytes(), width, height).map_err(PyValueError::new_err)?;
    Ok(py.detach(|| ink::row_span(image, cutoff)))
}

/// (dirty tiles, 4-connected components, mean component size, edge density).
#[pyfunction]
fn outside_change_features(
    py: Python<'_>,
    data: &Bound<'_, PyBytes>,
    outside: &Bound<'_, PyBytes>,
    width: usize,
    height: usize,
    edge_span: f64,
    tile_size: usize,
) -> PyResult<(usize, usize, f64, f64)> {
    let image =
        image::GrayImage::new(data.as_bytes(), width, height).map_err(PyValueError::new_err)?;
    let mask = outside.as_bytes();
    if mask.len() != image.pixels.len() || mask.iter().any(|&value| value > 1) {
        return Err(PyValueError::new_err(
            "outside mask must contain width * height bytes of 0 or 1",
        ));
    }
    if tile_size < 4 {
        return Err(PyValueError::new_err("tile_size must be at least 4"));
    }
    let result = py.detach(|| change::outside_features(image, mask, edge_span, tile_size));
    Ok((
        result.tile_count,
        result.component_count,
        result.mean_component_size,
        result.edge_density,
    ))
}

#[pymodule]
fn kizurium_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(tile_reductions, module)?)?;
    module.add_function(wrap_pyfunction!(ink_row_span, module)?)?;
    module.add_function(wrap_pyfunction!(outside_change_features, module)?)?;
    Ok(())
}
