"""Stop must invalidate work that has not started in the translation pool."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

from kizurium_translator.live import runtime
from kizurium_translator.translate import Translator
from kizurium_translator.translation import batch


def test_stop_releases_batch_and_prevents_queued_provider_calls(monkeypatch, tmp_path):
    tr = Translator(provider='custom', source='en', target='ru',
                    cache_path=tmp_path / 'cache.sqlite')
    monkeypatch.setattr(runtime.TRANSLATION, 'disabled', False)
    monkeypatch.setattr(runtime.TRANSLATION, 'translator', tr)
    monkeypatch.setattr(batch, 'glossary_translation', lambda *a, **kw: None)
    calls = []
    monkeypatch.setattr(tr, 'translate', lambda *a, **kw: calls.append(a) or 'Перевод')
    release, submitted, finished = Event(), Event(), Event()
    pool = ThreadPoolExecutor(max_workers=1)
    pool.submit(release.wait)
    submit = pool.submit
    def tracked_submit(*args, **kwargs):
        result = submit(*args, **kwargs)
        submitted.set()
        return result
    monkeypatch.setattr(pool, 'submit', tracked_submit)
    monkeypatch.setattr(runtime.TRANSLATION, 'pool', pool)
    def run():
        try:
            batch.translate_many(['A previously unseen dialogue line'], {})
        finally:
            finished.set()
    worker = Thread(target=run)
    worker.start()
    try:
        assert submitted.wait(2)
        tr.cancel_pending()
        assert finished.wait(1), 'Stop waited for the blocked executor'
    finally:
        release.set()
        worker.join(2)
        pool.shutdown(wait=True)
        tr.shutdown()
    assert calls == []


def test_job_queued_before_stop_cannot_capture_a_new_generation(monkeypatch):
    called = []
    monkeypatch.setattr(batch, 'translate_single_line', lambda *a, **kw: called.append(a))
    assert batch._translate_line_job('Old dialogue', 'en', False, object(), lambda: True) == ''
    assert called == []
