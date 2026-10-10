"""Bergamot model sources: Mozilla repository, Firefox import, verification.

No network and no native runtime: requests.get is replaced by a recording fake and
runtime_available() is forced where the test is about its absence.
"""

from __future__ import annotations

import hashlib

import pytest

from kizurium_translator.translation import bergamot as bg
from kizurium_translator.translation.providers import ProviderError
from kizurium_translator.translation.scheduler import Cancelled

BODIES = {"model": b"M" * 5000, "lex": b"L" * 700, "vocab": b"V" * 300}
NAMES = {"model": "model.enru.intgemm.alphas.bin", "lex": "lex.50.50.enru.s2t.bin", "vocab": "vocab.enru.spm"}


def record(kind, version="2.1", body=None, **extra):
    body = BODIES[kind] if body is None else body
    return {
        "name": NAMES[kind], "fromLang": "en", "toLang": "ru", "fileType": kind, "version": version,
        "attachment": {"hash": hashlib.sha256(body).hexdigest(), "size": len(body),
                       "location": f"main-workspace/translations-models/{kind}-{version}.bin"},
        **extra,
    }


def full_set(version="2.1"):
    return [record(kind, version) for kind in BODIES]


class TestPlan:
    def test_the_newest_stable_complete_version_wins(self):
        plan = bg.mozilla_plan(full_set("2.0") + full_set("2.1") + full_set("2.1a1"), "en", "ru")
        assert plan[0] == "2.1"
        assert set(plan[1]) == {"model", "lex", "vocab"}

    def test_alpha_versions_are_never_chosen(self):
        assert bg.mozilla_plan(full_set("2.1a1"), "en", "ru") is None

    def test_an_incomplete_newer_version_falls_back_to_a_complete_older_one(self):
        records = full_set("2.0") + [record("model", "2.2")]
        assert bg.mozilla_plan(records, "en", "ru")[0] == "2.0"

    def test_a_record_without_a_checksum_is_not_a_candidate(self):
        broken = full_set()
        broken[0]["attachment"]["hash"] = ""
        assert bg.mozilla_plan(broken, "en", "ru") is None

    def test_region_codes_match_by_language(self):
        assert bg.mozilla_plan(full_set(), "en-US", "ru-RU") is not None

    def test_a_missing_pair_is_none(self):
        assert bg.mozilla_plan(full_set(), "en", "de") is None


class FakeResponse:
    def __init__(self, body=b"", payload=None):
        self.body, self.payload = body, payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload

    def iter_content(self, _size):
        yield from (self.body[i:i + 1000] for i in range(0, len(self.body), 1000))


def fake_requests(monkeypatch, records, tamper=None):
    served = []

    def get(url, **_kw):
        served.append(url)
        if url == bg.MOZILLA_RECORDS:
            return FakeResponse(payload={"data": records})
        if url == bg.MOZILLA_SERVER:
            return FakeResponse(payload={"capabilities": {"attachments": {"base_url": "https://cdn.mozilla.net/"}}})
        kind = next(k for k in BODIES if f"/{k}-" in url)
        body = BODIES[kind] if tamper != kind else b"X" * len(BODIES[kind])
        return FakeResponse(body=body)

    import requests

    monkeypatch.setattr(requests, "get", get)
    return served


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(bg, "cache_root", lambda: tmp_path / "models")
    monkeypatch.setattr(bg, "runtime_available", lambda: False)
    events = []
    return bg.ModelManager(lambda: False, lambda *a: events.append(a)), events


class TestMozillaDownload:
    def test_a_verified_download_is_installed_and_valid(self, manager, tmp_path, monkeypatch):
        mgr, events = manager
        fake_requests(monkeypatch, full_set())
        path = mgr.download("en", "ru")
        assert path == tmp_path / "models" / "en-ru"
        bg.validate_files(path, "en", "ru")
        assert (path / NAMES["model"]).read_bytes() == BODIES["model"]
        assert any(e[0] == "Downloading" and 0 < e[2] <= 1 for e in events), "real progress is reported"
        mgr.pool.shutdown()

    def test_a_file_that_does_not_match_its_checksum_installs_nothing(self, manager, tmp_path, monkeypatch):
        mgr, _ = manager
        fake_requests(monkeypatch, full_set(), tamper="lex")
        with pytest.raises(ProviderError) as err:
            mgr.download("en", "ru")
        assert err.value.category == "corrupted"
        assert not (tmp_path / "models" / "en-ru").exists()
        assert not [p for p in (tmp_path / "models").iterdir()], "no half-downloaded leftovers"
        mgr.pool.shutdown()

    def test_an_existing_model_is_preserved_when_a_redownload_fails(self, manager, tmp_path, monkeypatch):
        mgr, _ = manager
        fake_requests(monkeypatch, full_set())
        path = mgr.download("en", "ru")
        before = (path / "kizurium-model.json").read_text()
        fake_requests(monkeypatch, full_set(), tamper="model")
        with pytest.raises(ProviderError):
            mgr.download("en", "ru", replace=True)
        assert (path / "kizurium-model.json").read_text() == before
        mgr.pool.shutdown()

    def test_offline_mode_blocks_the_download_before_any_request(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bg, "cache_root", lambda: tmp_path / "models")
        served = fake_requests(monkeypatch, full_set())
        mgr = bg.ModelManager(lambda: True)
        with pytest.raises(ProviderError) as err:
            mgr.submit("download", "en", "ru")
        assert err.value.category == "offline"
        with pytest.raises(Cancelled):
            mgr.download("en", "ru")
        assert served == []
        mgr.pool.shutdown()

    def test_a_cancelled_download_leaves_no_model(self, manager, tmp_path, monkeypatch):
        mgr, _ = manager
        fake_requests(monkeypatch, full_set())
        mgr.cancel()
        with pytest.raises(Cancelled):
            mgr.download("en", "ru")
        assert not (tmp_path / "models" / "en-ru").exists()
        mgr.pool.shutdown()

    def test_a_download_host_outside_mozilla_is_refused(self, manager, tmp_path, monkeypatch):
        mgr, _ = manager
        with pytest.raises(ProviderError):
            mgr._fetch("https://evil.example/x.bin", tmp_path / "x", 1, "0" * 64, 0, 1, "en-ru")
        with pytest.raises(ProviderError):
            mgr._fetch("http://cdn.mozilla.net/x.bin", tmp_path / "x", 1, "0" * 64, 0, 1, "en-ru")
        mgr.pool.shutdown()


