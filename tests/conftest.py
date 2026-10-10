"""Маркер для тестов, которым нужен настоящий хост.

Зачем маркер, а не «они и так падают в песочнице».

Nix-сборка идёт в песочнице, где нет ни `/bin`, ни установленных шрифтов. Часть
тестов проверяет честно именно их: зовёт `/bin/true`, меряет глифы системным
шрифтом, поднимает процессы селектора. В песочнице они падают не из-за кода, а
из-за отсутствующей предпосылки, и без соглашения это неотличимо от настоящей
поломки - сборка просто красная, и разбираться приходится заново каждый раз.

Поэтому предпосылка названа явно:

    uv run pytest -q                   # хост: всё, включая needs_host, проходит
    nix flake check                    # песочница: needs_host пропускается

Пропуск проверяет наличие, а не флажок: на машине, где `/bin/true` есть, тест
выполняется, даже если маркер стоит.
"""

from __future__ import annotations

import os
import shutil

import pytest

#: Что песочница nix не даёт и что нужно этой части тестов.
_HOST_PRECONDITION = "/bin/true"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"needs_host: requires {_HOST_PRECONDITION} and installed system fonts, "
        "which a nix build sandbox has neither of",
    )


def _host_is_complete() -> bool:
    return os.path.isfile(_HOST_PRECONDITION) and shutil.which("true") is not None


@pytest.fixture(autouse=True)
def _needs_host_precondition(request: pytest.FixtureRequest) -> None:
    if "needs_host" not in request.keywords:
        return
    if not _host_is_complete():
        pytest.skip(
            f"нет {_HOST_PRECONDITION} - песочница сборки без корня POSIX"
        )


@pytest.fixture(autouse=True)
def _never_touch_the_real_config(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Tests read and write a throwaway config, never the user's own.

    ``doctor`` and the settings path load the config, and ``apply_config`` saves it:
    with the real file a test passed or failed depending on what the user had
    switched on in Settings that day.
    """
    monkeypatch.setenv("KIZURIUM_TRANSLATOR_CONFIG", str(tmp_path_factory.mktemp("cfg") / "config.toml"))


@pytest.fixture(autouse=True)
def _never_touch_the_real_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test must neither start the application nor talk to a running one.

    ``desktop.forward`` goes to the user's session bus: with the app open, a
    ``--stop`` test would stop the real session. ``desktop.run`` creates a real
    GApplication, which blocks or aborts the test process. A test that needs
    either replaces it explicitly.
    """
    try:
        from kizurium_translator import desktop
    except Exception:  # noqa: BLE001 - tests that do not need it must still run
        return

    def no_run(*_a, **_k):
        raise AssertionError("a test started the real application (desktop.run)")

    monkeypatch.setattr(desktop, "forward", lambda _action: False)
    monkeypatch.setattr(desktop, "run", no_run)
