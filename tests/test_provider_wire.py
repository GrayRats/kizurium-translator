"""Wire format of the online providers and the scheduler's retry/cache rules.

No network: the HTTP object is a fake that records the request it was given.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kizurium_translator.translation.providers import HTTPProvider, ProviderError
from kizurium_translator.translation.scheduler import Scheduler


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeHTTP:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def translate(provider_id, options, source="en", target="ru", payload=None):
    http = FakeHTTP(FakeResponse(payload=payload))
    out = HTTPProvider(provider_id, options).translate(http, "hello", source, target)
    return out, http.calls[0]


class TestWireFormat:
    def test_deepl_uses_region_free_codes_the_api_accepts(self):
        out, call = translate(
            "deepl", {"key": "k"}, source="zh-CN", target="zh-CN",
            payload={"translations": [{"text": "ok"}]},
        )
        assert out == "ok"
        assert call["json"]["target_lang"] == "ZH-HANS"
        assert call["json"]["source_lang"] == "ZH"
        assert call["headers"]["Authorization"] == "DeepL-Auth-Key k"
        assert call["url"].startswith("https://api-free.deepl.com/")

    def test_deepl_pro_endpoint_and_english_default(self):
        _, call = translate("deepl", {"key": "k", "plan": "pro"}, target="en",
                            payload={"translations": [{"text": "x"}]})
        assert call["url"].startswith("https://api.deepl.com/")
        assert call["json"]["target_lang"] == "EN-US"

    def test_azure_headers_region_and_chinese_code(self):
        out, call = translate(
            "azure", {"key": "k", "region": "westeurope"}, target="zh-CN",
            payload=[{"translations": [{"text": "ok"}]}],
        )
        assert out == "ok"
        assert call["headers"]["Ocp-Apim-Subscription-Key"] == "k"
        assert call["headers"]["Ocp-Apim-Subscription-Region"] == "westeurope"
        assert call["params"]["to"] == "zh-Hans"
        assert call["url"].endswith("/translate")

    def test_yandex_auth_modes_and_folder(self):
        _, call = translate("yandex", {"key": "k", "folder_id": "f1"},
                            payload={"translations": [{"text": "x"}]})
        assert call["headers"]["Authorization"] == "Api-Key k"
        assert call["json"]["folderId"] == "f1"
        _, call = translate("yandex", {"key": "t", "auth": "iam"}, target="zh-CN",
                            payload={"translations": [{"text": "x"}]})
        assert call["headers"]["Authorization"] == "Bearer t"
        assert call["json"]["targetLanguageCode"] == "zh"

    def test_custom_template_auth_and_response_path(self):
        opts = {
            "endpoint": "https://example.test/t", "auth": "bearer", "key": "tok",
            "headers": {"X-Extra": "1"},
            "body": {"q": "{text}", "pair": ["{source}", "{target}"]},
            "response_path": "$.results[0].text",
        }
        out, call = translate("custom", opts, payload={"results": [{"text": "ok"}]})
        assert out == "ok"
        assert call["json"] == {"q": "hello", "pair": ["en", "ru"]}
        assert call["headers"]["Authorization"] == "Bearer tok"
        assert call["headers"]["X-Extra"] == "1"

    def test_missing_key_is_an_authentication_error_before_any_request(self):
        http = FakeHTTP(FakeResponse(payload={}))
        with pytest.raises(ProviderError) as err:
            HTTPProvider("deepl", {}).translate(http, "hello", "en", "ru")
        assert err.value.category == "authentication"
        assert http.calls == []

    def test_retry_after_header_is_parsed_from_a_429(self):
        http = FakeHTTP(FakeResponse(429, {}, {"Retry-After": "7"}))
        with pytest.raises(ProviderError) as err:
            HTTPProvider("deepl", {"key": "k"}).translate(http, "hello", "en", "ru")
        assert err.value.category == "rate_limit"
        assert err.value.retry_after == 7.0


class TestScheduler:
    def run(self, sched, key, operation, **kw):
        return sched.run_provider("p", key, operation, interval=0, **kw)

    def test_transient_failure_is_not_remembered(self):
        sched = Scheduler()
        with pytest.raises(ProviderError):
            self.run(sched, ("fp", "t"), lambda: (_ for _ in ()).throw(
                ProviderError("rate_limit", 429)), retries=0, cooldown=0)
        assert self.run(sched, ("fp", "t"), lambda: "ok", retries=0, cooldown=0) == "ok"

    def test_credential_verdict_belongs_to_one_configuration(self):
        sched = Scheduler()
        with pytest.raises(ProviderError):
            self.run(sched, ("old-key", "t"), lambda: (_ for _ in ()).throw(
                ProviderError("authentication", 401)), retries=0)
        # The fixed key has a different fingerprint and is not blocked.
        assert self.run(sched, ("new-key", "t"), lambda: "ok", retries=0) == "ok"
        with pytest.raises(ProviderError):
            self.run(sched, ("old-key", "other"), lambda: "never", retries=0)

    def test_retry_after_is_honoured(self):
        sched = Scheduler()
        attempts = []

        def op():
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise ProviderError("rate_limit", 429, retry_after=0.3)
            return "ok"

        assert self.run(sched, ("fp", "t"), op, retries=2) == "ok"
        assert attempts[1] - attempts[0] >= 0.29

    def test_absurd_retry_after_does_not_block_the_lane_for_hours(self):
        sched = Scheduler()
        calls = []

        def op():
            calls.append(1)
            raise ProviderError("rate_limit", 429, retry_after=86400)

        start = time.monotonic()
        with pytest.raises(ProviderError):
            self.run(sched, ("fp", "t"), op, retries=3)
        assert time.monotonic() - start < 2
        assert len(calls) == 1

    def test_after_the_retries_are_used_up_the_provider_cools_down_without_the_network(self):
        sched = Scheduler()
        calls = []

        def op():
            calls.append(1)
            raise ProviderError("rate_limit", 429)

        with pytest.raises(ProviderError):
            sched.run_provider("p", ("fp", "a"), op, interval=0, retries=0, cooldown=30)
        started = time.monotonic()
        with pytest.raises(ProviderError) as err:
            sched.run_provider("p", ("fp", "b"), op, interval=0, retries=0, cooldown=30)
        assert err.value.category == "rate_limit"
        assert len(calls) == 1, "the cooling provider must not be called again"
        assert time.monotonic() - started < 1

    def test_cooldown_ends_and_a_success_clears_it(self):
        sched = Scheduler()
        with pytest.raises(ProviderError):
            sched.run_provider("p", ("fp", "a"), lambda: (_ for _ in ()).throw(
                ProviderError("rate_limit", 429, retry_after=0.2)), interval=0, retries=0)
        time.sleep(0.25)
        assert sched.run_provider("p", ("fp", "b"), lambda: "ok", interval=0, retries=0) == "ok"

    def test_a_cached_answer_is_still_served_while_cooling(self):
        sched = Scheduler()
        assert self.run(sched, ("fp", "cached"), lambda: "ok", retries=0) == "ok"
        with pytest.raises(ProviderError):
            sched.run_provider("p", ("fp", "x"), lambda: (_ for _ in ()).throw(
                ProviderError("rate_limit", 429)), interval=0, retries=0, cooldown=30)
        assert self.run(sched, ("fp", "cached"), lambda: "never", retries=0) == "ok"

    def test_identical_requests_are_answered_from_the_cache(self):
        sched = Scheduler()
        calls = []
        op = lambda: calls.append(1) or "ok"  # noqa: E731
        assert self.run(sched, ("fp", "t"), op, retries=0) == "ok"
        assert self.run(sched, ("fp", "t"), op, retries=0) == "ok"
        assert len(calls) == 1

    def test_cancelled_request_never_reaches_the_provider(self):
        from kizurium_translator.translation.scheduler import Cancelled

        sched = Scheduler()
        calls = []
        with pytest.raises(Cancelled):
            self.run(sched, ("fp", "t"), lambda: calls.append(1), cancelled=lambda: True)
        assert calls == []

    def test_cancelled_frame_keeps_the_servers_pause(self):
        from kizurium_translator.translation.scheduler import Cancelled

        sched = Scheduler()
        cancelled = False

        def op():
            nonlocal cancelled
            cancelled = True
            raise ProviderError("rate_limit", 429, retry_after=300)

        with pytest.raises(Cancelled):
            self.run(sched, ("fp", "old-frame"), op, cancelled=lambda: cancelled)
        with pytest.raises(ProviderError) as err:
            self.run(sched, ("fp", "new-frame"), lambda: pytest.fail("must stay offline"))
        assert err.value.category == "rate_limit"

    def test_long_retry_after_is_not_shortened_to_the_wait_budget(self, monkeypatch):
        sched = Scheduler()
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        with pytest.raises(ProviderError):
            self.run(sched, ("fp", "t"), lambda: (_ for _ in ()).throw(
                ProviderError("rate_limit", 429, retry_after=86400)))
        now += 121
        with pytest.raises(ProviderError):
            self.run(sched, ("fp", "new"), lambda: pytest.fail("server asked for a day"))

    def test_success_from_obsolete_frame_can_be_reused(self):
        from kizurium_translator.translation.scheduler import Cancelled

        sched = Scheduler()
        cancelled = False

        def op():
            nonlocal cancelled
            cancelled = True
            return "translation"

        with pytest.raises(Cancelled):
            self.run(sched, ("fp", "same-text"), op, cancelled=lambda: cancelled)
        assert self.run(sched, ("fp", "same-text"), lambda: pytest.fail("duplicate request")) == "translation"

    def test_repeated_limits_increase_pacing_across_frames(self, monkeypatch):
        sched = Scheduler()
        now = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: now)
        for expected_interval in (3.6, 7.2):
            with pytest.raises(ProviderError):
                sched.run_provider("p", ("fp", str(now)), lambda: (_ for _ in ()).throw(
                    ProviderError("rate_limit", 429, retry_after=0)), retries=0, interval=1.8)
            assert sched._providers["p"].minimum_interval == expected_interval
            now += 10
