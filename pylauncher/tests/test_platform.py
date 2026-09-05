"""Tests for `yulon.platform` (roadmap Phase 1.2)."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from yulon import platform


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [
        ("win32", "windows"),
        ("cygwin", "windows"),
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),
    ],
)
def test_detect_returns_normalized_platform(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str
) -> None:
    """`detect()` collapses sys.platform to linux/windows/macos."""
    monkeypatch.setattr(platform.sys, "platform", sys_platform)
    assert platform.detect() == expected


def test_detect_falls_back_to_linux_for_unrecognized_platform(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unrecognized sys.platform (e.g. a BSD) defaults to linux, with a warning."""
    monkeypatch.setattr(platform.sys, "platform", "freebsd13")
    with caplog.at_level("WARNING"):
        assert platform.detect() == "linux"
    assert "freebsd13" in caplog.text


def test_config_dir_linux_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux, XDG_DATA_HOME (when set) is honored."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg")
    assert platform.config_dir() == Path("/tmp/xdg/yulon")


def test_config_dir_linux_defaults_to_dot_local_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux without XDG_DATA_HOME, ~/.local/share/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/home/testuser"))
    assert platform.config_dir() == Path("/home/testuser/.local/share/yulon")


def test_config_dir_windows_uses_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, %APPDATA%\\yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    appdata = r"C:\Users\test\AppData\Roaming"
    monkeypatch.setenv("APPDATA", appdata)
    # Path equality is separator-independent; assert component-wise so this test
    # still holds when run on a POSIX host (where Path normalizes to '/').
    assert platform.config_dir() == Path(appdata) / "yulon"


def test_config_dir_windows_falls_back_when_appdata_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows without APPDATA set, ~/AppData/Roaming/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/test"))
    assert platform.config_dir() == Path("/Users/test/AppData/Roaming/yulon")


def test_config_dir_linux_treats_empty_xdg_data_home_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty (but set) XDG_DATA_HOME is treated the same as unset."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/home/testuser"))
    assert platform.config_dir() == Path("/home/testuser/.local/share/yulon")


def test_config_dir_macos_uses_application_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS, ~/Library/Application Support/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/test"))
    assert platform.config_dir() == Path("/Users/test/Library/Application Support/yulon")


# ------------------------------------------- machine facts (roadmap 6.2)
# The facts the native install engine's preflight is built on. Everything here
# goes through the `detect`/`run` seams rather than through `sys.platform`
# where it can: these functions answer questions about a machine none of us is
# sitting at.


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_vm_resources_reads_the_engines_own_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The VM's size, not the host's: a 32 GB Mac can have a 4 GB Docker VM."""
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    seen: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _completed(stdout='{"MemTotal": 17179869184, "NCPU": 8}')

    got = platform.vm_resources(run)
    assert got == platform.VmResources(17179869184, 8)
    assert seen == [["docker", "info", "--format", "{{json .}}"]]


@pytest.mark.parametrize(
    "answer",
    [
        _completed(stdout='{"MemTotal": 0, "NCPU": 0}'),
        _completed(stdout="not json at all"),
        _completed(returncode=1),
    ],
)
def test_vm_resources_reports_nothing_rather_than_a_fabricated_zero(
    monkeypatch: pytest.MonkeyPatch, answer: subprocess.CompletedProcess[str]
) -> None:
    """A stopped Docker Desktop prints well-formed zeroes.

    Believing them refuses a perfectly good machine with "0 GB of RAM", which
    is the fabricated refusal the whole tri-state discipline exists to prevent.
    """
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    assert platform.vm_resources(lambda _argv: answer) is None


def test_vm_resources_bounds_the_other_docker_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight's `docker info` is a probe too, and hangs the same way.

    Same defect as `docker_ready()`'s, one function further down: the default
    runner passed no `timeout`, so a wedged Docker CLI held the preflight that
    is only trying to find out how big the VM is.
    """
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    seen: list[object] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("timeout"))
        return _completed(returncode=124)

    monkeypatch.setattr(platform.runner, "run", fake_run)
    assert platform.vm_resources() is None
    assert seen and all(isinstance(bound, float) for bound in seen), f"unbounded probe: {seen}"


