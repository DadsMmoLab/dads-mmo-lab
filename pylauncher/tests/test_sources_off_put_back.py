"""T766: "Put the folders back" -- a source folder `SOURCES_OFF_FILE` names, put back on its build.

Before this, the only way out of a folder off the commit its build came from was the git
command the refusal prints, and then Rebuild. T217 Decision 9 stays: mixed image tags are
repaired by a Rebuild alone, never by moving the sources under them. The press runs what the
printed command does for every folder the record names -- `restore_rev()`, a checkout with
`--force`, through `apply.module_git()` so a WSL server's folder is reached through its own
distro -- and then writes this app's own files back, because the checkout put upstream's
copies over them and Rebuild refuses those. After it, Rebuild is no longer refused for the
folder.

Round 2: the press holds the server's reservation (T568) like the other writing presses, it
refuses while the world server runs, and it writes the app's own files back on every press,
so a press that stopped half-way is finished by pressing again.

The checkout runs against a real git repository in each test: the behaviour under test is
git's, and a double would hide it. This laptop never runs docker, so every test here makes
`ContainerGit._launcher` fail, and every press runs with a fake reservation and a world that
reads as stopped unless the test says otherwise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, UPSTREAM_COMPOSE, Recorder, engine
from tests.test_rebuild_start_records import a_finished_install
from yulon import docker, git, server_build_presses
from yulon.catalog import composegen, native
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

CORE = ENTRY.emulator.sources[0]
PUT_BACK = server_build_presses.PUT_THE_FOLDERS_BACK
REBUILD = server_build_presses.under_server_build(server_build_presses.REBUILD)


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A containerised git would run docker, and this laptop never does (live gates run on VMs)."""

    def refuse(self: git.ContainerGit) -> list[str]:
        raise AssertionError("a test here asked for the containerised git")

    monkeypatch.setattr(git.ContainerGit, "_launcher", refuse)


@contextmanager
def _unclaimed(server_dir: Path, *, press: str, **_kw: Any) -> Iterator[None]:
    """A reservation asked for and granted as nothing: no docker, nothing held."""
    yield None


def _stopped(container: str) -> bool:
    return False


def _running(container: str) -> bool:
    return True


def _press_engine(
    rec: Recorder, *, claim: Any = _unclaimed, world: Any = _stopped
) -> AzerothCoreInstaller:
    """The engine a press runs under in these tests."""
    return engine(rec, server_claim=claim, world_running=world)


