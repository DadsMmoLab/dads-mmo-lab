"""T786, two low notes carried over from the T785 review.

(a) A Back up button a finished job gives back must stay grey while the database password is
unknown (`ControllerServices.db_refusal`): four handlers re-enabled it unconditionally.
(b) `InstallChannel._password()` with nothing handed over asks `Install.known_db_password()`, so
an unknown password is refused in its own words, and the Enable press reports that sentence.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_controller_view import WOTLK, _Ps, _services
from tests.test_wotlk_generated_password import ENTRY, a_record
from yulon import channel_setup as setup
from yulon import resources
from yulon.catalog.catalog import DbPasswordUnknown
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

REFUSAL = "This server's database password is not known. Nothing was asked of the database."


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon import runner

    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, tmp_path: Path, refusal: str | None) -> ControllerView:
    services = replace(_services(ps, tmp_path, []), db_refusal=refusal)
    return ControllerView(WOTLK, services, status_poll_ms=0)


@pytest.mark.parametrize(
    "handler",
    [
        "_backup_done",
        "_maintenance_failed",
        "_backup_before_update_done",
        "_backup_before_update_failed",
    ],
)
def test_a_finished_backup_job_does_not_give_back_a_grey_back_up_button(
    qapp: object, ps: _Ps, tmp_path: Path, handler: str
) -> None:
    view = _view(ps, tmp_path, REFUSAL)
    assert not view.backup_button.isEnabled()

    if handler == "_backup_done":
        view._backup_done(object())
    elif handler == "_maintenance_failed":
        view._maintenance_failed(RuntimeError("x"), "Backup failed")
    elif handler == "_backup_before_update_done":
        # Its own line only: the failure it chains to gives the button back again.
        view._backup_before_update_failed = lambda _exc: None  # type: ignore[method-assign]
        view._backup_before_update_done(object())
    else:
        view._backup_before_update_failed(RuntimeError("x"))

    assert not view.backup_button.isEnabled(), "still greyed: the database password is unknown"


def test_a_finished_backup_job_gives_back_the_button_when_the_password_is_known(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path, None)
    view.backup_button.setEnabled(False)

    view._backup_done(object())

    assert view.backup_button.isEnabled()


def _unknown_password_folder(tmp_path: Path) -> Path:
    a_record(tmp_path, generated=True)
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=has$dollar\n", encoding="utf-8")
    return tmp_path


def _channel(server_dir: Path, tmp_path: Path) -> setup.InstallChannel:
    return setup.InstallChannel(
        ENTRY,
        server_dir,
        templates_root=resources.installers_dir(),
        install_id="ab12cd34",
        create=lambda *_a: None,
        channel_for=lambda _endpoint: None,
        config_dir=tmp_path / "config",
    )


def test_with_no_password_handed_over_the_folders_own_is_asked_and_may_be_unknown(
    tmp_path: Path,
) -> None:
    folder = _unknown_password_folder(tmp_path / "wow")
    with pytest.raises(DbPasswordUnknown) as unknown:
        ENTRY.install.known_db_password(folder)

    with pytest.raises(DbPasswordUnknown) as refused:
        _channel(folder, tmp_path)._password()

    assert str(refused.value) == str(unknown.value)


def test_the_enable_press_reports_an_unknown_password_as_its_text_and_writes_nothing(
    tmp_path: Path,
) -> None:
    folder = _unknown_password_folder(tmp_path / "wow")
    with pytest.raises(DbPasswordUnknown) as unknown:
        ENTRY.install.known_db_password(folder)
    before = sorted(p.name for p in folder.iterdir())

    with pytest.raises(setup.EnableRefused) as refused:
        _channel(folder, tmp_path).enable(world_running=False)

    assert str(unknown.value) in str(refused.value)
    assert sorted(p.name for p in folder.iterdir()) == before, "nothing was written"


def test_is_enabled_says_not_known_for_an_unknown_password(tmp_path: Path) -> None:
    folder = _unknown_password_folder(tmp_path / "wow")

    assert _channel(folder, tmp_path).is_enabled() is None
