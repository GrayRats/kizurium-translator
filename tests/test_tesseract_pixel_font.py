import pytest
from PIL import Image

from kizurium_translator.core import active
from kizurium_translator.ocr import universal


@pytest.fixture
def ocr_settings(monkeypatch):
    current = active.Active()
    current.ocr_engines = ('tesseract',)
    current.ocr_language = 'eng'
    monkeypatch.setattr(active, '_ACTIVE', current)
    monkeypatch.setattr(universal, 'installed_tesseract_langs', lambda: ('eng',))
    return current


def test_bitmap_mode_preserves_coordinates_and_selected_language(monkeypatch, ocr_settings):
    ocr_settings.ocr_options = {'tesseract_pixel_font': True, 'tesseract_psm': '6'}
    seen = []
    def recognize(image, lang, psm):
        seen.append((image.size, image.getpixel((0, 0)), lang, psm))
        return [{'text': 'Open the door', 'box': (20, 40, 200, 80), 'line_height': 40}]
    monkeypatch.setattr(universal, 'ocr_with', recognize)
    result = universal._run_tesseract(Image.new('RGB', (180, 70), 'black'))
    assert seen == [((360, 140), 255, 'eng', '6')]
    assert result[0]['box'] == (10, 20, 100, 40)
    assert result[0]['line_height'] == 20


def test_ocr_failure_is_not_disguised_as_an_empty_screen(monkeypatch, ocr_settings):
    def recognize(*args, **kwargs):
        raise RuntimeError('OCR engine failed')
    monkeypatch.setattr(universal, 'ocr_with', recognize)
    with pytest.raises(RuntimeError, match='OCR engine failed'):
        universal._run_tesseract(Image.new('RGB', (100, 50), 'white'))


def test_invalid_psm_fails_before_running_tesseract(ocr_settings):
    ocr_settings.ocr_options = {'tesseract_psm': 'shell text'}
    with pytest.raises(RuntimeError, match='segmentation'):
        universal._run_tesseract(Image.new('RGB', (100, 50), 'white'))
