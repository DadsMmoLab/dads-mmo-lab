"""Revive on a Centurion server comes back only on a build that carries the fork's fix (T218).

#295 withheld Revive because the fork's console `revive` read through a null session
(`cs_misc.cpp:795`) and took the world server down for every console or SOAP command.
thomasjteachey/TrinityCore112 #1767 fixed it (CENTURION 4948d1a9, the catalog's pin since
2026-10-08). The button is drawn again, but only for a server whose checkout is on a build that
has the fix: a server made before the pin moved is still the crashing build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import process_events
from yulon import git, wsl
from yulon import play as play_module
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller_wow_centurion import characters
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

ENTRY: CatalogEntry = load_catalog().get("wow-centurion")
(SOURCE,) = ENTRY.emulator.sources
PIN = SOURCE.rev
assert PIN is not None
OLD_PIN = "6c6472c3b6aeb89169d7d49c45af7f7eab326743"
LATER = "f" * 40


def _server(tmp_path: Path, head: str | None = PIN, *, ref: str | None = None) -> Path:
    """A Centurion folder whose checkout is on `head` (detached), or on a branch `ref`."""
    server = tmp_path / "centurion"
    git_dir = server / SOURCE.dest / ".git"
    git_dir.mkdir(parents=True)
    (server / ".db_password").write_text("tc-0123456789abcdef\n", encoding="utf-8")
    if ref is not None:
        (git_dir / "HEAD").write_text(f"ref: refs/heads/{ref}\n", encoding="utf-8")
    elif head is not None:
        (git_dir / "HEAD").write_text(f"{head}\n", encoding="utf-8")
    _state(server)
    return server


def _state(
    server: Path,
    *,
    completed: tuple[str, ...] = ("build",),
    last_error: str = "",
    rows: tuple[native.SourceRev, ...] = (),
) -> None:
    """The install record: what a finished install leaves (`build` done, no error)."""
    native.write_state(
        server,
        native.InstallState(
            game_id=ENTRY.id,
            install_id="x",
            family="trinitycore",
            completed=completed,
            last_error=last_error,
            source_revs=rows,
        ),
    )


def _recorded(server: Path, *, built: str, pin: str) -> None:
    """What `_record_source_revs()` writes after an Update to latest / Return to the pin."""
    _state(server, rows=(native.SourceRev(repo=SOURCE.repo, built=built, pin=pin, ahead=None),))


def test_the_shipped_pin_carries_the_revive_fix() -> None:
    """Whoever moves the pin reads that #1767 is still in it, then adds it to the set."""
    assert PIN in characters.REVIVE_FIXED_PINS


def test_a_server_on_the_pin_is_offered_revive(tmp_path: Path) -> None:
    server = _server(tmp_path)
    assert characters.revive_is_fixed(ENTRY, server)
    assert dict(characters.withheld(ENTRY, server)) == {}


def test_a_server_on_the_old_pin_keeps_revive_withheld_with_the_update_sentence(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, head=OLD_PIN)
    assert not characters.revive_is_fixed(ENTRY, server)
    assert dict(characters.withheld(ENTRY, server)) == {"revive": characters.REVIVE_NEEDS_UPDATE}
    assert "Update the server to latest" in characters.REVIVE_NEEDS_UPDATE


def test_a_folder_without_a_checkout_or_without_a_folder_withholds_revive(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not characters.revive_is_fixed(ENTRY, empty)
    assert not characters.revive_is_fixed(ENTRY, None)
    assert "revive" in characters.withheld(ENTRY)
    assert "revive" in characters.withheld(ENTRY, empty)


def test_a_checkout_on_the_pin_without_a_finished_build_is_not_trusted(tmp_path: Path) -> None:
    """An update that moved the source and then failed leaves the old binary (Codex)."""
    server = _server(tmp_path)
    _state(server, completed=())
    assert not characters.revive_is_fixed(ENTRY, server), "build not recorded"
    _state(server, last_error="the build was stopped.")
    assert not characters.revive_is_fixed(ENTRY, server), "a press failed or was stopped since"
    (server / native.STATE_FILE).unlink()
    assert not characters.revive_is_fixed(ENTRY, server), "no install record at all"
    _state(server)
    assert characters.revive_is_fixed(ENTRY, server)


def test_an_unreadable_head_withholds_revive(tmp_path: Path) -> None:
    server = _server(tmp_path, head=None)
    assert not characters.revive_is_fixed(ENTRY, server)
    (server / SOURCE.dest / ".git" / "HEAD").write_bytes(b"\xff\xfe\x00")
    assert not characters.revive_is_fixed(ENTRY, server)


def test_a_checkout_on_a_branch_is_read_through_its_ref(tmp_path: Path) -> None:
    server = _server(tmp_path, ref="CENTURION")
    git_dir = server / SOURCE.dest / ".git"
    assert not characters.revive_is_fixed(ENTRY, server), "a branch that resolves to nothing"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "refs" / "heads" / "CENTURION").write_text(PIN + "\n", encoding="utf-8")
    assert characters.revive_is_fixed(ENTRY, server)


