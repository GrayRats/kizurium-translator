"""Provider-specific wire formats. Scheduling, retries and caching live elsewhere."""
from __future__ import annotations

import importlib.util
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

PROVIDERS = {
    "google": "Google Translate",
    "yandex": "Yandex Translate API",
    "deepl": "DeepL API",
    "azure": "Microsoft Azure AI Translator",
    "custom": "Custom API",
    "bergamot": "Bergamot (Local / Offline)",
}


class ProviderError(Exception):
    """Safe to log: never contains server bodies, URLs or credentials."""

    def __init__(self, category, status=0, retry_after=None, detail=""):
        self.category = category
        self.status = status
        self.retry_after = retry_after
        self.detail = detail
        super().__init__(f"{category} HTTP={status}" if status else category)

    @property
    def retryable(self):
        return self.category in {"rate_limit", "unavailable", "network", "timeout"}


def retry_after(value, now=None):
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = (date - (now or datetime.now(timezone.utc))).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


_ZH = {"zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-hans": "zh-Hans", "zh-tw": "zh-Hant", "zh-hant": "zh-Hant"}


def api_language(provider_id, code, *, target):
    """Language code in the form each API accepts (zh-CN is nobody's code)."""
    low = (code or "").lower()
    if provider_id == "deepl":
        if target:
            special = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH-HANS", "zh-cn": "ZH-HANS",
                       "zh-hans": "ZH-HANS", "zh-tw": "ZH-HANT", "zh-hant": "ZH-HANT"}
            return special.get(low, code.upper())
        return code.split("-")[0].upper()
    if provider_id == "azure":
        return _ZH.get(low, code)
    if provider_id == "yandex":
        return {"zh-cn": "zh", "zh-hans": "zh", "zh-tw": "zh-TW", "zh-hant": "zh-TW"}.get(low, code)
    return code


def response_value(data, path):
    """Small deterministic JSON path: $.results[0].text or /results/0/text."""
    if path.startswith("/"):
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
    else:
        path = re.sub(r"\[(\d+)\]", r".\1", path.removeprefix("$").lstrip("."))
        parts = path.split(".") if path else []
    for part in parts:
        data = data[int(part)] if isinstance(data, list) else data[part]
    if not isinstance(data, str) or not data.strip():
        raise ValueError("response path must select a non-empty string")
    return data.strip()


def substitute(value, text, source, target):
    # Substitute decoded JSON values, never interpolate into JSON syntax.
    if isinstance(value, str):
        return re.sub(r"\{(text|source|target)\}",
                      lambda m: {"text": text, "source": source, "target": target}[m[1]], value)
    if isinstance(value, list):
        return [substitute(v, text, source, target) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, text, source, target) for k, v in value.items()}
    return value


