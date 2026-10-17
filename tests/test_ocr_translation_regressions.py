"""OCR fallback and language propagation through the live replacement pipeline."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from kizurium_translator.core import active
from kizurium_translator.live import runtime
from kizurium_translator.ocr import engine, frame, routing, universal
from kizurium_translator.translate import Translator
from kizurium_translator.translation import batch


@pytest.mark.parametrize('failed,available', [(False, ('tesseract',)),
                                              (True, ('rapid', 'tesseract'))])
def test_automatic_live_ocr_uses_tesseract_when_rapid_cannot_run(monkeypatch, failed, available):
    monkeypatch.setattr(active, '_ACTIVE', active.Active())
    monkeypatch.setattr(routing, 'available_engines', lambda: available)
    monkeypatch.setattr(engine, 'RAPID_INIT_FAILED', failed)
    calls = []
    def recognize(image, langs=None):
        calls.append(image.size)
        return [{'text': 'Take the key and open the door', 'box': (10, 20, 300, 40),
                 'line_height': 20, 'conf': 95}]
    monkeypatch.setattr(universal, '_run_tesseract', recognize)
    monkeypatch.setattr(universal, '_run_rapid', lambda _: [])
    lines, mode = frame.ocr_image(Image.new('RGB', (600, 400)))
    assert calls == [(600, 400)]
    assert lines[0]['engine'] == 'tesseract'
    assert lines[0]['lang'] == 'en'
    assert lines[0]['box'] == (10, 20, 300, 40)


def test_explicit_ocr_language_survives_the_compact_fallback(monkeypatch):
    settings = active.Active()
    settings.ocr_engines = ('tesseract',)
    settings.ocr_language = 'jpn'
    monkeypatch.setattr(active, '_ACTIVE', settings)
    monkeypatch.setattr(universal, 'installed_tesseract_langs', lambda: ('eng', 'jpn'))
    calls = []
    monkeypatch.setattr(universal, 'ocr_with', lambda image, lang, psm: calls.append(lang) or [])
    universal._run_tesseract(Image.new('RGB', (100, 50), 'white'), 'eng')
    assert calls == ['jpn']


def test_missing_selected_engine_reports_a_dependency_error(monkeypatch):
    settings = active.Active()
    settings.ocr_engines = ('rapid',)
    monkeypatch.setattr(active, '_ACTIVE', settings)
    monkeypatch.setattr(routing, 'available_engines', lambda: ())
    with pytest.raises(RuntimeError, match='OCR.*rapid'):
        frame.ocr_image(Image.new('RGB', (100, 50)))


def test_live_single_line_uses_the_selected_target_glossary(monkeypatch):
    tr = Translator(target='de', source='en')
    monkeypatch.setattr(runtime.TRANSLATION, 'disabled', False)
    requested = []
    monkeypatch.setattr(batch, 'glossary_translation',
                        lambda text, target_lang=None: requested.append(target_lang) or 'Einstellungen')
    assert batch.translate_single_line('Settings', tr=tr) == 'Einstellungen'
    assert requested == ['de']


def test_provider_queue_keeps_source_language_and_translator_snapshot(monkeypatch):
    tr = Translator(target='ru', source='auto')
    monkeypatch.setattr(runtime.TRANSLATION, 'disabled', False)
    monkeypatch.setattr(batch, 'glossary_translation', lambda *a, **kw: None)
    seen = []
    monkeypatch.setattr(tr, 'translate', lambda text, **kw: seen.append(kw['source']) or 'Перевод')
    # The active translator changes after submission: this job belongs to tr.
    monkeypatch.setattr(runtime, 'translator', lambda: pytest.fail('job switched translator'))
    assert batch._translate_line_job('Bonjour tout le monde', 'fr', False, tr) == 'Перевод'
    assert seen == ['fr']


def test_live_batch_preserves_the_explicit_source(monkeypatch):
    tr = Translator(provider='custom', source='fr', target='ru')
    monkeypatch.setattr(runtime.TRANSLATION, 'disabled', False)
    monkeypatch.setattr(runtime.TRANSLATION, 'translator', tr)
    monkeypatch.setattr(batch, 'glossary_translation', lambda *a, **kw: None)
    seen = []
    monkeypatch.setattr(tr, 'translate', lambda text, **kw: seen.append(kw['source']) or 'Доброе утро')
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(runtime.TRANSLATION, 'pool', pool)
        assert batch.translate_many([{'text': 'Bonjour tout le monde', 'source': 'en'}], {}) == ['Доброе утро']
    assert seen == ['fr']