def test_the_macos_data_root_prefers_the_settings_store_and_falls_back_to_docker_raw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Docker Desktop's `diskPath` wins; absent, the documented default `Docker.raw`.

    The fallback path is believed, not measured — it is read only as a target
    for the host free-space probe, and a miss is reported *unchecked*, never as
    a refusal. The settings keys themselves are read defensively, exactly like
    the Windows branch: an unreadable file falls through to the default.
    """
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/deck"))

    store = tmp_path / "settings-store.json"
    store.write_text('{"diskPath": "/Volumes/Big/docker-data"}', encoding="utf-8")
    monkeypatch.setattr(platform, "docker_desktop_settings_file", lambda: store)
    assert platform.docker_desktop_data_root() == Path("/Volumes/Big/docker-data")

    store.write_text('{"virtualDiskPath": "/Volumes/Fast/Docker.raw"}', encoding="utf-8")
    assert platform.docker_desktop_data_root() == Path("/Volumes/Fast/Docker.raw")

    store.write_text("{ not json", encoding="utf-8")
    assert platform.docker_desktop_data_root() == Path(
        "/Users/deck/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw"
    )


def test_docker_desktop_settings_file_on_macos_prefers_settings_store_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    monkeypatch.setattr(platform.Path, "home", lambda: tmp_path)

    group_dir = tmp_path / "Library" / "Group Containers" / "group.com.docker"
    group_dir.mkdir(parents=True)

    # When neither exists, default to settings-store.json
    assert platform.docker_desktop_settings_file() == group_dir / "settings.json"

    # When settings.json exists
    settings_old = group_dir / "settings.json"
    settings_old.write_text('{"diskPath": "/data"}', encoding="utf-8")
    assert platform.docker_desktop_settings_file() == settings_old

    # When settings-store.json exists, it takes precedence
    settings_new = group_dir / "settings-store.json"
    settings_new.write_text('{"diskPath": "/data2"}', encoding="utf-8")
    assert platform.docker_desktop_settings_file() == settings_new


def test_the_docker_data_root_on_linux_is_the_host_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    assert platform.docker_desktop_data_root() == Path("/var/lib/docker")


def test_the_windows_data_root_comes_from_docker_desktops_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read defensively: an unreadable settings file falls through to the default."""
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    store = tmp_path / "settings-store.json"
    store.write_text('{"dataFolder": "D:\\\\docker-data"}', encoding="utf-8")
    monkeypatch.setattr(platform, "docker_desktop_settings_file", lambda: store)
    assert platform.docker_desktop_data_root() == Path("D:\\docker-data")
    store.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert platform.docker_desktop_data_root() == tmp_path / "Docker" / "wsl"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"\\fileserver\share\wow", "network path"),
        ("/Users/pk/OneDrive/wow", "cloud-synced"),
        ("/Users/pk/Library/Mobile Documents/com~apple~CloudDocs/wow", "iCloud Drive"),
        ("/Users/pk/Dropbox/wow", "cloud-synced"),
        ("/Users/pk/games/wow", ""),
        ("/", "root of a filesystem"),
        ("/etc", "system directory"),
        ("/tmp", "system directory"),
        # One "up" click from where the picker opens, and `--reinstall` would
        # have run `sudo rm -rf /home` - every account on the machine.
        ("/home", "system directory"),
        ("/media", "system directory"),
        ("/mnt", "system directory"),
        # A folder UNDER a reserved one is fine - only the tree roots are
        # refused, exactly as the scripts' `case` list does it.
        ("/var/lib/wow", ""),
    ],
)
def test_server_dir_problem_names_the_folders_that_break_a_build(
    monkeypatch: pytest.MonkeyPatch, path: str, expected: str
) -> None:
    """Each of these fails AFTER the two-to-four-hour build, which is why it is a refusal.

    A sync client rewrites files under the compiler and uploads a
    multi-gigabyte checkout; a network path mounts inside Docker as an EMPTY
    directory rather than failing.
    """
    monkeypatch.setattr(platform, "detect", lambda: "macos")
    problem = platform.server_dir_problem(Path(path))
    if not expected:
        assert problem is None
    else:
        assert problem is not None and expected in problem


INHIBIT_ARGV = [
    "systemd-inhibit",
    "--what=idle:sleep",
    "--who=Yu'lon",
    "--why=installing a server",
    "sleep",
    "infinity",
]


