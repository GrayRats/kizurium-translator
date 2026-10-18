//! Row-major reductions over partial or full grayscale tiles.
use crate::image::GrayImage;

pub(crate) fn reduce(image: GrayImage<'_>, tile_size: usize, threshold: f32) -> Vec<(f64, f64)> {
    let GrayImage {
        pixels,
        width,
        height,
    } = image;
    if pixels.is_empty() {
        return Vec::new();
    }
    let mut output = Vec::with_capacity(width.div_ceil(tile_size) * height.div_ceil(tile_size));
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

#[cfg(test)]
mod tests {
    use super::reduce;
    use crate::image::GrayImage;

    #[test]
    fn partial_edge_tiles_use_actual_pixel_count() {
        let data = [0, 0, 0, 0, 25, 0, 0, 0, 0, 50];
        let result = reduce(GrayImage::new(&data, 5, 2).unwrap(), 4, 24.0);
        assert_eq!(result, vec![(0.0, 0.0), (37.5, 1.0)]);
    }

    #[test]
    fn strict_threshold_obeys_float32_rounding() {
        let data = [25; 16];
        let result = reduce(
            GrayImage::new(&data, 4, 4).unwrap(),
            4,
            24.9999999_f64 as f32,
        );
        assert_eq!(result, vec![(25.0, 0.0)]);
    }

    #[test]
    fn empty_dimensions_produce_no_tiles() {
        assert!(reduce(GrayImage::new(&[], 0, 10).unwrap(), 4, 24.0).is_empty());
        assert!(reduce(GrayImage::new(&[], 10, 0).unwrap(), 4, 24.0).is_empty());
    }
}
