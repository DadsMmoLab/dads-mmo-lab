"""Shared pytest fixtures: an offscreen Qt application, and a pinned docker CLI name."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from yulon import apply as apply_module
from yulon import platform

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _docker_cli_is_the_plain_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every argv assertion in the suite gets `docker` at argv[0], on any machine.

    `platform.docker_program()` answers with what THIS host has: the plain name
    where docker is on the live PATH, an absolute `...\\resources\\bin\\docker.exe`
    on a Windows box where it is not, and `None` where Docker is not installed
    at all. All three are correct, and all three would be baked into the ~60
    tests that assert `["docker", "compose", ...]` — which would then pass or
    fail on the developer's Docker install rather than on the code. Pinning the
    resolved value keeps those tests about argv *shape*, and keeps the promise
    that the suite needs no Docker (and no Windows install) to run.

    Pinning the cache rather than the function leaves `docker_programs()` and
    `docker_ready()` untouched, so `test_provision.py` still exercises the real
    resolution; the tests that exercise `docker_program()` itself set the cache
    back to None first, and a `monkeypatch` from the test body wins over this
    one.
    """
    monkeypatch.setattr(platform, "_resolved_docker_cli", "docker")


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """One offscreen `QApplication` for the whole session (Qt allows exactly one)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def process_events(ms: int = 50) -> None:
    """Pump the Qt event loop for `ms` milliseconds (lets queued signals deliver)."""
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop

    deadline = QDeadlineTimer(ms)
    while not deadline.hasExpired():
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test block on a modal dialog.

    `QMessageBox.warning()` and friends are modal: called from a slot in an
    offscreen test they wait for a click that never comes, and the whole run
    hangs. Tests that care about a dialog patch it themselves and see their own
    patch (this one is applied first).
    """
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:  # pragma: no cover - Qt-less environments skip UI tests anyway
        return
    for name in ("warning", "information", "critical", "about"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture(autouse=True)
def _classic_mysql_client_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the client probe without touching the seam the tests assert on.

    `apply.mysql_client()` asks the database container whether it has `mysql` or
    only `mariadb` (mariadb:11 dropped the mysql* symlinks). That probe is a
    `docker exec` like every other call in these modules, so left real it would
    appear in argv assertions that are about SQL. Answering None here means the
    classic names are used, which is what every test written before the probe
    existed expects; `test_apply.py` drives the resolver itself directly.
    """
    monkeypatch.setattr(apply_module, "_probe_client", lambda container, candidates: None)
    apply_module._client_cache.clear()