def test_keep_awake_on_linux_holds_a_systemd_inhibit_and_lets_it_go() -> None:
    """The `caffeinate` shape on Linux: a detached inhibitor, terminated on the way out."""
    spawned: list[list[str]] = []
    stopped: list[str] = []

    class FakeChild:
        def terminate(self) -> None:
            stopped.append("terminated")

    with platform.keep_awake(
        platform_id=lambda: "linux",
        spawn=lambda argv: (spawned.append(argv), FakeChild())[1],  # type: ignore[arg-type,return-value]
    ):
        assert spawned == [INHIBIT_ARGV]
        assert stopped == []
    assert stopped == ["terminated"]


def test_keep_awake_on_linux_survives_a_missing_systemd_inhibit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No systemd (or no binary) is a warning, and the install still runs."""

    def refuse(_argv: list[str]) -> object:
        raise OSError("no such file")

    with caplog.at_level("WARNING"):
        with platform.keep_awake(platform_id=lambda: "linux", spawn=refuse):  # type: ignore[arg-type]
            pass
    assert "may be interrupted" in caplog.text


def test_keep_awake_on_macos_spawns_a_caffeinate_that_dies_with_us() -> None:
    """`-w <our pid>`: a child bound to this process, so there is no cleanup to forget.

    Unverified on a Mac by this project — `caffeinate` is assumed to ship with
    the OS and `-dims` is assumed to be the right assertion set. What is tested
    here is that we ask for exactly that, and that we let it go afterwards.
    """
    import os

    spawned: list[list[str]] = []
    stopped: list[str] = []

    class FakeChild:
        def terminate(self) -> None:
            stopped.append("terminated")

    with platform.keep_awake(
        platform_id=lambda: "macos",
        spawn=lambda argv: (spawned.append(argv), FakeChild())[1],  # type: ignore[arg-type,return-value]
    ):
        assert spawned == [["caffeinate", "-dims", "-w", str(os.getpid())]]
        assert stopped == []
    assert stopped == ["terminated"]


def test_keep_awake_on_macos_handles_already_terminated_child() -> None:
    class DeadChild:
        def terminate(self) -> None:
            raise ProcessLookupError("process already dead")

    with platform.keep_awake(
        platform_id=lambda: "macos",
        spawn=lambda _argv: DeadChild(),  # type: ignore[arg-type,return-value]
    ):
        pass


def test_keep_awake_on_macos_works_on_a_worker_thread() -> None:
    outcome: list[str] = []

    def work() -> None:
        try:
            with platform.keep_awake(platform_id=lambda: "macos"):
                outcome.append("held")
        except Exception as exc:  # pragma: no cover
            outcome.append(f"failed: {exc}")

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert outcome == ["held"]


def test_keep_awake_on_macos_survives_a_caffeinate_that_will_not_start() -> None:
    """A power helper saying no must not fail an install that would have worked."""

    def refuse(_argv: list[str]) -> object:
        raise OSError("no such file")

    with platform.keep_awake(platform_id=lambda: "macos", spawn=refuse):  # type: ignore[arg-type]
        pass


def test_keep_awake_on_windows_refuses_the_gui_thread() -> None:
    """The assertion is scoped to the thread that sets it, so the GUI thread is a lie.

    `SetThreadExecutionState` holds only while its own thread lives, so taking
    it on the main thread and then running the install on a worker would claim
    a guarantee the install does not have.
    """
    with pytest.raises(RuntimeError, match="worker thread"):
        with platform.keep_awake(platform_id=lambda: "windows"):
            pass


def test_keep_awake_on_windows_is_taken_on_a_worker_thread() -> None:
    """The other half: off the main thread it does not refuse.

    What it actually asserts to Windows cannot be checked here (there is no
    Windows power API in a test), so this is about the thread rule only —
    roadmap 6.3's live gate owns the rest.
    """
    outcome: list[str] = []

    def work() -> None:
        try:
            with platform.keep_awake(platform_id=lambda: "windows"):
                outcome.append("held")
        except RuntimeError as exc:  # pragma: no cover - would be the bug
            outcome.append(f"refused: {exc}")

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert outcome == ["held"]


def test_the_missing_cli_help_names_every_module_that_raises_it() -> None:
    """The docstring enumerates its callers, so the enumeration is asked of the package.

    `DOCKER_CLI_MISSING_HELP` is one sentence with several homes: each module
    that cannot find the Docker CLI raises its own error type carrying this
    text, and the constant's docstring names them so a reader can reach them all
    from one place. That list said "Four modules" for as long as it took
    `maintenance` to start raising it and nobody to notice, and `console.py`
    carried a "the fourth module that has to say this" comment that went wrong
    with it (audit, 2026-08-24).

    SET equality, not "each name appears somewhere". The first version of this
    test asked whether each module's name occurred in the docstring at all, and
    a mutation that deleted `maintenance` from the LIST survived it — the same
    word was still there in the prose sentence explaining the fix. A test that a
    passing sentence can satisfy by accident is not a test. Comparing sets also
    catches the other direction, a module that stops raising it and is left
    named.

    Modules are found by reading source, not by importing: importing them all
    would drag PySide6 in, and the question is about references in the tree.
    """
    package = Path(platform.__file__).parent
    raisers = {
        path.stem
        for path in package.rglob("*.py")
        if path.name != "platform.py"
        and "DOCKER_CLI_MISSING_HELP" in path.read_text(encoding="utf-8")
    }
    assert raisers, "nothing references the constant — this test is measuring the wrong thing"

    # A constant carries no `__doc__`, so the sentence is read from source too.
    source = (package / "platform.py").read_text(encoding="utf-8")
    marker = "Modules that raise it: "
    line = source[source.index(marker) + len(marker) :].splitlines()[0]
    named = set(re.findall(r"`([a-z_]+)`", line))

    assert named == raisers, (
        f"the constant's docstring names {sorted(named)} but {sorted(raisers)} raise it; "
        f"missing {sorted(raisers - named)}, stale {sorted(named - raisers)}"
    )


def test_the_home_directory_itself_is_refused_before_the_installer_sees_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The picker opens on home, so this is the path a click-through actually produces.

    Live gate on clean Fedora 44 (2026-08-25): choosing `/home/pk` passed
    `server_dir_problem()`, reached `install-wow-wotlk-fedora.sh`, and died on
    its `case "$SERVER_DIR" in /|"$HOME"|...` branch - but only AFTER the user
    had typed a sudo password into Yu'lon's own dialog and waited through
    Docker discovery. The refusal has to happen at the picker or it costs the
    user a password and a wait.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    problem = platform.server_dir_problem(tmp_path)
    assert problem is not None
    assert "home folder itself" in problem
    # The message must say what to do instead, not merely refuse - but it must
    # NOT name a specific game's folder: `_reserved_dir_reason` has no entry to
    # read one from, and naming WotLK's for all four catalog entries pointed a
    # TBC install straight at an existing WotLK install (review finding).
    assert "dedicated subfolder" in problem
    assert "wow-server-playerbots" not in problem
    # a dedicated subfolder of the same home is fine
    assert platform.server_dir_problem(tmp_path / "wow-server-playerbots") is None


def test_a_symlink_onto_a_reserved_directory_is_refused_too() -> None:
    """The scripts `realpath -m --` before their `case`; a lexical check cannot.

    On Fedora Atomic `/home` is a symlink to `/var/home`, so a picker returning
    `/home/pk` and a script seeing `/var/home/pk` disagree about whether the
    path is the home folder - and the user pays for that disagreement with a
    sudo password and a wait before the script refuses.
    """
    with tempfile.TemporaryDirectory() as raw:
        link = Path(raw) / "shortcut"
        try:
            link.symlink_to("/tmp", target_is_directory=True)
        except (OSError, NotImplementedError):  # pragma: no cover - needs privilege on Windows
            pytest.skip("cannot create a directory symlink on this machine")
        if link.resolve() == link:  # pragma: no cover - resolution disabled
            pytest.skip("symlinks are not resolved on this filesystem")
        problem = platform.server_dir_problem(link)
        assert problem is not None and "system directory" in problem


# ---------------------------------------------------------------- WSL-resident

# Real bytes, captured from `wsl -l -q` on a Windows 11 box (2026-08-26).
# `wsl.exe` writes UTF-16LE, and decoding it as UTF-8 yields NUL-riddled
# garbage rather than an error - so the wrong reading looks like a distro named
# "d\x00m\x00l\x00...". Two days of this project have already been spent on
# that, which is why the fixture is the real thing rather than a hand-written
# unicode string.
WSL_LIST_UTF16 = (
    b"d\x00m\x00l\x00-\x00a\x00r\x00c\x00h\x00\r\x00\n\x00"
    b"d\x00o\x00c\x00k\x00e\x00r\x00-\x00d\x00e\x00s\x00k\x00t\x00o\x00p\x00\r\x00\n\x00"
)


def test_docker_prefix_is_the_local_cli_when_no_distro_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case is unchanged: whatever `docker_program()` resolved."""
    monkeypatch.setattr(platform, "docker_program", lambda: "docker")
    assert platform.docker_prefix(None) == ("docker",)


