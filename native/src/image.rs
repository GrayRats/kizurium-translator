//! Validated, borrowed grayscale pixels shared by the kernels.

#[derive(Clone, Copy, Debug)]
pub(crate) struct GrayImage<'a> {
    pub(crate) pixels: &'a [u8],
    pub(crate) width: usize,
    pub(crate) height: usize,
}

impl<'a> GrayImage<'a> {
    pub(crate) fn new(pixels: &'a [u8], width: usize, height: usize) -> Result<Self, &'static str> {
        let length = width
            .checked_mul(height)
            .ok_or("image dimensions overflow")?;
        if pixels.len() != length {
            return Err("buffer length must equal width * height");
        }
        if length > (u64::MAX / 255) as usize {
            return Err("image is too large");
        }
        Ok(Self {
            pixels,
            width,
            height,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::GrayImage;

    #[test]
    fn rejects_overflow_and_mismatched_buffers() {
        assert!(GrayImage::new(&[], usize::MAX, 2).is_err());
        assert!(GrayImage::new(&[1], 2, 2).is_err());
        assert!(GrayImage::new(&[], 0, 3).is_ok());
    }
}