def _git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _commit(folder: Path, message: str) -> str:
    _git(
        folder,
        "-c",
        "user.name=yulon-test",
        "-c",
        "user.email=yulon-test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(folder, "rev-parse", "HEAD")


def _two_commits(folder: Path) -> tuple[str, str]:
    """A real repository: the build's commit, then a later one, detached on the later one."""
    folder.mkdir(parents=True, exist_ok=True)
    _git(folder, "init", "-q")
    (folder / "docker-compose.yml").write_text(UPSTREAM_COMPOSE, encoding="utf-8")
    _git(folder, "add", "docker-compose.yml")
    built = _commit(folder, "the commit the build was made from")
    (folder / "stray.txt").write_text("a commit an update left the folder on\n", encoding="utf-8")
    _git(folder, "add", "stray.txt")
    stray = _commit(folder, "the commit the folder was left on")
    _git(folder, "checkout", "--detach", "-q", stray)
    return built, stray


def _off_core(tmp_path: Path) -> tuple[Recorder, Path, str, str]:
    """A finished WotLK install whose server folder is a real checkout, off its build.

    The record names the commit the build came from; the folder is detached on a later one,
    which is what an update that could not put it back leaves. The app's own compose files
    are written over the tracked `docker-compose.yml`, as an update leaves them: git sees the
    file as changed, and the change is this app's.
    """
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    marked = {
        name: (server_dir / name).read_text(encoding="utf-8") for name in composegen.COMPOSE_FILES
    }
    shutil.rmtree(server_dir / ".git", ignore_errors=True)
    built, stray = _two_commits(server_dir)
    for name, text in marked.items():
        (server_dir / name).write_text(text, encoding="utf-8")
    rec.heads[server_dir] = stray
    assert native.remember_sources_off(server_dir, [(CORE.repo, server_dir, built)]) == ""
    return rec, server_dir, built, stray


def _spy_halves(
    made: Any, calls: list[str], *, patch_errors: list[Exception] | None = None
) -> None:
    """Record each write-back half the press runs; the patch half fails once per error given."""
    compose = made._rewrite_what_we_own
    carried = made.apply_carried_patches

    def rewrite(server_dir: Path, opts: InstallOptions, state: Any) -> Iterator[str]:
        calls.append("compose")
        return compose(server_dir, opts, state)

    def patch(server_dir: Path) -> Iterator[str]:
        calls.append("patch")
        if patch_errors:
            raise patch_errors.pop(0)
        yield from carried(server_dir)

    made._rewrite_what_we_own = rewrite
    made.apply_carried_patches = patch


def test_the_put_back_checks_out_the_build_commit_and_clears_the_record(tmp_path: Path) -> None:
    rec, server_dir, built, _stray = _off_core(tmp_path)
    made = _press_engine(rec)
    assert native.sources_off_refusal(server_dir) is not None, "the ground: Start refuses"

    said = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert native.read_head_file(server_dir) == built
    gone = "checkout --force removes what only the stray commit added"
    assert not (server_dir / "stray.txt").exists(), gone
    assert set(native.generated_compose_files(server_dir)) == set(composegen.COMPOSE_FILES)
    # Read before anything asks sources_still_off(), which forgets a record with no folder off.
    assert not (server_dir / native.SOURCES_OFF_FILE).exists()
    assert native.sources_off_refusal(server_dir) is None
    assert any(f"was put back on {built[:7]}" in line for line in said), said


def test_after_the_put_back_rebuild_is_no_longer_refused_for_the_folder(tmp_path: Path) -> None:
    rec, server_dir, built, _stray = _off_core(tmp_path)
    rec.images = True
    # The real T766 state: the mixed tags are owed a Rebuild (START_REFUSED_FILE, why "rebuild").
    assert native.owe_start(server_dir) == ""
    made = _press_engine(rec)
    assert native.owed_start_refusal(server_dir) == native.REBUILD_OWED_REFUSAL
    assert made.rebuild_refusal_before_asking(server_dir) is not None, "the ground: Rebuild refuses"

    list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))
    rec.heads[server_dir] = built  # the Recorder's own head answer, as git now gives it

    assert made.rebuild_refusal_before_asking(server_dir) is None
    assert not (server_dir / native.WRITE_BACK_OWED_FILE).exists(), "a whole press clears it"
    assert native.owed_start_refusal(server_dir) == native.REBUILD_OWED_REFUSAL, "Start waits"
    made._refuse_unless_rebuildable(server_dir)  # the compose files are this app's again
    list(made.rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" in rec.calls, rec.calls


def test_the_put_back_refuses_a_folder_with_edits_of_the_players_own_and_changes_nothing(
    tmp_path: Path,
) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)
    (server_dir / "stray.txt").write_text("my own note\n", encoding="utf-8")

    with pytest.raises(InstallerError) as refused:
        list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    said = str(refused.value)
    assert "stray.txt" in said and "changes of your own" in said, said
    assert "throw them away" in said, said
    assert said.endswith("Nothing was changed."), said
    assert native.read_head_file(server_dir) == stray
    assert (server_dir / "stray.txt").read_text(encoding="utf-8") == "my own note\n"
    assert {row.built for row in native.sources_still_off(server_dir)} == {built}


