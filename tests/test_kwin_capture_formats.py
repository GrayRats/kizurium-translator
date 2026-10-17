"""ScreenShot2 returns QImage format IDs, not a fixed BGRA pixel layout."""
import sys

import pytest

from kizurium_translator import capture


@pytest.mark.parametrize('fmt,pixel', [
    (4, bytes([10, 20, 200, 0])),
    (5, bytes([10, 20, 200, 255])),
    (6, bytes([10, 20, 200, 255])),
    (16, bytes([200, 20, 10, 0])),
    (17, bytes([200, 20, 10, 255])),
    (18, bytes([200, 20, 10, 255])),
])
def test_format_preserves_red_and_blue_channels_and_row_padding(fmt, pixel):
    if fmt in (4, 5, 6) and sys.byteorder != 'little':
        pytest.skip('native little-endian test pixels')
    info = {'format': fmt, 'width': 1, 'height': 2, 'stride': 8}
    image = capture._decode_kwin_image((pixel + bytes(4)) * 2, info)
    assert image.mode == 'RGB'
    assert image.size == (1, 2)
    assert [image.getpixel((0, y)) for y in range(2)] == [(200, 20, 10)] * 2


@pytest.mark.parametrize('fmt,pixel', [(6, bytes([25, 50, 100, 128])),
                                      (18, bytes([100, 50, 25, 128]))])
def test_premultiplied_color_is_restored(fmt, pixel):
    if fmt == 6 and sys.byteorder != 'little':
        pytest.skip('native little-endian test pixels')
    image = capture._decode_kwin_image(pixel, {'format': fmt, 'width': 1, 'height': 1, 'stride': 4})
    assert image.getpixel((0, 0)) == (199, 99, 49)


def test_truncated_pixels_are_rejected():
    with pytest.raises(capture.CaptureError, match='interrupted'):
        capture._decode_kwin_image(bytes(3), {'format': 16, 'width': 1, 'height': 1, 'stride': 4})


@pytest.mark.parametrize('info', [{'format': 16, 'width': 2, 'height': 1, 'stride': 4},
                                 {'format': 16, 'width': 1, 'height': 0, 'stride': 4},
                                 {'format': 99, 'width': 1, 'height': 1, 'stride': 4}])
def test_invalid_metadata_is_rejected(info):
    with pytest.raises(capture.CaptureError):
        capture._kwin_image_layout(info)


def test_unsupported_kwin_format_falls_back_once_to_portal(monkeypatch):
    monkeypatch.setattr(capture, '_kwin_state', None)
    calls = []
    def unsupported(geom):
        calls.append('kwin')
        raise capture.KWinUnsupported('Unsupported KWin screenshot format: 99')
    monkeypatch.setattr(capture, 'kwin_capture', unsupported)
    monkeypatch.setattr(capture, 'portal_capture', lambda geom: calls.append('portal') or 'image')
    assert capture.kde_capture('0,0 16x16') == 'image'
    assert capture.kde_capture('0,0 16x16') == 'image'
    assert calls == ['kwin', 'portal', 'portal']


def test_the_real_layout_check_names_an_unreadable_format_as_unsupported():
    # kde_capture remembers this type for the whole process; a plain CaptureError is a one-off.
    import pytest

    with pytest.raises(capture.KWinUnsupported):
        capture._kwin_image_layout({"width": 4, "height": 4, "stride": 16, "format": 99})
    assert issubclass(capture.KWinUnsupported, capture.CaptureError)