def test_a_branch_that_is_only_in_packed_refs_is_read_too(tmp_path: Path) -> None:
    server = _server(tmp_path, ref="CENTURION")
    packed = server / SOURCE.dest / ".git" / "packed-refs"
    packed.write_text(
        f"# pack-refs with: peeled\n{OLD_PIN} refs/heads/other\n{PIN} refs/heads/CENTURION\n",
        encoding="utf-8",
    )
    assert characters.revive_is_fixed(ENTRY, server)


def test_an_update_to_latest_made_while_the_catalog_had_this_pin_is_offered_revive(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, head=LATER)
    _recorded(server, built=f"{LATER[:7]} · 2026-10-09", pin=PIN)
    assert characters.revive_is_fixed(ENTRY, server)


def test_an_update_made_against_the_old_pin_is_not(tmp_path: Path) -> None:
    """It may have landed between 6c6472c3 and the fix: nothing says it has the fix."""
    server = _server(tmp_path, head=LATER)
    _recorded(server, built=f"{LATER[:7]} · 2026-10-07", pin=OLD_PIN)
    assert not characters.revive_is_fixed(ENTRY, server)


def test_a_record_that_names_another_commit_than_the_checkout_is_not_trusted(
    tmp_path: Path,
) -> None:
    server = _server(tmp_path, head=LATER)
    _recorded(server, built=f"{'e' * 7} · 2026-10-09", pin=PIN)
    assert not characters.revive_is_fixed(ENTRY, server)


def test_a_pin_that_nobody_has_checked_withholds_revive_with_the_old_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin moved without adding it to the set: Revive goes off again, and says it crashed."""
    server = _server(tmp_path)
    monkeypatch.setattr(characters, "REVIVE_FIXED_PINS", frozenset({OLD_PIN}))
    assert not characters.revive_is_fixed(ENTRY, server)
    assert dict(characters.withheld(ENTRY, server)) == {"revive": characters.REVIVE_CRASHED}


def test_the_other_verbs_stay_as_they_were(tmp_path: Path) -> None:
    server = _server(tmp_path, head=OLD_PIN)
    assert set(characters.withheld(ENTRY, server)) == {"revive"}
    assert set(play_module.VERBS) - {"revive"} == set(characters.CONFIRMED_LIVE)


def test_the_tab_draws_revive_on_a_server_on_the_pin_and_not_on_the_old_one(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Modules tab asks the checkout what is new upstream through a container; nothing here.
    monkeypatch.setattr(git.ContainerGit, "head_sha", lambda self, dest: None)
    fixed = ControllerServices.for_entry(ENTRY, _server(tmp_path / "a"))
    view = ControllerView(ENTRY, fixed, status_poll_ms=0, job_runner=run_inline)
    process_events()
    assert dict(fixed.characters_withheld) == {}
    assert view.revive_button in view.character_buttons()
    assert view.characters_withheld_label.isHidden()

    old = ControllerServices.for_entry(ENTRY, _server(tmp_path / "b", head=OLD_PIN))
    stale = ControllerView(ENTRY, old, status_poll_ms=0, job_runner=run_inline)
    assert stale.revive_button not in stale.character_buttons()
    assert stale.characters_withheld_label.text().startswith(
        "Not offered on this server yet: Revive — this server was built before Revive was fixed"
    )


def test_a_server_in_a_distro_that_is_not_running_is_not_read_and_keeps_revive_withheld(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading its checkout would boot the distro (T133): withheld, and nothing opened."""
    server = _server(tmp_path)
    monkeypatch.setattr(wsl, "may_read", lambda distro: False)
    opened: list[object] = []
    monkeypatch.setattr(characters, "_checkout_head", lambda path: opened.append(path))
    services = ControllerServices.for_entry(ENTRY, server, None, "Ubuntu-24.04")
    assert set(services.characters_withheld) == {"revive"}
    assert opened == []


def test_a_server_in_a_running_distro_is_read_like_a_local_one(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _server(tmp_path)
    monkeypatch.setattr(wsl, "may_read", lambda distro: True)
    services = ControllerServices.for_entry(ENTRY, server, None, "Ubuntu-24.04")
    assert dict(services.characters_withheld) == {}
