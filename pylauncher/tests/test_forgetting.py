"""What removing a server from Yu'lon says (T95): one sentence per install type, each true of it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from yulon import forgetting
from yulon.catalog import composegen


def _facts(tmp_path: Path, **changes: object) -> forgetting.Facts:
    base: dict[str, object] = {
        "name": "WoW TBC",
        "server_dir": tmp_path,
        "wsl_distro": None,
        "folder_gone": False,
        "stop_first": False,
        "built_here": False,
    }
    base.update(changes)
    return forgetting.Facts(**base)  # type: ignore[arg-type]


def test_every_question_says_what_goes_and_that_nothing_is_deleted(tmp_path: Path) -> None:
    said = forgetting.question(_facts(tmp_path))
    assert f"Yu'lon stops listing WoW TBC at {tmp_path}" in said
    assert "Nothing is deleted" in said
    assert "database volume (your characters)" in said
    assert "Docker images" in said


def test_a_server_that_may_be_running_is_said_to_be_stopped_first_and_only_then(
    tmp_path: Path,
) -> None:
    assert "stopped first" in forgetting.question(_facts(tmp_path, stop_first=True))
    assert "stopped first" not in forgetting.question(_facts(tmp_path, stop_first=False))


def test_an_adopted_install_is_never_told_to_install_into_its_own_folder(tmp_path: Path) -> None:
    """Install into a folder with no record is REFUSED (`native.py:5551-5563`): offering it lies."""
    said = forgetting.question(_facts(tmp_path, built_here=False))
    assert 'press "Use existing…" on the WoW TBC tile' in said
    assert "Installing" not in said and "resum" not in said


def test_a_folder_yulon_built_is_offered_both_ways_back(tmp_path: Path) -> None:
    said = forgetting.question(_facts(tmp_path, built_here=True))
    assert 'press "Use existing…" on the WoW TBC tile' in said
    assert "Installing WoW TBC into this same folder also works" in said
    assert "skips every step already done and starts the server" in said


def test_a_wsl_install_is_pointed_at_find_in_wsl(tmp_path: Path) -> None:
    said = forgetting.question(_facts(tmp_path, wsl_distro="Ubuntu-24.04", built_here=True))
    assert '"Find in WSL…"' in said and "Ubuntu-24.04" in said
    assert "Use existing" not in said


def test_a_gone_folder_keeps_t34s_promise_about_docker(tmp_path: Path) -> None:
    """No folder, no provable project: Docker is left alone and nothing is offered back."""
    gone = tmp_path / "gone"
    said = forgetting.question(_facts(gone, folder_gone=True, stop_first=True, built_here=True))
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


def test_built_here_asks_ownership_not_presence(tmp_path: Path) -> None:
    """A copied folder carries a record naming the ORIGINAL's path (`apply.server_dir_claim`)."""
    ours = tmp_path / "ours"
    ours.mkdir()
    record = {
        "version": 1,
        "game_id": "wow-tbc",
        "family": "cmangos",
        "install_id": composegen.install_id(ours),
        "completed": ["clone-sources"],
        "updated_unix": 1788481989,
    }
    (ours / ".yulon-install.json").write_text(json.dumps(record), encoding="utf-8")
    assert forgetting.built_here(ours) is True

    copy = tmp_path / "copy"
    shutil.copytree(ours, copy)
    assert forgetting.built_here(copy) is False, "a copy was told Install resumes there"

    bare = tmp_path / "bare"
    bare.mkdir()
    assert forgetting.built_here(bare) is False
