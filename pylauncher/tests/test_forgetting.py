"""What removing a server from Yu'lon says (T95): one sentence per install type, each true of it."""

from __future__ import annotations

from pathlib import Path

from yulon import forgetting


def _facts(tmp_path: Path, **changes: object) -> forgetting.Facts:
    base: dict[str, object] = {
        "name": "WoW TBC",
        "server_dir": tmp_path,
        "wsl_distro": None,
        "folder_gone": False,
        "running": False,
    }
    base.update(changes)
    return forgetting.Facts(**base)  # type: ignore[arg-type]


def test_every_question_says_what_goes_and_that_nothing_is_deleted(tmp_path: Path) -> None:
    said = forgetting.question(_facts(tmp_path))
    assert f"Yu'lon stops listing WoW TBC at {tmp_path}" in said
    # Final review (T95): a second install of the same game keeps the tile
    # "Installed", so the Catalog does not forget the game.
    assert "Catalog forgets" not in said
    assert "Nothing is deleted" in said
    assert "database volume (your characters)" in said
    assert "Docker images" in said


def test_every_server_whose_folder_exists_is_said_to_be_stopped_first_if_it_runs(
    tmp_path: Path,
) -> None:
    """The stop always runs (T95, final review); the last poll only picks the words."""
    assert "It is running, so it is stopped first" in forgetting.question(
        _facts(tmp_path, running=True)
    )
    for unsure in (False, None):
        said = forgetting.question(_facts(tmp_path, running=unsure))
        assert "If it is running, it is stopped first" in said, unsure
        assert "It is running" not in said, unsure


def test_the_way_back_is_use_existing_and_never_a_reinstall(tmp_path: Path) -> None:
    """The m910q gate (T95) refuted "Install into the same folder resumes".

    The install preflight's disk check ran before the installer read its
    resume record, and refused the resume as a fresh install ("21 GB free,
    the install needs 40 GB"; T112). "Use existing…" brought the server back.
    Adopted installs were never offered it: Install into a folder with no
    record is refused (`native.py:5551-5563`).
    """
    said = forgetting.question(_facts(tmp_path))
    assert 'press "Use existing…" on the WoW TBC tile in the Catalog and pick this folder' in said
    assert "Installing" not in said and "resum" not in said and "skips" not in said


def test_a_wsl_install_is_pointed_at_find_in_wsl(tmp_path: Path) -> None:
    said = forgetting.question(_facts(tmp_path, wsl_distro="Ubuntu-24.04"))
    assert '"Find in WSL…"' in said and "Ubuntu-24.04" in said
    assert "Use existing" not in said


def test_a_gone_folder_keeps_t34s_promise_about_docker(tmp_path: Path) -> None:
    """No folder, no provable project: Docker is left alone and nothing is offered back."""
    gone = tmp_path / "gone"
    said = forgetting.question(_facts(gone, folder_gone=True, running=True))
    assert f"{gone} no longer exists" in said
    assert "NOT touched" in said
    assert "Nothing is deleted" not in said
    assert "stopped first" not in said
    assert "bring it back" not in said


def test_the_stop_failure_question_names_the_reason_and_what_yes_costs() -> None:
    said = forgetting.stop_failed_question("WoW TBC", "Docker would not say who owns tbc-db")
    assert "Docker would not say who owns tbc-db" in said
    assert "anyway" in said
    assert "keeps running with nothing in Yu'lon managing it" in said