def test_no_folder_is_put_back_while_another_folder_has_edits_of_the_players_own(
    tmp_path: Path,
) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)
    elsewhere = server_dir / "elsewhere"
    elsewhere_built, _elsewhere_stray = _two_commits(elsewhere)
    assert (
        native.remember_sources_off(
            server_dir,
            [(CORE.repo, server_dir, built), ("other/repo", elsewhere, elsewhere_built)],
        )
        == ""
    )
    (elsewhere / "stray.txt").write_text("my own note\n", encoding="utf-8")

    with pytest.raises(InstallerError) as refused:
        list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert str(elsewhere) in str(refused.value), refused.value
    assert native.read_head_file(server_dir) == stray, "the clean folder was not moved either"


def test_a_folder_git_cannot_be_asked_about_is_refused_not_taken_as_clean(tmp_path: Path) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    shutil.rmtree(server_dir / ".git")

    with pytest.raises(InstallerError) as refused:
        list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert "could not ask git" in str(refused.value), refused.value
    assert "Nothing was changed." in str(refused.value), refused.value
    assert (server_dir / native.SOURCES_OFF_FILE).exists()


def test_a_folder_git_will_not_put_back_keeps_its_row_and_the_others_are_cleared(
    tmp_path: Path,
) -> None:
    rec, server_dir, built, _stray = _off_core(tmp_path)
    elsewhere = server_dir / "elsewhere"
    _two_commits(elsewhere)
    missing = "d" * 40  # a commit this repository does not have
    assert (
        native.remember_sources_off(
            server_dir,
            [(CORE.repo, server_dir, built), ("other/repo", elsewhere, missing)],
        )
        == ""
    )

    said = list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert native.read_head_file(server_dir) == built
    # The file itself, read before sources_still_off() can rewrite it: the restored row is gone.
    record = json.loads((server_dir / native.SOURCES_OFF_FILE).read_text(encoding="utf-8"))
    assert record["sources"] == [{"repo": "other/repo", "dest": str(elsewhere), "commit": missing}]
    assert [row.dest for row in native.sources_still_off(server_dir)] == [elsewhere]
    assert [row.built for row in native.sources_still_off(server_dir)] == [missing]
    assert any("could NOT be put back" in line for line in said), said
    failed = [line for line in said if "could NOT be put back" in line]
    # Round 4: a player line names no command (T248); the press stays offered, so it is the way.
    assert not any("git -C" in line or "checkout" in line for line in failed), failed
    assert PUT_BACK in failed[0] and "again" in failed[0], failed
    assert "support file" in failed[0], failed
    assert native.sources_off_refusal(server_dir) is not None


def test_no_install_record_means_nothing_is_put_back(tmp_path: Path) -> None:
    rec, server_dir, _built, stray = _off_core(tmp_path)
    (server_dir / native.STATE_FILE).unlink()

    with pytest.raises(InstallerError):
        list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert native.read_head_file(server_dir) == stray


def test_the_put_back_goes_through_the_module_git_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, built, _stray = _off_core(tmp_path)
    real = native.module_git
    asked: list[Path] = []

    def spy(folder: Path) -> object:
        asked.append(folder)
        return real(folder)

    monkeypatch.setattr(native, "module_git", spy)

    list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert asked and set(asked) == {server_dir}, asked
    assert native.read_head_file(server_dir) == built


def test_a_held_reservation_refuses_the_press_and_changes_nothing(tmp_path: Path) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)
    asked: list[tuple[Path, str]] = []

    @contextmanager
    def held(folder: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        asked.append((Path(folder), press))
        raise docker.ServerHeldError("Another Yu'lon is working on this server right now.")
        yield

    with pytest.raises(InstallerError, match="Another Yu'lon"):
        list(
            _press_engine(rec, claim=held).put_the_folders_back(
                InstallOptions(server_dir=server_dir)
            )
        )

    assert asked == [(server_dir, PUT_BACK)], asked
    assert native.read_head_file(server_dir) == stray
    assert {row.built for row in native.sources_still_off(server_dir)} == {built}


def test_the_press_reserves_the_folder_it_was_asked_for(tmp_path: Path) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    reserved: list[tuple[Path, str]] = []

    @contextmanager
    def recording(folder: Path, *, press: str, **_kw: Any) -> Iterator[None]:
        reserved.append((Path(folder), press))
        yield None

    list(
        _press_engine(rec, claim=recording).put_the_folders_back(
            InstallOptions(server_dir=server_dir)
        )
    )

    assert reserved == [(server_dir, PUT_BACK)], reserved


def test_a_running_world_refuses_the_press_and_changes_nothing(tmp_path: Path) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)

    with pytest.raises(InstallerError, match="is running") as refused:
        list(
            _press_engine(rec, world=_running).put_the_folders_back(
                InstallOptions(server_dir=server_dir)
            )
        )

    message = str(refused.value)
    assert "database is started" not in message, message
    assert "Put the folders back" in message and "again" in message, message
    assert native.read_head_file(server_dir) == stray
    assert {row.built for row in native.sources_still_off(server_dir)} == {built}