def test_docker_prefix_routes_through_wsl_for_a_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server inside a distro is reached by its own docker, not Docker Desktop's.

    Measured against a real WSL-resident server (2026-08-26): `wsl -d dml-arch
    -- docker compose ps` returns rc=0 and the running container, and exit codes
    propagate faithfully - which is what makes this substitution safe for the 21
    lifecycle call sites that read them.
    """
    monkeypatch.setattr(platform, "_which", lambda name, path=None: "C:\\Windows\\wsl.exe")
    assert platform.docker_prefix("dml-arch") == (
        "C:\\Windows\\wsl.exe",
        "-d",
        "dml-arch",
        "--",
        "docker",
    )


def test_docker_prefix_is_none_when_the_host_cannot_reach_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None is a real answer, the same one `docker_program()` gives.

    Callers already turn that into an error carrying DOCKER_CLI_MISSING_HELP, so
    a Windows box with no WSL fails the way a box with no docker already does
    rather than through a new channel.
    """
    monkeypatch.setattr(platform, "docker_program", lambda: None)
    assert platform.docker_prefix(None) is None

    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    assert platform.docker_prefix("dml-arch") is None


def test_docker_prefix_does_not_ask_the_host_about_its_own_docker_for_a_distro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WSL install must not need Docker Desktop installed at all.

    The tester who prompted this has NO Docker Desktop on Windows - the daemon
    lives inside the distro. Resolving the local CLI first would refuse a
    perfectly good server for the absence of something it never uses.
    """
    asked = []
    monkeypatch.setattr(
        platform, "docker_program", lambda: asked.append("local") or None  # type: ignore[func-returns-value]
    )
    monkeypatch.setattr(platform, "_which", lambda name, path=None: "wsl.exe")
    assert platform.docker_prefix("dml-arch") is not None
    assert asked == [], "the local docker CLI was consulted for a WSL install"


def test_wsl_env_names_the_variables_that_must_cross(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without WSLENV a forwarded variable arrives EMPTY, not missing.

    `apply.py` runs `docker exec -e MYSQL_PWD` with no `=value` deliberately, so
    the password never appears in an argv `ps` can read. Measured: without
    WSLENV the inner process sees `[]`, with it `[from-windows]` - so forgetting
    this produces an authentication failure rather than a missing-setting error,
    which is the worst kind of bug to debug.
    """
    monkeypatch.delenv("WSLENV", raising=False)
    env = platform.wsl_env({"MYSQL_PWD": "hunter2"})
    assert env["MYSQL_PWD"] == "hunter2"
    assert "MYSQL_PWD" in env["WSLENV"].split(":")