class HTTPProvider:
    requires_network = True

    def __init__(self, provider_id, options=None):
        if provider_id not in PROVIDERS or provider_id == "bergamot":
            raise ProviderError("configuration")
        self.provider_id = provider_id
        self.display_name = PROVIDERS[provider_id]
        self.options = dict(options or {})

    def supported_languages(self):
        # Provider remains authoritative; no speculative list or background network call.
        return None

    def is_available(self):
        return importlib.util.find_spec("requests") is not None

    def validate_configuration(self):
        o = self.options
        if self.provider_id in {"deepl", "azure", "yandex"} and not o.get("key"):
            raise ProviderError("authentication", detail="API key / IAM token is missing")
        try:
            timeout = float(o.get("timeout", 15))
            if not math.isfinite(timeout) or not 0 < timeout <= 120:
                raise ValueError
            if self.provider_id == "custom":
                url = urlsplit(o.get("endpoint", ""))
                if url.scheme not in {"http", "https"} or not url.hostname or url.username:
                    raise ValueError
                if o.get("method", "POST").upper() not in {"POST", "PUT", "PATCH", "GET"}:
                    raise ValueError
                if o.get("auth", "none") not in {"none", "bearer", "api-key"}:
                    raise ValueError
                if not isinstance(o.get("headers", {}), dict):
                    raise ValueError
                if o.get("auth", "none") != "none" and not o.get("key"):
                    raise ProviderError("authentication")
            if self.provider_id == "azure":
                url = urlsplit(o.get("endpoint", "https://api.cognitive.microsofttranslator.com"))
                if url.scheme != "https" or not url.hostname or url.username:
                    raise ValueError
        except (ValueError, TypeError):
            raise ProviderError("configuration") from None

    def initialize(self):
        self.validate_configuration()

    def translate(self, http, text, source, target):
        self.validate_configuration()
        o, pid = self.options, self.provider_id
        headers, params = {}, {}
        method, body = "POST", None
        if pid == "google":
            from .online import gtx_request
            response = gtx_request(http, text, source, target, float(o.get("timeout", 15)))
        else:
            if pid == "yandex":
                url = "https://translate.api.cloud.yandex.net/translate/v2/translate"
                headers["Authorization"] = ("Bearer " if o.get("auth") == "iam" else "Api-Key ") + o["key"]
                body = {"texts": [text], "format": "PLAIN_TEXT",
                        "targetLanguageCode": api_language(pid, target, target=True)}
                if source != "auto":
                    body["sourceLanguageCode"] = api_language(pid, source, target=False)
                if o.get("folder_id"):
                    body["folderId"] = o["folder_id"]
                path = "translations.0.text"
            elif pid == "deepl":
                url = "https://api.deepl.com/v2/translate" if o.get("plan") == "pro" else "https://api-free.deepl.com/v2/translate"
                headers["Authorization"] = "DeepL-Auth-Key " + o["key"]
                body = {"text": [text], "target_lang": api_language(pid, target, target=True)}
                if source != "auto":
                    body["source_lang"] = api_language(pid, source, target=False)
                path = "translations.0.text"
            elif pid == "azure":
                url = o.get("endpoint", "https://api.cognitive.microsofttranslator.com").rstrip("/")
                if not url.endswith("/translate"):
                    url += "/translate"
                headers["Ocp-Apim-Subscription-Key"] = o["key"]
                if o.get("region"):
                    headers["Ocp-Apim-Subscription-Region"] = o["region"]
                params = {"api-version": "3.0", "to": api_language(pid, target, target=True)}
                if source != "auto":
                    params["from"] = api_language(pid, source, target=False)
                body, path = [{"Text": text}], "0.translations.0.text"
            else:
                url, method = o["endpoint"], o.get("method", "POST").upper()
                headers = dict(o.get("headers", {}))
                auth = o.get("auth", "none")
                if auth == "bearer":
                    headers["Authorization"] = "Bearer " + o["key"]
                elif auth == "api-key":
                    headers[o.get("key_header", "X-API-Key")] = o["key"]
                body = substitute(o.get("body", {"q": "{text}", "source": "{source}", "target": "{target}"}), text, source, target)
                path = o.get("response_path", "translatedText")
                if method == "GET":
                    params, body = body, None
            response = http.request(method, url, headers=headers, params=params, json=body,
                                    timeout=(5, float(o.get("timeout", 15))), allow_redirects=False)
        status = response.status_code
        if status != 200:
            category = {401: "authentication", 403: "authentication_or_quota", 429: "rate_limit", 456: "quota"}.get(status, "unavailable" if status >= 500 else "request")
            # Explicit structured quota errors must not be retried as transient throttling.
            if status == 429:
                try:
                    error = response.json().get("error", {})
                    code = str(error.get("code", "")) if isinstance(error, dict) else ""
                    if code in {"insufficient_quota", "quota_exceeded", "QuotaExceeded"}:
                        category = "quota"
                except (ValueError, AttributeError):
                    pass
            raise ProviderError(category, status, retry_after(getattr(response, "headers", {}).get("Retry-After")))
        try:
            data = response.json()
            if pid == "google":
                result = "".join(part[0] for part in data[0] if part and part[0]).strip()
                if not result:
                    raise ValueError
                return result
            return response_value(data, path)
        except (ValueError, TypeError, IndexError, KeyError):
            raise ProviderError("response") from None

    def cancel(self):
        # Transport deadlines bound an in-flight request; scheduler cancels queued work.
        pass

    def shutdown(self):
        self.cancel()