def test_a_second_press_repeats_the_patch_and_leaves_the_compose_files_alone(
    tmp_path: Path,
) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    made = _press_engine(rec)
    list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))
    calls: list[str] = []
    _spy_halves(made, calls)

    said = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert calls == ["patch"], calls
    assert any("already on the commit" in line for line in said), said
    assert any(f"Press {REBUILD}" in line for line in said), said


def test_a_failed_patch_write_is_completed_by_pressing_again(tmp_path: Path) -> None:
    rec, server_dir, built, _stray = _off_core(tmp_path)
    made = _press_engine(rec)
    calls: list[str] = []
    _spy_halves(made, calls, patch_errors=[InstallerError("the patch file could not be read")])

    first = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert native.read_head_file(server_dir) == built, "the folder is back on its build"
    assert not (server_dir / native.SOURCES_OFF_FILE).exists()
    failed = [line for line in first if "could not be written into them again" in line]
    assert failed, first
    assert PUT_BACK in failed[0] and "again" in failed[0], failed[0]
    assert not any(f"Press {REBUILD}" in line for line in first), first
    # Round 4: the record is cleared and the compose files are this app's again, so only the
    # write-back marker says the patch is owed: the press is offered, and Rebuild refuses.
    assert (server_dir / native.WRITE_BACK_OWED_FILE).exists()
    assert native.put_the_folders_back_offered(server_dir) is True
    refused = made.rebuild_refusal_before_asking(server_dir)
    assert refused is not None and PUT_BACK in refused, refused
    rec.images = True
    rec.heads[server_dir] = built
    with pytest.raises(InstallerError, match=PUT_BACK):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))
    assert "build" not in rec.calls, rec.calls

    second = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert calls == ["compose", "patch", "patch"], calls
    assert not [line for line in second if "could not" in line], second
    assert any(f"Press {REBUILD}" in line for line in second), second
    assert not (server_dir / native.WRITE_BACK_OWED_FILE).exists()
    assert native.put_the_folders_back_offered(server_dir) is False
    assert made.rebuild_refusal_before_asking(server_dir) is None


def test_a_folder_is_keyed_the_same_whichever_wsl_share_spelling_names_it() -> None:
    server = Path(r"\\wsl$\Ubuntu\home\pk\wow")
    shared = Path(r"\\wsl.localhost\Ubuntu\home\pk\wow")
    assert native._folder_key(server, shared) == "."
    assert native._folder_key(server, shared / "modules" / "mod-x") == "modules/mod-x"
    assert native._folder_key(Path("/srv/wow"), Path("/srv/wow/modules/mod-x")) == "modules/mod-x"


def test_a_stop_before_any_folder_moves_changes_nothing(tmp_path: Path) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)
    stop = threading.Event()
    stop.set()

    said = list(
        _press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir), cancel=stop)
    )

    assert native.read_head_file(server_dir) == stray
    assert {row.built for row in native.sources_still_off(server_dir)} == {built}
    assert any("Stopped before any folder moved" in line for line in said), said