def test_wsl_env_keeps_an_existing_wslenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone else's WSLENV is not ours to discard."""
    monkeypatch.setenv("WSLENV", "THEIRS/p")
    env = platform.wsl_env({"MYSQL_PWD": "hunter2"})
    assert "THEIRS/p" in env["WSLENV"]
    assert "MYSQL_PWD" in env["WSLENV"]


def test_wsl_distros_decodes_utf16(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wsl.exe` writes UTF-16LE and a UTF-8 read returns garbage, not an error."""
    monkeypatch.setattr(platform, "_wsl_list_bytes", lambda: WSL_LIST_UTF16)
    assert platform.wsl_distros() == ("dml-arch", "docker-desktop")


def test_wsl_distros_is_empty_rather_than_raising_without_wsl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows box with no WSL, and every non-Windows host, has no distros."""
    monkeypatch.setattr(platform, "_wsl_list_bytes", lambda: None)
    assert platform.wsl_distros() == ()


def test_a_utf8_read_of_the_same_bytes_would_have_been_garbage() -> None:
    """The fixture earns its place: prove the wrong decoding is silently wrong.

    Not a test of our code - a test of the trap, so that anyone tempted to
    simplify `wsl_distros()` to `subprocess.run(..., text=True)` sees what that
    produces before they try it.
    """
    naive = [d for d in WSL_LIST_UTF16.decode("utf-8", "ignore").split() if d]
    assert naive != ["dml-arch", "docker-desktop"]
    assert "\x00" in naive[0], "the UTF-8 reading is expected to carry NUL bytes"


def test_wsl_linux_path_converts_a_unc_path_back_to_the_distro_view() -> None:
    """`\\\\wsl.localhost\\dml-arch\\home\\dml\\x` is `/home/dml/x` to the distro.

    A WSL install's `server_dir` is stored in its Windows UNC form, because that
    is what the picker returns and what `compose_file()` can read. Docker inside
    the distro knows nothing of it, so the argv needs the Linux spelling.
    """
    got = platform.wsl_linux_path(Path(r"\\wsl.localhost\dml-arch\home\dml\games\srv"))
    assert got == "/home/dml/games/srv"
    assert platform.wsl_linux_path(Path(r"\\wsl$\Ubuntu\srv\wow")) == "/srv/wow"


def test_wsl_linux_path_is_none_for_a_path_that_is_not_in_wsl() -> None:
    """An ordinary Windows path has no Linux spelling, and guessing one would lie."""
    assert platform.wsl_linux_path(Path(r"C:\Users\pk\srv")) is None
    assert platform.wsl_linux_path(Path(r"\\nas\share\srv")) is None


def test_wsl_linux_path_keeps_the_distro_root_itself() -> None:
    """The share root is the distro's `/`."""
    assert platform.wsl_linux_path(Path(r"\\wsl.localhost\dml-arch")) == "/"


def test_a_wsl_path_is_refused_in_words_that_fit_what_happened() -> None:
    """`\\\\wsl.localhost\\...` is a network path to Windows, but not to the user.

    A tester's server, installed by the DML Launcher, lives inside a WSL distro.
    The generic refusal told them to "pick a folder on this machine's own disk" -
    which it IS. Docker Desktop cannot bind-mount it ("is not a valid Windows
    path", measured on Windows 11), so the refusal is right and only the wording
    was wrong.
    """
    for text in (r"\\wsl.localhost\dml-arch\home\dml\games\srv", r"\\wsl$\Ubuntu\srv"):
        problem = platform.server_dir_problem(Path(text))
        assert problem is not None, f"{text} was accepted"
        assert "WSL" in problem, f"the WSL case is not named in: {problem}"
        assert "own disk" not in problem, "the generic network wording leaked into the WSL case"


def test_an_ordinary_unc_path_keeps_the_network_wording() -> None:
    """A real file share is still a file share."""
    problem = platform.server_dir_problem(Path(r"\\nas\media\srv"))
    assert problem is not None
    assert "network path" in problem
    assert "WSL" not in problem


# ------------------------------------------------------------------- SELinux
# Every probe below has THREE outcomes, and these tests are shaped around
# telling them apart: yes, no, and could-not-ask. The bash lineage of these
# functions shipped a bug for exactly that reason — a helper the test harness
# had not lifted exited 127, the caller read 127 as "this filesystem cannot
# hold labels", and the Fedora install went unlabelled while the test asserted
# an empty list and passed. So a fake `run` that RAISES (the command is not
# there) must not answer the same as one that returns non-zero, and neither may
# answer the same as a clean "no".


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _never(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """A runner that must not be reached; the kernel's own file answered already."""
    raise AssertionError(argv)


def _no_tools(name: str) -> str | None:
    return None


def _has_getenforce(name: str) -> str | None:
    return "/usr/sbin/getenforce" if name == "getenforce" else None


def test_selinux_enforcing_reads_selinuxfs_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`/sys/fs/selinux/enforce` is the kernel's own answer; `getenforce` is the fallback."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    enforce = tmp_path / "enforce"
    enforce.write_text("1", encoding="utf-8")
    assert platform.selinux_enforcing(enforce_path=enforce, run=_never, which=_no_tools) is True
    enforce.write_text("0\n", encoding="utf-8")
    assert platform.selinux_enforcing(enforce_path=enforce, run=_never, which=_no_tools) is False


def test_selinux_enforcing_falls_back_to_getenforce_then_to_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No selinuxfs: ask the tool when it is there, and read its absence as a definite no."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    missing = tmp_path / "no-selinuxfs"
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done("Enforcing\n")

    assert platform.selinux_enforcing(enforce_path=missing, run=run, which=_has_getenforce) is True
    assert calls == [["getenforce"]]

    def permissive(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("Permissive\n")

    def disabled(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("Disabled\n")

    for said in (permissive, disabled):
        got = platform.selinux_enforcing(enforce_path=missing, run=said, which=_has_getenforce)
        assert got is False, said.__name__
    # No selinuxfs and no getenforce: SELinux is not on this box — that is a
    # "no", not a shrug. Ubuntu and Arch must not get an "unchecked" preflight
    # line for a subsystem they do not have.
    assert platform.selinux_enforcing(enforce_path=missing, run=run, which=_no_tools) is False


def test_selinux_enforcing_separates_a_no_from_a_tool_it_could_not_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken, unstartable or babbling `getenforce` is `None`, never `False`.

    `False` means "SELinux is not enforcing here", and the install acts on it:
    no `:z`, no relabel, a green preflight line. A non-zero exit, an `OSError`
    or an answer this code does not recognise says nothing of the sort, and
    letting any of those spell "no" is the exact bug the shell version shipped.
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")
    missing = tmp_path / "no-selinuxfs"

    def broken(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("", 1)

    def not_there(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "getenforce")

    def babbling(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("getenforce: SELinux is disabled on this system\n")

    for run in (broken, not_there, babbling):
        got = platform.selinux_enforcing(enforce_path=missing, run=run, which=_has_getenforce)
        assert got is None, run.__name__


def test_selinux_enforcing_falls_through_an_unreadable_selinuxfs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path that exists but will not read is not an answer either; ask the tool."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    unreadable = tmp_path / "enforce-dir"
    unreadable.mkdir()

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("Enforcing\n")

    got = platform.selinux_enforcing(enforce_path=unreadable, run=run, which=_has_getenforce)
    assert got is True


def test_selinux_enforcing_is_none_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows and macOS have no SELinux to be enforcing or not, and no probe runs."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    assert platform.selinux_enforcing(run=_never, which=_has_getenforce) is None


def test_filesystem_type_asks_stat_for_the_first_existing_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The server dir rarely exists at preflight; its parent's filesystem is the answer."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done("ext2/ext3\n")

    assert platform.filesystem_type(tmp_path / "wow" / "deeper", run=run) == "ext2/ext3"
    assert calls == [["stat", "-f", "-c", "%T", str(tmp_path)]]
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    assert platform.filesystem_type(tmp_path, run=run) is None


def test_filesystem_type_is_none_whenever_stat_did_not_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-zero, unstartable, or nothing on stdout: unknown, and never an empty type.

    `""` would sail through `selinux_labels_supported()` as a filesystem merely
    not on the deny-list — the same "a failure reads as a yes" shape that cost
    the shell relabel six hours of CI.
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")

    def broken(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("", 1)

    def not_there(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "stat")

    def silent(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _done("  \n")

    for run in (broken, not_there, silent):
        assert platform.filesystem_type(tmp_path, run=run) is None, run.__name__


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs GNU coreutils stat")
def test_filesystem_type_reads_the_real_stat_on_linux(tmp_path: Path) -> None:
    """The argv is only right if a real `stat` accepts it, and no fake can prove that."""
    got = platform.filesystem_type(tmp_path / "not-created-yet")
    assert got is not None
    assert got == got.strip().lower()
    assert " " not in got


@pytest.mark.parametrize(
    ("fs_type", "supported"),
    [
        (None, True),
        ("ext2/ext3", True),
        ("btrfs", True),
        ("xfs", True),
        ("ntfs", False),
        ("NTFS3", False),
        ("fuseblk", False),
        ("exfat", False),
        ("nfs4", False),
        ("9p", False),
    ],
)
def test_selinux_labels_supported_is_a_deny_list(fs_type: str | None, supported: bool) -> None:
    """Unknown means labels work (the common case); only the listed ones cannot carry them."""
    assert platform.selinux_labels_supported(fs_type) is supported


def test_the_deny_list_is_the_one_the_fedora_gate_passed_with() -> None:
    """The shipped installers' `case` arm, verbatim; a name dropped here silently arms `:z`."""
    assert platform.SELINUX_NOLABEL_FS == frozenset(
        {"exfat", "ntfs", "ntfs3", "fuseblk", "msdos", "vfat", "cifs", "smb2", "nfs", "nfs4", "9p"}
    )


def test_bind_label_is_z_only_when_enforcing_on_a_labelable_filesystem() -> None:
    assert platform.bind_label(enforcing=True, fs_type="ext2/ext3") == ":z"
    assert platform.bind_label(enforcing=True, fs_type=None) == ":z"
    assert platform.bind_label(enforcing=True, fs_type="ntfs") == ""
    assert platform.bind_label(enforcing=False, fs_type="ext2/ext3") == ""
    assert platform.bind_label(enforcing=None, fs_type="ext2/ext3") == ""


def test_bind_label_answers_only_the_two_strings_composegen_will_splice() -> None:
    """`render()` refuses anything else, and that refusal lands mid-install, not here.

    Nothing wires the two together until A.5, so this and its twin in
    `test_composegen.py` are the whole of the agreement between them.
    """
    fs_types: list[str | None] = [None, "", "ext2/ext3", "xfs", "9p", "NTFS3", "tmpfs"]
    answers = {
        platform.bind_label(enforcing=enforcing, fs_type=fs_type)
        for enforcing in (True, False, None)
        for fs_type in fs_types
    }
    assert answers <= {"", ":z"}, answers


# ------------------------------------------------- relabelling and --user args
# `relabel_for_containers()` reports what it DID, not what it learned, so its
# single `False` is not the collapsed "could not ask" the probes above refuse:
# a missing `chcon`, a `chcon` that would not start and a `chcon` that exited
# non-zero all leave the same fact behind — the files are unlabelled — and each
# one says so in the log. `:z` on the bind lines is what carries the install.


def test_relabel_for_containers_runs_chcon_without_sudo(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The Fedora script's `selinux_label_for_containers`, ported.

    Recursive, our own files, no sudo.
    """
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done()

    def which(name: str) -> str | None:
        return "/usr/bin/chcon" if name == "chcon" else None

    assert platform.relabel_for_containers(tmp_path, run=run, which=which) is True
    assert calls == [["chcon", "-Rt", "container_file_t", str(tmp_path)]]
    with caplog.at_level("WARNING"):
        assert (
            platform.relabel_for_containers(tmp_path, run=lambda a: _done("", 1), which=which)
            is False
        )
        assert platform.relabel_for_containers(tmp_path, run=run, which=_no_tools) is False

        def refuse(argv: list[str]) -> subprocess.CompletedProcess[str]:
            raise OSError("no such file")

        assert platform.relabel_for_containers(tmp_path, run=refuse, which=which) is False
    assert caplog.text.count("could not relabel") == 3
    # The three failures are three different sentences, because "chcon is not
    # installed" and "chcon exit 1" send the reader to different places.
    assert len(set(caplog.text.splitlines())) == 3


def test_relabel_for_containers_asks_for_no_privilege_at_all(tmp_path: Path) -> None:
    """Privilege transparency is asserted on the ARGV, not on the sentence describing it.

    The server directory is the user's own, so labelling it needs nothing but
    the user. A `sudo`, a `pkexec` or a `chown` sneaking in here would be a
    silent privilege ask in the middle of an install — the one thing this
    project never does without the explicit consent path.
    """
    calls: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _done()

    platform.relabel_for_containers(tmp_path, run=run, which=lambda name: f"/usr/bin/{name}")
    assert calls, "nothing was run at all, so this proves nothing"
    forbidden = {"sudo", "pkexec", "doas", "su", "chown", "chmod", "setfacl", "usermod"}
    for argv in calls:
        assert not forbidden & set(argv), argv


def test_container_user_args_is_uid_gid_on_linux_and_nothing_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One home for the `docker run` uid:gid policy.

    Linux only; Docker Desktop runs as the image's user.
    """
    monkeypatch.setattr(platform.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(platform.os, "getgid", lambda: 1000, raising=False)
    assert platform.container_user_args(platform_id=lambda: "linux") == ["--user", "1000:1000"]
    assert platform.container_user_args(platform_id=lambda: "macos") == []
    assert platform.container_user_args(platform_id=lambda: "windows") == []


def test_container_user_args_leaves_evidence_when_it_cannot_ask_for_a_uid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Linux with no `os.getuid` is a contradiction, and silence would hide it.

    The empty list is the only safe answer — a `--user` cannot be spelled
    without the numbers — but it is the same empty list Docker Desktop gets for
    a completely different reason, so the log is what tells the two apart.
    """
    monkeypatch.delattr(platform.os, "getuid", raising=False)
    monkeypatch.delattr(platform.os, "getgid", raising=False)
    with caplog.at_level("WARNING"):
        assert platform.container_user_args(platform_id=lambda: "linux") == []
    assert "getuid" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert platform.container_user_args(platform_id=lambda: "windows") == []
    assert caplog.text == "", "Docker Desktop having no getuid is normal, not a warning"
