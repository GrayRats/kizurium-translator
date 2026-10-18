//! Pixel features only; classification, masking and thresholds belong to Python.
use crate::image::GrayImage;

#[derive(Debug, Default, PartialEq)]
pub(crate) struct Features {
    pub(crate) tile_count: usize,
    pub(crate) component_count: usize,
    pub(crate) mean_component_size: f64,
    pub(crate) edge_density: f64,
}

pub(crate) fn outside_features(
    image: GrayImage<'_>,
    outside: &[u8],
    edge_span: f64,
    tile: usize,
) -> Features {
    let GrayImage {
        pixels,
        width,
        height,
    } = image;
    if pixels.is_empty() {
        return Features::default();
    }
    let mut ink = vec![false; pixels.len()];
    let columns = width.div_ceil(tile);
    let mut dirty_tiles = vec![false; columns * height.div_ceil(tile)];
    let mut edge_count = 0;
    for y in 0..height {
        // Match NumPy roll: neighborhoods wrap at both image boundaries.
        let rows = [
            if y == 0 { height - 1 } else { y - 1 },
            y,
            if y + 1 == height { 0 } else { y + 1 },
        ];
        for x in 0..width {
            let index = y * width + x;
            if outside[index] == 0 {
                continue;
            }
            dirty_tiles[y / tile * columns + x / tile] = true;
            let xs = [
                if x == 0 { width - 1 } else { x - 1 },
                x,
                if x + 1 == width { 0 } else { x + 1 },
            ];
            let (mut low, mut high) = (u8::MAX, u8::MIN);
            for row in rows {
                for column in xs {
                    let value = pixels[row * width + column];
                    low = low.min(value);
                    high = high.max(value);
                }
            }
            // NumPy uses float32 neighborhoods and scalar promotion.
            if f32::from(high - low) > edge_span as f32 {
                ink[index] = true;
                edge_count += 1;
            }
        }
    }
    let mut component_count = 0;
    let mut stack = Vec::new();
    for index in 0..ink.len() {
        if !ink[index] {
            continue;
        }
        component_count += 1;
        ink[index] = false;
        stack.push(index);
        while let Some(current) = stack.pop() {
            let (x, y) = (current % width, current / width);
            // Components use four neighbors WITHOUT wrapping, unlike contrast.
            let neighbors = [
                (y > 0).then(|| current - width),
                (y + 1 < height).then(|| current + width),
                (x > 0).then(|| current - 1),
                (x + 1 < width).then(|| current + 1),
            ];
            for neighbor in neighbors.into_iter().flatten() {
                if ink[neighbor] {
                    // Mark on admission: every pixel enters the stack once.
                    ink[neighbor] = false;
                    stack.push(neighbor);
                }
            }
        }
    }
    Features {
        tile_count: dirty_tiles.into_iter().filter(|&dirty| dirty).count(),
        component_count,
        mean_component_size: if component_count == 0 {
            0.0
        } else {
            edge_count as f64 / component_count as f64
        },
        edge_density: edge_count as f64 / pixels.len() as f64,
    }
}

#[cfg(test)]
mod tests {
    use super::{outside_features, Features};
    use crate::image::GrayImage;

    #[test]
    fn diagonal_pixels_are_separate_components() {
        let image = GrayImage::new(&[0, 255, 255, 0], 2, 2).unwrap();
        let got = outside_features(image, &[1, 0, 0, 1], 40.0, 4);
        assert_eq!(got.component_count, 2);
        assert_eq!(got.mean_component_size, 1.0);
        assert_eq!(got.edge_density, 0.5);
        assert_eq!(got.tile_count, 1);
    }

    #[test]
    fn neighborhood_wraps_but_components_do_not() {
        let image = GrayImage::new(&[0, 0, 0, 255], 4, 1).unwrap();
        let got = outside_features(image, &[1, 0, 0, 1], 40.0, 4);
        assert_eq!(got.component_count, 2);
        assert_eq!(got.edge_density, 0.5);
    }

    #[test]
    fn flat_changed_area_has_dirty_tiles_without_edges() {
        let pixels = [80; 25];
        let image = GrayImage::new(&pixels, 5, 5).unwrap();
        assert_eq!(
            outside_features(image, &[1; 25], 40.0, 4),
            Features {
                tile_count: 4,
                ..Features::default()
            }
        );
        assert_eq!(
            outside_features(image, &[0; 25], 40.0, 4),
            Features::default()
        );
    }
}