def test_a_players_stop_after_the_first_checkout_still_ends_with_the_write_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder has moved, so the player's Stop waits for the write-back to finish."""
    rec, server_dir, built, _stray = _off_core(tmp_path)
    stop = threading.Event()
    made = _press_engine(rec)
    calls: list[str] = []
    _spy_halves(made, calls)
    real_put = native.put_sources_off_back

    def put_then_stop(folder_server: Path, off: Any) -> Any:
        done = real_put(folder_server, off)
        stop.set()  # the player's Stop lands once the folder is on its build
        return done

    monkeypatch.setattr(native, "put_sources_off_back", put_then_stop)

    said = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir), cancel=stop))

    assert native.read_head_file(server_dir) == built
    assert calls == ["compose", "patch"], calls
    assert not any("Stopped" in line for line in said), said
    assert any(f"Press {REBUILD}" in line for line in said), said


def test_a_lost_reservation_after_the_first_checkout_stops_before_the_write_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another Yu'lon took the reservation: the write-back waits for the next press."""
    rec, server_dir, built, _stray = _off_core(tmp_path)
    stop = threading.Event()
    lost = threading.Event()
    stop.reservation_lost = lost  # as `_end_on_loss()` records it on the press's cancel
    made = _press_engine(rec)
    calls: list[str] = []
    _spy_halves(made, calls)
    real_put = native.put_sources_off_back

    def put_then_lose(folder_server: Path, off: Any) -> Any:
        done = real_put(folder_server, off)
        lost.set()
        stop.set()  # the loss watcher sets the press's cancel as well
        return done

    monkeypatch.setattr(native, "put_sources_off_back", put_then_lose)

    first = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir), cancel=stop))

    assert native.read_head_file(server_dir) == built
    assert calls == [], "a lost reservation does not start the write-back"
    assert (server_dir / native.WRITE_BACK_OWED_FILE).exists(), "round 4: the marker stays"
    assert native.put_the_folders_back_offered(server_dir) is True
    assert any("Stopped before the app's own files" in line for line in first), first

    second = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert calls == ["compose", "patch"], calls
    assert any(f"Press {REBUILD}" in line for line in second), second


def test_a_press_with_no_folder_off_keeps_a_players_line_in_the_override(tmp_path: Path) -> None:
    """Nothing moved, so the compose files are not rendered again over a player's own line."""
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    override = server_dir / composegen.OVERRIDE_FILE
    override.write_text(
        override.read_text(encoding="utf-8") + "# a line the player wrote\n", encoding="utf-8"
    )
    made = _press_engine(rec)
    calls: list[str] = []
    _spy_halves(made, calls)

    said = list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert "# a line the player wrote" in override.read_text(encoding="utf-8")
    assert calls == ["patch"], calls
    assert any("already on the commit" in line for line in said), said


def test_a_press_with_no_folder_off_rewrites_a_base_file_that_is_not_the_apps_own(
    tmp_path: Path,
) -> None:
    """The compose files are written when one is missing or not this app's own."""
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    made = _press_engine(rec)
    list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))
    (server_dir / composegen.BASE_FILE).write_text(UPSTREAM_COMPOSE, encoding="utf-8")
    assert composegen.BASE_FILE not in native.generated_compose_files(server_dir)
    calls: list[str] = []
    _spy_halves(made, calls)

    list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert composegen.BASE_FILE in native.generated_compose_files(server_dir)
    assert calls == ["compose", "patch"], calls


def test_a_press_with_no_folder_off_leaves_a_missing_override_to_its_own_repair(
    tmp_path: Path,
) -> None:
    """Round 3: with no folder off, only a base file that is not this app's own is rewritten."""
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)
    (server_dir / composegen.OVERRIDE_FILE).unlink()
    assert composegen.BASE_FILE in native.generated_compose_files(server_dir)
    made = _press_engine(rec)
    calls: list[str] = []
    _spy_halves(made, calls)

    list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert calls == ["patch"], calls
    assert not (server_dir / composegen.OVERRIDE_FILE).exists()


