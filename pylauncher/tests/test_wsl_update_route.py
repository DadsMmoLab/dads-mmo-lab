"""T125: Rebuild and "Update the server to latest…" for a server that lives inside a WSL distro.

The server the Windows app adopted with "Find in WSL…" was built by Yu'lon on
Linux INSIDE the distro, on that distro's own Docker. Every command these two
presses run has to reach that Docker and that checkout, never Windows' own:
`pyplan/wsl-resident-servers.md` §1 (the prefix only), §2 (a stopped distro is
never booted by a reading), §4a (accepting a distro is not passing one).

The measured facts behind the fixtures, from yulon-win11 (2026-09-25):

* the Vanilla fixture at `/home/pk/wow-vanilla` recorded install id `2f1c23d4`,
  which is sha256 of that Linux spelling; the Windows spelling the app holds,
  hashed the Windows way, is `27a96c15` -- a different image tag;
* Docker Desktop refuses a `\\\\wsl.localhost\\...` bind mount outright.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import pytest

from tests.support_native import ENTRY, Recorder, install
from yulon import docker, git, install_wiring, platform, runner, wsl
from yulon.catalog import composegen, native, upstream
from yulon.catalog.installer import InstallerError

DISTRO = "Ubuntu"
OLD = "a" * 40
NEW = "b" * 40

# -- identity ------------------------------------------------------------------------
#
# Two ids for one folder, and both are right for what they key (T125 rework):
# the WINDOWS-side id -- the hash of `\\wsl.localhost\...`, lowercased -- keys
# the command-channel credential, its GM account name, dbsecret, altbot memory
# and the run records, and must not move; the distro's images and compose
# project are named after the id Linux Yu'lon RECORDED in `.yulon-install.json`.

UNC_FIXTURE = Path("\\\\wsl.localhost\\Ubuntu\\home\\pk\\wow-vanilla")


@pytest.fixture
def _as_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """`os.path.abspath` as Windows computes it, so the UNC fixture hashes as it does there."""
    import ntpath

    monkeypatch.setattr(composegen.os.path, "abspath", ntpath.abspath)


def test_the_windows_side_id_of_a_wsl_folder_is_the_one_it_always_was(_as_windows: None) -> None:
    """Pinned to the value measured on yulon-win11 before T125: every WSL install
    adopted earlier keys its channel credential, GM account and history by it."""
    assert composegen.install_id(UNC_FIXTURE, platform_id=lambda: "windows") == "27a96c15"


def test_a_credential_keyed_by_the_old_id_is_still_found(_as_windows: None, tmp_path: Path) -> None:
    from yulon import channel_setup

    before = tmp_path / "credentials" / "wow-vanilla-27a96c15.json"
    here = channel_setup.credential_path(
        "wow-vanilla",
        composegen.install_id(UNC_FIXTURE, platform_id=lambda: "windows"),
        config_dir=tmp_path,
    )
    assert here == before


def _recorded(tmp_path: Path, ident: str) -> Path:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": ENTRY.id,
                "family": ENTRY.install.native.family,  # type: ignore[union-attr]
                "install_id": ident,
                "completed": [],
                "last_error": "",
                "updated_unix": 0,
            }
        ),
        encoding="utf-8",
    )
    return server_dir


def test_the_distros_images_are_named_after_the_recorded_id(tmp_path: Path) -> None:
    server_dir = _recorded(tmp_path, "2f1c23d4")
    engine = install_wiring.installer_for_app(ENTRY, wsl_distro=DISTRO)
    assert engine._install_id(server_dir) == "2f1c23d4"  # type: ignore[attr-defined]
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.read_state(server_dir, valid=()),  # type: ignore[arg-type]
        cancel=None,
        secrets=native.Secrets("unused"),
    )
    refs = engine.built_image_refs(ctx)  # type: ignore[attr-defined]
    assert refs and all(ref.endswith(":native-2f1c23d4") for ref in refs), refs
    local = install_wiring.installer_for_app(ENTRY)
    assert local._install_id(server_dir) != "2f1c23d4"  # type: ignore[attr-defined]


@pytest.mark.parametrize("ident", ["", "27A96C15", "2f1c23d4; rm", "abc"])
def test_a_record_with_no_usable_id_is_refused(tmp_path: Path, ident: str) -> None:
    server_dir = _recorded(tmp_path, ident)
    with pytest.raises(InstallerError, match="install id"):
        native.recorded_install_id(server_dir)


def test_no_record_at_all_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InstallerError, match="install id"):
        native.recorded_install_id(tmp_path)


# -- the distro's own docker -----------------------------------------------------------


def _wsl_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        platform, "_which", lambda name, path=None: "wsl" if name == "wsl" else None
    )


def test_daemon_ready_asks_the_distros_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    _wsl_on_path(monkeypatch)
    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "29.1.3\n", "")

    monkeypatch.setattr(runner, "run", run)
    assert docker.daemon_ready(wsl_distro=DISTRO) is True
    [argv] = seen
    assert argv[:4] == ["wsl", "-d", DISTRO, "--"] and argv[4:6] == ["docker", "info"]


# -- containerised git inside the distro ----------------------------------------------


UNC_SERVER = "\\\\wsl.localhost\\Ubuntu\\home\\pk\\wow-vanilla"


def _git_in(monkeypatch: pytest.MonkeyPatch) -> tuple[git.ContainerGit, list[list[str]]]:
    _wsl_on_path(monkeypatch)
    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, f"{OLD}\n", "")

    monkeypatch.setattr(runner, "run", run)
    made = git.ContainerGit(wsl_distro=DISTRO, owner=lambda distro, path: "1000:1000")
    return made, seen


def test_git_in_a_distro_runs_on_the_distros_docker_with_the_linux_path_and_the_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    made, seen = _git_in(monkeypatch)
    dest = Path(UNC_SERVER + "\\src\\mangos-classic")
    argv = made._argv(made._launcher(), dest, ["rev-parse", "HEAD"], writes=False)
    assert argv[:5] == ["wsl", "-d", DISTRO, "--", "docker"]
    assert "-v" in argv
    assert argv[argv.index("-v") + 1] == "/home/pk/wow-vanilla/src/mangos-classic:/git:ro"
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert not any("wsl.localhost" in part for part in argv), argv
    written = made._argv(made._launcher(), dest, ["fetch"], writes=True)
    assert written[written.index("-v") + 1] == "/home/pk/wow-vanilla/src/mangos-classic:/git"


def test_git_in_a_distro_refuses_a_folder_the_distro_cannot_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made, _ = _git_in(monkeypatch)
    with pytest.raises(git.GitError, match="not inside"):
        made._argv(made._launcher(), Path("C:\\Games\\wow"), ["status"], writes=False)


def test_git_in_a_distro_refuses_rather_than_run_as_root_when_the_owner_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wsl_on_path(monkeypatch)
    made = git.ContainerGit(wsl_distro=DISTRO, owner=lambda distro, path: None)
    with pytest.raises(git.GitError, match="owns"):
        made._argv(made._launcher(), Path(UNC_SERVER), ["status"], writes=False)


def test_the_owner_is_asked_of_the_distro_by_stat(monkeypatch: pytest.MonkeyPatch) -> None:
    _wsl_on_path(monkeypatch)
    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "1000:1000\n", "")

    monkeypatch.setattr(runner, "run", run)
    assert git.distro_owner(DISTRO, "/home/pk/wow-vanilla") == "1000:1000"
    assert seen == [["wsl", "-d", DISTRO, "--exec", "stat", "-c", "%u:%g", "/home/pk/wow-vanilla"]]


def test_the_owner_answer_must_look_like_uid_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    _wsl_on_path(monkeypatch)
    monkeypatch.setattr(
        runner, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "root; rm\n", "")
    )
    assert git.distro_owner(DISTRO, "/x") is None


def test_the_wsl_route_keeps_the_pin_logic_only_the_transport_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`CloneSpec.rev` (T126's releases, the way back to a pin) reaches git unchanged.

    The same update-in-place clone with a `rev`, run locally and in a distro:
    the git subcommands after the image must be identical, argv for argv.
    """
    dest = tmp_path / "checkout"
    (dest / ".git").mkdir(parents=True)
    spec = git.CloneSpec(url="https://github.com/x/y.git", dest=dest, branch="main", rev=NEW)
    monkeypatch.setattr(platform, "wsl_location", lambda path: (DISTRO, str(path)))

    def git_tails(made: git.ContainerGit) -> list[list[str]]:
        seen: list[list[str]] = []
        monkeypatch.setattr(
            runner,
            "run",
            lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        made.clone(spec)
        return [cmd[cmd.index(made.image) + 1 :] for cmd in seen if made.image in cmd]

    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    local = git_tails(git.ContainerGit())
    _wsl_on_path(monkeypatch)
    inside = git_tails(git.ContainerGit(wsl_distro=DISTRO, owner=lambda d, p: "1000:1000"))
    assert local and inside == local
    assert any(NEW in part for tail in inside for part in tail), "the pin never reached git"


# -- the WSL engine's seams (§4a, first half) ------------------------------------------

_GIT_FIELDS = {
    "clone",
    "remote_url",
    "file_unmodified",
    "local_edits",
    "no_local_commits",
    "head_sha",
    "head_version",
    "commits_since",
    "restore_rev",
}
"""The seams that are git questions: bound to a `ContainerGit` that names the distro."""

_NOT_ADDRESSED_TO_THE_DISTRO = {
    "platform_id": "answered 'linux': the install inside the distro was made by Linux Yu'lon",
    "docker_ready": "a `daemon_ready` partial, checked below",
    "ensure_docker": "install-only: provisioning Docker is not something an existing server asks",
    "dir_problem": "install-only preflight",
    "gather": "install-only preflight",
    "upstream_get": "plain HTTPS from the app to GitHub; no daemon is involved",
    "monotonic": "a clock",
    "sleep": "a clock",
    "keep_awake": "it is Windows that must not sleep while the distro compiles",
    "lan_ip": "install-only: the realm row an install ends by setting",
    "relabel": "SELinux: the WSL kernel has none, and the label is '' (never called with '')",
    "selinux_enforcing": "answered False: the WSL kernel runs no SELinux",
    "fs_type": "answered None: no SELinux, so nothing to ask (never the host's `stat -f`)",
    "run_container": "install-only (extraction); takes no distro, so it is refused instead",
    "copy_from_image": "install-only (conf templates); takes no distro, so it is refused instead",
    "verify_import": "install-only (the import stage); takes no distro",
    "install_id": "the id the install RECORDED (`recorded_install_id`), tested on its own",
}


def _default_of(field: dataclasses.Field[object]) -> object:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return None


def _takes_a_distro(value: object) -> bool:
    try:
        return callable(value) and "wsl_distro" in inspect.signature(value).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def test_every_seam_that_can_name_a_daemon_names_the_distro() -> None:
    """Derived from the signatures, not a hand list: a new docker seam fails here unbound."""
    seams = native.Seams.in_wsl(DISTRO)
    late = {"world_running": docker.world_running, "db_running": docker.world_running}
    unbound: list[str] = []
    for field in dataclasses.fields(native.Seams):
        default = late.get(field.name, _default_of(field))
        value = getattr(seams, field.name)
        if field.name in _GIT_FIELDS:
            owner = getattr(value, "__self__", None)
            if not isinstance(owner, git.ContainerGit) or owner.wsl_distro != DISTRO:
                unbound.append(field.name)
        elif _takes_a_distro(default):
            if not (
                isinstance(value, functools.partial)
                and value.func is default
                and value.keywords.get("wsl_distro") == DISTRO
            ):
                unbound.append(field.name)
        else:
            assert field.name in _NOT_ADDRESSED_TO_THE_DISTRO, (
                f"Seams.{field.name} is new and neither names the distro nor says why it "
                "need not; bind it in Seams.in_wsl or add it to _NOT_ADDRESSED_TO_THE_DISTRO"
            )
    assert unbound == [], f"these seams would ask Windows' own Docker: {unbound}"


def test_the_wsl_seams_answer_linux_and_ask_the_distros_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[object] = []
    monkeypatch.setattr(docker, "daemon_ready", lambda **kw: asked.append(kw) or True)
    seams = native.Seams.in_wsl(DISTRO)
    assert seams.install_id is native.recorded_install_id
    assert seams.platform_id() == "linux"
    assert seams.ask_selinux() is False
    assert seams.docker_ready() is True
    assert asked and asked[0]["wsl_distro"] == DISTRO  # type: ignore[index]


def test_the_install_only_seams_refuse_rather_than_reach_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seams = native.Seams.in_wsl(DISTRO)
    for name in ("run_container", "copy_from_image", "verify_import", "ensure_docker"):
        with pytest.raises(InstallerError, match="inside"):
            getattr(seams, name)()


# -- the wiring --------------------------------------------------------------------------


def test_a_wsl_server_is_offered_both_presses(tmp_path: Path) -> None:
    assert install_wiring.update_to_latest_for_app(ENTRY, tmp_path, wsl_distro=DISTRO) is not None
    assert install_wiring.rebuild_for_app(ENTRY, tmp_path, wsl_distro=DISTRO) is not None


def test_the_wsl_press_builds_its_engine_on_the_distros_seams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built: list[object] = []
    real = install_wiring.installer_for

    def spy(entry: object, **kw: object) -> object:
        built.append(kw.get("seams"))
        return real(entry, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(install_wiring, "installer_for", spy)
    engine = install_wiring.installer_for_app(ENTRY, wsl_distro=DISTRO)
    assert isinstance(built[-1], native.Seams)
    assert engine._seams.platform_id() == "linux"  # type: ignore[attr-defined]
    install_wiring.installer_for_app(ENTRY)
    assert built[-1] is None, "a local engine must keep the default seams"


# -- §2: a stopped distro is never booted by a reading ------------------------------------


def test_a_stopped_distro_is_not_read_for_the_version_line_or_the_news(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_wiring.wsl, "is_running", lambda distro: False)
    # Built first: the route's construction reads the catalog, never the folder.
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path, wsl_distro=DISTRO)
    assert route is not None and route.upstream_news is not None

    def boom(*a: object, **kw: object) -> object:
        raise AssertionError("a stopped distro was read, which would start it")

    monkeypatch.setattr(install_wiring, "read_state", boom)
    monkeypatch.setattr(install_wiring, "installer_for", boom)
    monkeypatch.setattr(install_wiring, "installer_for_app", boom)
    monkeypatch.setattr(upstream, "read_cached", boom)
    monkeypatch.setattr(runner, "run", boom)
    said = route.source_version()
    assert said.line == "" and said.past_the_pin is False
    news = route.upstream_news()
    assert news.sources == () and not news.answered()


def test_a_running_distro_is_read_as_usual(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(install_wiring.wsl, "is_running", lambda distro: True)
    monkeypatch.setattr(platform, "wsl_location", lambda path: (DISTRO, "/home/pk/x"))
    read: list[Path] = []
    monkeypatch.setattr(
        install_wiring, "read_state", lambda path, valid=(): read.append(path) or None
    )
    route = install_wiring.update_to_latest_for_app(ENTRY, tmp_path, wsl_distro=DISTRO)
    assert route is not None
    route.source_version()
    assert read == [tmp_path]


# -- identity guard -------------------------------------------------------------------------


def _nothing_may_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **kw: object) -> object:
        raise AssertionError(f"something was read or run: {a!r}")

    for name in ("run", "stream", "stream_progress"):
        monkeypatch.setattr(runner, name, boom)
    monkeypatch.setattr(install_wiring, "read_state", boom)
    monkeypatch.setattr(native, "read_state", boom)
    monkeypatch.setattr(native, "recorded_install_id", boom)


def test_a_folder_in_another_distro_is_refused_before_anything_is_read_or_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex, high: the UNC path names Ubuntu, the install is remembered in Debian.

    `wsl_linux_path()` dropped the distro, so the same Linux path would have been
    rebuilt in the wrong distro. Refused on the comparison alone.
    """
    unc = Path(UNC_SERVER)
    _nothing_may_run(monkeypatch)
    with pytest.raises(InstallerError, match="Debian"):
        list(install_wiring.rebuild_for_app(ENTRY, unc, wsl_distro="Debian")(None))
    route = install_wiring.update_to_latest_for_app(ENTRY, unc, wsl_distro="Debian")
    assert route is not None
    with pytest.raises(InstallerError, match="Debian"):
        list(route.press(None))
    with pytest.raises(InstallerError, match="Debian"):
        list(route.to_pin(None))
    assert route.source_version().line == ""
    assert route.upstream_news().sources == ()


def test_the_distro_is_compared_without_regard_to_case(monkeypatch: pytest.MonkeyPatch) -> None:
    assert platform.wsl_linux_path_in(Path(UNC_SERVER), "ubuntu") == "/home/pk/wow-vanilla"
    assert platform.wsl_location(Path(UNC_SERVER)) == ("Ubuntu", "/home/pk/wow-vanilla")
    assert platform.wsl_linux_path_in(Path("C:\\Games\\wow"), "Ubuntu") is None


def test_docker_refuses_a_folder_in_another_distro_without_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wsl_on_path(monkeypatch)
    _nothing_may_run(monkeypatch)
    with pytest.raises(docker.DockerCommandError, match="Debian"):
        docker._run(["compose", "ps"], cwd=Path(UNC_SERVER), wsl_distro="Debian")
    run = docker.run_attached(["compose", "build"], cwd=Path(UNC_SERVER), wsl_distro="Debian")
    assert run.returncode != 0 and "Debian" in " ".join(run.tail)


def test_git_refuses_a_folder_in_another_distro_without_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wsl_on_path(monkeypatch)
    _nothing_may_run(monkeypatch)
    made = git.ContainerGit(wsl_distro="Debian", owner=lambda d, p: "1000:1000")
    with pytest.raises(git.GitError, match="Debian"):
        made._argv(made._launcher(), Path(UNC_SERVER), ["status"], writes=False)


# -- §4a second half: the presses end to end, every argv through the distro -----------------


class _Distro:
    """The distro's docker and git as argv sees them: recorded, answered, never real."""

    def __init__(self, server_dir: Path) -> None:
        self.server_dir = server_dir
        self.calls: list[list[str]] = []
        self.heads: dict[str, str] = {}

    def _source_url(self, mount: str) -> str:
        where = mount.split(":/git")[0]
        for source in ENTRY.emulator.sources:
            if where == str(PurePosixPath(str(self.server_dir)) / source.dest).rstrip("/.") or (
                source.dest == "." and where == str(self.server_dir)
            ):
                return source.url
        return ENTRY.emulator.sources[0].url

    def answer(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        inner = cmd[cmd.index("--") + 1 :] if "--" in cmd else cmd
        if cmd[3:5] == ["--exec", "stat"]:
            return subprocess.CompletedProcess(cmd, 0, "1000:1000\n", "")
        text = " ".join(inner)
        mount = inner[inner.index("-v") + 1] if "-v" in inner else ""
        if "remote get-url origin" in text:
            return subprocess.CompletedProcess(cmd, 0, self._source_url(mount) + "\n", "")
        if "rev-parse HEAD" in text:
            return subprocess.CompletedProcess(cmd, 0, self.heads.get(mount, OLD) + "\n", "")
        if inner[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(cmd, 0, "29.1.3\n", "")
        if "image inspect" in text:
            return subprocess.CompletedProcess(cmd, 0, "sha256:0123\n", "")
        if " up " in f" {text} " and "compose" in text:
            return subprocess.CompletedProcess(cmd, 1, "", "the test stops the press here")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def stream(self, cmd: list[str], *a: object, **kw: object) -> Iterator[str]:
        self.answer(cmd)
        text = " ".join(cmd)
        if "reset" in text and "-v" in cmd:
            self.heads[cmd[cmd.index("-v") + 1].replace(":/git", ":/git:ro")] = NEW
        yield from ()


def _through_the_distro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[_Distro, Path]:
    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    # The Linux spelling of the folder IS its path here, so the id the install
    # recorded is the one the distro's engine recomputes (see the identity tests).
    monkeypatch.setattr(
        platform, "wsl_location", lambda path: (DISTRO, str(path).replace("\\", "/"))
    )
    _wsl_on_path(monkeypatch)
    distro = _Distro(server_dir)
    monkeypatch.setattr(runner, "run", lambda cmd, *a, **kw: distro.answer(cmd))
    monkeypatch.setattr(runner, "stream", distro.stream)
    monkeypatch.setattr(runner, "stream_progress", distro.stream)
    monkeypatch.setattr(wsl, "is_running", lambda name: True)
    monkeypatch.setattr(
        platform, "docker_program", lambda: pytest.fail("the local docker was asked")
    )
    return distro, server_dir


def _only_the_distro(distro: _Distro) -> None:
    assert distro.calls, "nothing was run at all"
    strays = [cmd for cmd in distro.calls if cmd[:3] != ["wsl", "-d", DISTRO]]
    assert strays == [], f"these reached something other than the distro: {strays}"


def test_a_rebuild_runs_every_command_in_the_distro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    distro, server_dir = _through_the_distro(monkeypatch, tmp_path)
    with pytest.raises(InstallerError):
        list(install_wiring.rebuild_for_app(ENTRY, server_dir, wsl_distro=DISTRO)(None))
    _only_the_distro(distro)
    said = [" ".join(cmd) for cmd in distro.calls]
    assert any(
        "docker image tag" in line for line in said
    ), "the rollback was not kept in the distro"
    assert any("compose" in line and " up " in f" {line} " for line in said), "\n".join(said)


def test_an_update_fetches_and_builds_in_the_distro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    distro, server_dir = _through_the_distro(monkeypatch, tmp_path)
    route = install_wiring.update_to_latest_for_app(ENTRY, server_dir, wsl_distro=DISTRO)
    assert route is not None
    with pytest.raises(InstallerError):
        list(route.press(None))
    _only_the_distro(distro)
    said = [" ".join(cmd) for cmd in distro.calls]
    assert any(" fetch " in f" {line} " for line in said), "nothing was fetched"
    assert any("--user 1000:1000" in line for line in said), "git ran without the owner's uid"
    assert any("compose" in line and " up " in f" {line} " for line in said)


def test_the_tortoise_adoption_reads_the_module_checkout_through_the_distro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T123's wrap keeps `upstream_news` and asks the distro's git for the module's head."""
    from yulon.controller_wow_tortoise import botpool

    asked: list[object] = []
    monkeypatch.setattr(
        git.ContainerGit,
        "head_sha",
        lambda self, dest: asked.append(self.wsl_distro) or OLD,
    )
    assert botpool.head_sha(tmp_path, wsl_distro=DISTRO) == OLD
    assert asked == [DISTRO]
    route = native.LatestRoute(
        confirmation=lambda: "",
        press=lambda cancel: iter(()),
        pin_confirmation=lambda: "",
        to_pin=lambda cancel: iter(()),
        source_version=lambda: native.SourceVersion(line="", past_the_pin=False),
        upstream_news=lambda: upstream.UpstreamNews(checked_unix=0, sources=()),
    )
    from yulon.catalog.catalog import load_catalog

    tortoise = load_catalog().get("wow-tortoise")
    wrapped = botpool.wrap_route(
        route, tortoise, tmp_path, channels=lambda: [], restart=lambda: None, wsl_distro=DISTRO
    )
    assert wrapped is not None and wrapped.upstream_news is route.upstream_news
