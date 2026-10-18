"""Планировщик перевода.

Запрос несёт ревизию кадра. Ответ, который вернулся после смены ревизии,
не применяется: блок уже другой, и старый перевод лёг бы поверх новой сцены.

every asynchronous result carries:

```text
session_id
frame_revision
state_revision
block_id
content_revision
```

and is discarded when any of those no longer match current state.

Онлайн-бэкенд (`gtx`) идёт строго по одному запросу за раз. Локальная пачка
ограничена восемью блоками или 512 исходными токенами — это режет
`translate_many`, а не этот модуль.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

# Приоритеты очереди. Меньше — раньше.
P0_NEW_DIALOGUE = 0
P1_CHANGED_DIALOGUE = 1
P2_NEW_UI = 2
P3_CHANGED_UI = 3
P4_TRANSIENT = 4
P5_BACKGROUND = 5

MAX_RETRY_AFTER_S = 120.0
LOCAL_BATCH_BLOCKS = 8
LOCAL_BATCH_TOKENS = 512


@dataclass(frozen=True)
class ResultIdentity:
    """identity stamped on every async OCR/translation result."""

    session_id: str
    frame_revision: int
    state_revision: int
    block_id: int = 0
    content_revision: int = 0


@dataclass(frozen=True)
class TranslationRequest:
    """Один запрос. Ревизия — то, с чем сверяется ответ."""

    session_id: str
    state_revision: int
    block_id: int
    content_revision: int
    source: str
    source_language: str
    target_language: str
    text: str
    priority: int = P2_NEW_UI
    frame_revision: int = 0

    def identity(self) -> ResultIdentity:
        return ResultIdentity(
            session_id=self.session_id,
            frame_revision=int(self.frame_revision),
            state_revision=int(self.state_revision),
            block_id=int(self.block_id),
            content_revision=int(self.content_revision),
        )


class Scheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0
        self._frame_revision = 0
        self._session = "live"
        self._block_content: dict[int, int] = {}
        self._gtx = threading.BoundedSemaphore(1)
        self._providers = {}

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def frame_revision(self) -> int:
        with self._lock:
            return self._frame_revision

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._session

    def bump(self) -> int:
        """Сцена сменилась: ответы, снятые со старой ревизии, больше не годятся."""
        with self._lock:
            self._revision += 1
            self._block_content.clear()
            return self._revision

    def bump_frame(self) -> int:
        """A newer capture arrived; OCR/translate tied to the old frame is stale."""
        with self._lock:
            self._frame_revision += 1
            return self._frame_revision

    def set_session(self, session_id: str) -> None:
        with self._lock:
            self._session = str(session_id or "live")
            self._revision += 1
            self._frame_revision += 1
            self._block_content.clear()

    def note_block_content(self, block_id: int, content_revision: int) -> None:
        with self._lock:
            self._block_content[int(block_id)] = int(content_revision)

    def capture(self) -> int:
        return self.revision

    def capture_identity(
        self,
        *,
        block_id: int = 0,
        content_revision: int | None = None,
    ) -> ResultIdentity:
        with self._lock:
            rev = (
                int(content_revision)
                if content_revision is not None
                else int(self._block_content.get(int(block_id), 0))
            )
            return ResultIdentity(
                session_id=self._session,
                frame_revision=self._frame_revision,
                state_revision=self._revision,
                block_id=int(block_id),
                content_revision=rev,
            )

    def stale(self, captured: int) -> bool:
        return captured != self.revision

    def accept_identity(self, token: ResultIdentity) -> bool:
        """discard when session/frame/state/content no longer match."""
        with self._lock:
            if token.session_id != self._session:
                return False
            # frame_revision 0 means "not stamped" (legacy TranslationRequest).
            if int(token.frame_revision) and int(token.frame_revision) != int(
                self._frame_revision
            ):
                return False
            if int(token.state_revision) != int(self._revision):
                return False
            if int(token.block_id) and int(token.block_id) in self._block_content:
                if int(token.content_revision) != int(
                    self._block_content[int(token.block_id)]
                ):
                    return False
            return True

    def accept(self, request: TranslationRequest) -> bool:
        """Ответ ещё про тот же кадр и ту же ревизию содержимого."""
        return self.accept_identity(request.identity())

    def clear_results(self):
        with self._lock:
            lanes = list(self._providers.values())
        for lane in lanes:
            with lane.condition:
                lane.results.clear()
                lane.disabled.clear()

    def online(self):
        """Не больше одного одновременного запроса к gtx."""
        return self._gtx

    def cooldown_remaining(self, provider):
        import time

        with self._lock:
            lane = self._providers.get(provider)
        if lane is None:
            return 0.0
        with lane.condition:
            return max(0.0, lane.cooldown_until - time.monotonic())

    def run_provider(self, provider, key, operation, *, cancelled=lambda: False,
                      interval=1.8, debounce=0.0, status=lambda *_: None, retries=4,
                      cooldown=25.0):
        import random
        import time

        from .providers import ProviderError

        with self._lock:
            lane = self._providers.setdefault(provider, ProviderQueue())
        ticket = object()

        def check():
            if cancelled():
                raise Cancelled()

        def wait_until(deadline):
            while deadline > time.monotonic():
                check()
                with lane.condition:
                    lane.condition.wait(min(0.05, deadline - time.monotonic()))
            check()

        check()
        status("waiting", 0)
        with lane.condition:
            if len(lane.queue) >= 128:
                raise ProviderError("queue_full")
            lane.queue.append(ticket)
        admitted = False
        try:
            with lane.condition:
                while lane.active or lane.queue[0] is not ticket:
                    check()
                    lane.condition.wait(0.05)
                check()
                if key[0] in lane.disabled:
                    raise lane.disabled[key[0]]
                if key in lane.results:
                    result = lane.results[key]
                    lane.results.move_to_end(key)
                    if isinstance(result, ProviderError):
                        raise result
                    return result
                if lane.cooldown_error is not None and time.monotonic() < lane.cooldown_until:
                    # Retries were just used up: answer at once, no network, no wait.
                    raise lane.cooldown_error
                lane.active = admitted = True
            wait_until(time.monotonic() + max(0, debounce))
            for attempt in range(retries + 1):
                wait_until(lane.next_start)
                check()
                lane.next_start = time.monotonic() + max(0, interval, lane.minimum_interval)
                try:
                    result = operation()
                    with lane.condition:
                        lane.results[key] = result
                        lane.cooldown_error = None
                        lane.rate_failures = 0
                        lane.minimum_interval *= 0.9
                    # Keep a valid response even if its frame became obsolete.
                    # The next frame may contain exactly the same text.
                    check()
                    return result
                except Cancelled:
                    raise
                except Exception as exc:
                    if isinstance(exc, ProviderError):
                        error = exc
                    else:
                        # Exception strings from HTTP libraries can contain credentials/URLs.
                        import requests

                        category = "timeout" if isinstance(exc, requests.Timeout) else (
                            "network" if isinstance(exc, requests.ConnectionError) else "initialization")
                        error = ProviderError(category)
                    delay = error.retry_after
                    long_pause = delay is not None and delay > MAX_RETRY_AFTER_S
                    if long_pause:
                        # The server asks for a longer pause than is sane to sit
                        # through: report it instead of blocking the lane.
                        attempt = retries
                    if delay is None:
                        delay = min(60.0, 2.0 * 2 ** attempt * random.uniform(1.0, 1.25))
                        if error.category == "rate_limit":
                            delay = min(MAX_RETRY_AFTER_S,
                                        max(0, cooldown) * 2 ** min(lane.rate_failures, 6))
                    if error.category == "rate_limit":
                        lane.rate_failures += 1
                        lane.minimum_interval = min(30.0, 2 * max(interval, lane.minimum_interval))
                        # Publish before checking cancellation: a new OCR frame
                        # must inherit the server's pause, including a long one.
                        with lane.condition:
                            lane.cooldown_until = time.monotonic() + delay
                            lane.cooldown_error = error
                    if error.retryable:
                        lane.next_start = max(lane.next_start, time.monotonic() + delay)
                    check()
                    status(error.category, attempt)
                    if not error.retryable or attempt == retries:
                        with lane.condition:
                            # A transient failure (429, network) must not be
                            # remembered: the same text has to be retried later.
                            if not error.retryable:
                                lane.results[key] = error
                            else:
                                pause = error.retry_after if error.retry_after is not None else cooldown
                                lane.cooldown_until = max(lane.cooldown_until,
                                                          time.monotonic() + pause)
                                lane.cooldown_error = error
                            if error.category in {"authentication", "authentication_or_quota", "quota"}:
                                lane.disabled[key[0]] = error
                        raise error from None
                    status("retrying", attempt + 1)
        finally:
            with lane.condition:
                lane.queue.remove(ticket)
                if admitted:
                    lane.active = False
                while len(lane.results) > 4000:
                    lane.results.popitem(last=False)
                lane.condition.notify_all()


SCHEDULER = Scheduler()


def source_tokens(text: str) -> int:
    """Грубая оценка исходных токенов: слова, минимум одно."""
    parts = [p for p in str(text or "").split() if p]
    return max(1, len(parts))


def pack_local_batch(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Пачки не больше 8 блоков и 512 исходных токенов."""
    batches: list[list[tuple[str, str]]] = []
    buf: list[tuple[str, str]] = []
    tokens = 0
    for key, src in items:
        n = source_tokens(key)
        if buf and (len(buf) >= LOCAL_BATCH_BLOCKS or tokens + n > LOCAL_BATCH_TOKENS):
            batches.append(buf)
            buf, tokens = [], 0
        buf.append((key, src))
        tokens += n
    if buf:
        batches.append(buf)
    return batches


class Cancelled(Exception):
    """Obsolete work; no retry and no result publication."""


class ProviderQueue:
    """FIFO admission, coalescing and provider-wide cooldown; no extra worker threads."""

    def __init__(self):
        from collections import OrderedDict, deque

        self.condition = threading.Condition()
        self.queue = deque()
        self.results = OrderedDict()
        self.active = False
        self.next_start = 0.0
        self.cooldown_until = 0.0
        self.cooldown_error = None
        self.disabled = {}
        self.rate_failures = 0
        self.minimum_interval = 0.0