def test_the_press_is_offered_while_a_folder_is_off_its_build(tmp_path: Path) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)

    assert native.put_the_folders_back_offered(server_dir) is True

    list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert native.put_the_folders_back_offered(server_dir) is False


def test_the_press_is_not_offered_on_a_server_with_nothing_off(tmp_path: Path) -> None:
    rec = Recorder()
    server_dir = a_finished_install(rec, tmp_path)

    assert native.put_the_folders_back_offered(server_dir) is False


def test_the_press_is_offered_again_after_a_press_lost_its_reservation_mid_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No folder off, but the base compose is upstream's: "press again" must be followable."""
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    stop = threading.Event()
    lost = threading.Event()
    stop.reservation_lost = lost  # type: ignore[attr-defined]
    real_put = native.put_sources_off_back

    def put_then_lose(folder_server: Path, off: Any) -> Any:
        done = real_put(folder_server, off)
        (folder_server / composegen.BASE_FILE).write_text(UPSTREAM_COMPOSE, encoding="utf-8")
        lost.set()
        stop.set()
        return done

    monkeypatch.setattr(native, "put_sources_off_back", put_then_lose)
    list(
        _press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir), cancel=stop)
    )
    assert not native.sources_still_off(server_dir)

    assert native.put_the_folders_back_offered(server_dir) is True


def test_the_press_is_not_offered_without_an_install_record(tmp_path: Path) -> None:
    rec, server_dir, _built, _stray = _off_core(tmp_path)
    (server_dir / native.STATE_FILE).unlink()

    assert native.put_the_folders_back_offered(server_dir) is False


def test_a_crash_after_the_checkout_leaves_the_press_offered_and_rebuild_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 4: the record is gone and the base compose is this app's, yet the write-back is owed.

    A family whose patched source is a sub-checkout under the server folder: its checkout never
    puts upstream's compose file over this app's, so only the marker can say the patch is owed.
    """
    rec, server_dir, built, _stray = _off_core(tmp_path)
    marked = {
        name: (server_dir / name).read_text(encoding="utf-8") for name in composegen.COMPOSE_FILES
    }
    made = _press_engine(rec)
    real_put = native.put_sources_off_back

    def put_then_crash(folder_server: Path, off: Any) -> Any:
        real_put(folder_server, off)
        raise KeyboardInterrupt("the power went")

    monkeypatch.setattr(native, "put_sources_off_back", put_then_crash)
    with pytest.raises(KeyboardInterrupt):
        list(made.put_the_folders_back(InstallOptions(server_dir=server_dir)))
    for name, text in marked.items():
        (server_dir / name).write_text(text, encoding="utf-8")
    assert not native.sources_still_off(server_dir), "the record is forgotten on read"
    assert not native._base_compose_not_ours(server_dir)

    assert native.put_the_folders_back_offered(server_dir) is True
    rec.heads[server_dir] = built
    refused = made.rebuild_refusal_before_asking(server_dir)
    assert refused is not None and PUT_BACK in refused, refused


def test_a_marker_that_cannot_be_written_refuses_the_press_before_any_folder_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, built, stray = _off_core(tmp_path)

    def cannot(server: Path) -> str:
        return "the disk is full"

    monkeypatch.setattr(native, "owe_write_back", cannot)

    with pytest.raises(InstallerError, match="the disk is full"):
        list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert native.read_head_file(server_dir) == stray
    assert {row.built for row in native.sources_still_off(server_dir)} == {built}


def test_a_folder_git_will_not_put_back_keeps_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, built, _stray = _off_core(tmp_path)

    def refuses(folder_server: Path, off: Any) -> Any:
        return tuple(
            native.FolderPutBack(row.repo, row.dest, row.built, row.head, "git said no")
            for row in off
        )

    monkeypatch.setattr(native, "put_sources_off_back", refuses)
    list(_press_engine(rec).put_the_folders_back(InstallOptions(server_dir=server_dir)))

    assert (server_dir / native.WRITE_BACK_OWED_FILE).exists()