class TestFirefoxImport:
    def make_profile(self, tmp_path, with_vocab=True):
        profile = tmp_path / "ff" / "abc.default"
        folder = profile / "exported" / "models"
        folder.mkdir(parents=True)
        for kind, name in NAMES.items():
            if kind != "vocab" or with_vocab:
                (folder / name).write_bytes(BODIES[kind])
        return profile, folder

    def test_a_complete_file_set_is_found(self, tmp_path):
        profile, folder = self.make_profile(tmp_path)
        found = list(bg.firefox_file_sets(profile, "en", "ru"))
        assert [d for d, _ in found] == [folder]

    def test_an_incomplete_set_is_ignored(self, tmp_path):
        profile, _ = self.make_profile(tmp_path, with_vocab=False)
        assert list(bg.firefox_file_sets(profile, "en", "ru")) == []

    def test_a_different_pair_is_ignored(self, tmp_path):
        profile, _ = self.make_profile(tmp_path)
        assert list(bg.firefox_file_sets(profile, "en", "de")) == []

    def test_import_copies_validates_and_never_touches_the_original(self, tmp_path, monkeypatch):
        profile, folder = self.make_profile(tmp_path)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
        monkeypatch.setattr(bg, "cache_root", lambda: tmp_path / "models")
        monkeypatch.setattr(bg, "firefox_profiles", lambda home=None: iter([profile]))
        path = bg.discover("en", "ru")
        bg.validate_files(path, "en", "ru")
        assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()} == before
        assert path == tmp_path / "models" / "en-ru"

    def test_nothing_found_is_a_clear_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bg, "cache_root", lambda: tmp_path / "models")
        monkeypatch.setattr(bg, "firefox_profiles", lambda home=None: iter([]))
        with pytest.raises(ProviderError) as err:
            bg.discover("en", "ru")
        assert err.value.category == "not_found"


class TestVerification:
    def installed(self, tmp_path):
        stage = tmp_path / "m"
        stage.mkdir()
        for kind, name in NAMES.items():
            (stage / name).write_bytes(BODIES[kind])
        bg.write_model_dir(stage, "en", "ru", NAMES, "2.1", "test")
        return stage

    def test_a_good_model_validates(self, tmp_path):
        bg.validate_files(self.installed(tmp_path), "en", "ru")

    def test_a_changed_byte_is_corruption(self, tmp_path):
        stage = self.installed(tmp_path)
        (stage / NAMES["lex"]).write_bytes(b"Z" * len(BODIES["lex"]))
        with pytest.raises(ProviderError) as err:
            bg.validate_files(stage, "en", "ru")
        assert err.value.category == "corrupted"

    def test_the_wrong_pair_is_incompatible(self, tmp_path):
        with pytest.raises(ProviderError) as err:
            bg.validate_files(self.installed(tmp_path), "en", "de")
        assert err.value.category == "incompatible"

    def test_a_truncated_file_is_corruption(self, tmp_path):
        stage = self.installed(tmp_path)
        (stage / NAMES["model"]).write_bytes(b"M" * 10)
        with pytest.raises(ProviderError):
            bg.validate_files(stage, "en", "ru")


class TestProviderDiagnosis:
    def test_a_missing_runtime_is_reported_before_a_missing_model(self, monkeypatch):
        monkeypatch.setattr(bg, "runtime_available", lambda: False)
        provider = bg.BergamotProvider()
        with pytest.raises(ProviderError) as err:
            provider.translate(None, "hello", "en", "ru")
        assert err.value.category == "incompatible"
        assert "--bergamot-runtime-install" in err.value.detail

    def test_with_a_runtime_a_missing_model_is_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bg, "runtime_available", lambda: True)
        monkeypatch.setattr(bg, "cache_root", lambda: tmp_path / "models")
        monkeypatch.setattr(bg, "firefox_profiles", lambda home=None: iter([]))
        with pytest.raises(ProviderError) as err:
            bg.BergamotProvider().translate(None, "hello", "en", "ru")
        assert err.value.category == "not_found"

    def test_translator_with_auto_source_names_the_problem(self, tmp_path, monkeypatch):
        from kizurium_translator import translate

        monkeypatch.setattr(bg, "runtime_available", lambda: True)
        tr = translate.Translator(provider="bergamot", target="ru", source="auto", offline_only=True,
                                  cache_path=tmp_path / "c.json")
        monkeypatch.setattr(tr, "resolve_source", lambda _t: "auto")
        seen = []
        tr.status_callback = seen.append
        assert tr.via_provider("???") == ""
        assert any("исходный язык" in s for s in seen)
        tr.shutdown()
