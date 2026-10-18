//! Vertical extent of rows containing a pixel below the supplied threshold.
use crate::image::GrayImage;

pub(crate) fn row_span(image: GrayImage<'_>, cutoff: f64) -> usize {
    if image.pixels.is_empty() {
        return 0;
    }
    let has_ink = |row: &[u8]| row.iter().any(|&value| f64::from(value) < cutoff);
    let mut rows = image.pixels.chunks_exact(image.width);
    let Some(first) = rows.clone().position(has_ink) else {
        return image.height;
    };
    let last = rows.rposition(has_ink).unwrap_or(first);
    last - first + 1
}

#[cfg(test)]
mod tests {
    use super::row_span;
    use crate::image::GrayImage;

    #[test]
    fn includes_first_and_last_ink_rows_and_keeps_strict_threshold() {
        let pixels = [140, 140, 139, 140, 140, 140, 60, 140, 140, 140];
        let image = GrayImage::new(&pixels, 2, 5).unwrap();
        assert_eq!(row_span(image, 140.0), 3);
        assert_eq!(row_span(image, 60.0), 5);
        assert_eq!(row_span(image, f64::NAN), 5);
    }

    #[test]
    fn empty_images_have_zero_span() {
        assert_eq!(row_span(GrayImage::new(&[], 0, 7).unwrap(), 100.0), 0);
    }
}
