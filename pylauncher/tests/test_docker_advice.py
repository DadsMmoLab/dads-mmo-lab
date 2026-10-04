"""What the Server tab's Docker banner says, per machine and per failure (T194 C7, T185)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.support_player_text import text_faults
from yulon import docker, docker_advice, wsl
from yulon import platform as yulon_platform

HOSTS = ("windows", "macos", "linux", "deck")
PROBLEMS = ("missing", "not-running", "permission", "removed", "wsl", "unknown")

ACTIONS = {
    ("windows", "missing"): "open-desktop",
    ("windows", "not-running"): "open-desktop",
    ("windows", "permission"): None,
    ("windows", "removed"): "open-desktop",
    ("windows", "wsl"): None,
    ("windows", "unknown"): None,
    ("macos", "missing"): "open-desktop",
    ("macos", "not-running"): "open-desktop",
    ("macos", "permission"): None,
    ("macos", "removed"): "open-desktop",
    ("macos", "wsl"): None,
    ("macos", "unknown"): None,
    ("linux", "missing"): None,
    ("linux", "not-running"): None,
    ("linux", "permission"): None,
    ("linux", "removed"): None,
    ("linux", "wsl"): None,
    ("linux", "unknown"): None,
    ("deck", "missing"): "reinstall-deck",
    ("deck", "not-running"): None,
    ("deck", "permission"): None,
    ("deck", "removed"): "reinstall-deck",
    ("deck", "wsl"): None,
    ("deck", "unknown"): None,
}
"""The press each machine gets for each failure: written out, never derived."""

NPIPE = (
    'docker ps --format {{.Names}} exited 1: error during connect: Get "http://%2F%2F.%2Fpipe'
    '%2FdockerDesktopLinuxEngine/v1.47/containers/json": open //./pipe/dockerDesktopLinuxEngine: '
    "The system cannot find the file specified."
)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("problem", PROBLEMS)
def test_every_machine_and_failure_gets_the_one_title_a_body_and_its_press(
    host: docker_advice.Host, problem: docker_advice.Problem
) -> None:
    advice = docker_advice.advise(problem, host, distro="Ubuntu")

    assert advice.title == "Yu'lon can't ask Docker about this server right now"
    assert advice.action == ACTIONS[(host, problem)]
    assert advice.body.strip(), "a banner with a title and nothing to do"
    assert text_faults(advice.body) == [], advice.body
    for raw in ("pipe", "docker ps", "exited", "Error"):
        assert raw not in advice.body, advice.body
    if host in ("linux", "deck") or problem == "wsl":
        assert "Docker Desktop" not in advice.body, advice.body


def test_docker_desktop_down_says_open_it_and_wait_for_engine_running() -> None:
    advice = docker_advice.advise("not-running", "windows")

    assert advice.body == (
        "Docker Desktop isn't running. Open it and wait until it says Engine running; "
        "Yu'lon checks again every few seconds."
    )
    assert docker_advice.advise("not-running", "macos").body == advice.body


def test_a_deck_or_linux_daemon_that_is_down_is_told_how_to_start_it() -> None:
    linux = docker_advice.advise("not-running", "linux").body
    deck = docker_advice.advise("not-running", "deck").body

    assert linux.startswith("Docker is installed but not running. Restart the computer"), linux
    assert deck.startswith("Docker is installed but not running. Restart the Deck"), deck
    assert "Deck" not in linux, "a plain Linux machine was told about a Deck"
    assert "computer" not in deck
    for body in (linux, deck):
        assert "sudo systemctl start docker" in body


def test_a_deck_whose_docker_an_update_removed_is_told_the_reinstall() -> None:
    assert docker_advice.advise("removed", "deck").body == yulon_platform.STEAMOS_DOCKER_GONE_HELP
    assert docker_advice.advise("missing", "deck").body == yulon_platform.STEAMOS_DOCKER_GONE_HELP


def test_a_missing_cli_is_told_only_its_own_platforms_half() -> None:
    linux = docker_advice.advise("missing", "linux").body
    windows = docker_advice.advise("missing", "windows").body

    assert "Docker Engine" in linux and "Docker Desktop" not in linux
    assert "Docker Desktop" in windows and "Docker Engine" not in windows


def test_the_two_halves_are_the_missing_cli_sentences_own_words() -> None:
    """One home for the words: the halves are platform's, and the whole is built from them."""
    assert docker_advice.advise("missing", "linux").body == yulon_platform.DOCKER_MISSING_ON_LINUX
    assert docker_advice.advise("missing", "windows").body == (
        yulon_platform.DOCKER_MISSING_ON_DESKTOP
    )
    assert docker_advice.advise("missing", "macos").body == (
        yulon_platform.DOCKER_MISSING_ON_DESKTOP
    )
    whole = yulon_platform.DOCKER_CLI_MISSING_HELP
    for half in (yulon_platform.DOCKER_MISSING_ON_LINUX, yulon_platform.DOCKER_MISSING_ON_DESKTOP):
        advice = half.removeprefix("Docker could not be found on this machine. ")
        assert advice != half and advice in whole, (advice, whole)


def test_linux_permission_says_log_out_and_back_in() -> None:
    body = docker_advice.advise("permission", "linux").body
    assert "Log out and back in" in body
    assert "Try again" in body
    assert "Deck" not in body


def test_deck_permission_says_restart_the_deck() -> None:
    body = docker_advice.advise("permission", "deck").body
    assert "Restart the Deck" in body
    assert "Try again" in body
    assert "computer" not in body


def test_a_wsl_install_names_its_distro_and_never_docker_desktop() -> None:
    body = docker_advice.advise("wsl", "windows", distro="dml-arch").body

    assert "dml-arch" in body
    assert "Docker Desktop" not in body


# ------------------------------------------------------------- unreachable


@pytest.mark.parametrize(
    "said",
    [
        NPIPE,
        "docker ps exited 1: Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?",
        "docker ps exited 1: failed to connect to the docker API at unix:///var/run/docker.sock: "
        "connect: no such file or directory",
        "docker ps exited 1: permission denied while trying to connect to the Docker daemon "
        "socket at unix:///var/run/docker.sock",
    ],
)
def test_docker_not_answering_is_recognised_in_each_wording_it_has(said: str) -> None:
    assert docker_advice.unreachable(docker.DockerCommandError(said))


@pytest.mark.parametrize(
    "said",
    [
        "docker compose up exited 1: the world service has no image: build it first",
        "Port 3724 is in use by another program.",
    ],
)
def test_a_failure_docker_answered_is_not_docker_not_answering(said: str) -> None:
    assert not docker_advice.unreachable(docker.DockerCommandError(said))


def test_a_missing_cli_is_not_unreachable_it_has_its_own_sentence() -> None:
    assert not docker_advice.unreachable(docker.DockerCliMissingError("no docker"))


# ---------------------------------------------------------------- problem_of


def test_a_pipe_error_on_windows_is_docker_not_running() -> None:
    exc = docker.DockerCommandError(NPIPE)
    assert docker_advice.problem_of(exc, distro=None, deck_docker_removed=False) == "not-running"


def test_no_cli_is_missing_and_on_a_deck_that_lost_it_removed() -> None:
    exc = docker.DockerCliMissingError("Docker could not be found")
    assert docker_advice.problem_of(exc, distro=None, deck_docker_removed=False) == "missing"
    assert docker_advice.problem_of(exc, distro=None, deck_docker_removed=True) == "removed"


@pytest.mark.parametrize(
    "said",
    [
        "permission denied while trying to connect to the Docker daemon socket at "
        "unix:///var/run/docker.sock",
        "open //./pipe/docker_engine: Access is denied.",
    ],
)
def test_a_refused_socket_is_permission(said: str) -> None:
    exc = docker.DockerCommandError(f"docker ps exited 1: {said}")
    assert docker_advice.problem_of(exc, distro=None, deck_docker_removed=False) == "permission"


UNRECOGNISED = (
    'docker ps --format {{.Names}} exited 1: context "remote": context not found: open '
    "meta.json: The system cannot find the path specified.",
    "docker ps exited 1: tls: failed to verify certificate: x509: certificate signed by "
    "unknown authority",
)


@pytest.mark.parametrize("said", UNRECOGNISED)
def test_an_error_docker_answered_that_yulon_does_not_recognise_is_unknown(said: str) -> None:
    """F2 (T194 final fix): never "isn't running" for something Docker answered."""
    exc = docker.DockerCommandError(said)
    assert not docker_advice.unreachable(exc)
    assert docker_advice.problem_of(exc, distro=None, deck_docker_removed=False) == "unknown"


def test_a_failure_that_is_not_dockers_at_all_is_unknown() -> None:
    exc = RuntimeError("boom")
    assert docker_advice.problem_of(exc, distro=None, deck_docker_removed=False) == "unknown"


@pytest.mark.parametrize("host", HOSTS)
def test_unknown_says_docker_answered_and_where_its_words_are(host: docker_advice.Host) -> None:
    advice = docker_advice.advice_for(
        docker.DockerCommandError(UNRECOGNISED[0]),
        distro=None,
        host=host,
        deck_docker_removed=False,
    )

    assert advice.body == (
        "Docker answered with an error Yu'lon doesn't recognise. The Logs tab has what it "
        "said; press Try again once it's sorted."
    )
    assert advice.action is None
    assert "running" not in advice.body


def test_a_server_in_a_distro_is_the_distros_docker() -> None:
    exc = docker.DockerCommandError("docker ps exited 1: Cannot connect to the Docker daemon")
    assert docker_advice.problem_of(exc, distro="dml-arch", deck_docker_removed=False) == "wsl"


def test_a_deleted_distro_is_told_in_wsls_own_sentence() -> None:
    gone = (
        "The WSL distro dml-arch no longer exists - it was deleted, or renamed. Everything on "
        "this tab runs docker inside dml-arch, so nothing here can start."
    )
    advice = docker_advice.advice_for(
        docker.DockerCommandError(gone),
        distro="dml-arch",
        host="windows",
        deck_docker_removed=False,
    )

    assert advice.body == gone
    assert advice.action is None
    assert hasattr(wsl, "missing_distro_problem"), "the sentence's author moved"


def test_advice_for_reads_this_machine_when_not_told(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yulon_platform, "is_steamos", lambda: True)
    monkeypatch.setattr(yulon_platform, "_which", lambda _name, path=None: None)

    advice = docker_advice.advice_for(docker.DockerCliMissingError("x"), distro=None)

    assert advice.action == "reinstall-deck"
    assert docker_advice.this_host() == "deck"


# ------------------------------------------------------- open_docker_desktop


class _Run:
    """`run` for `open_docker_desktop`: answers Windows' probe, records every argv."""

    def __init__(self, found: str = "", start_rc: int = 0) -> None:
        self.found = found
        self.start_rc = start_rc
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if any("Start-Process" in part for part in argv):
            return subprocess.CompletedProcess(argv, self.start_rc, "", "")
        if argv[:1] == ["open"]:
            return subprocess.CompletedProcess(argv, self.start_rc, "", "")
        return subprocess.CompletedProcess(argv, 0, self.found + "\n", "")


@pytest.fixture
def no_known_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in yulon_platform._DOCKER_DESKTOP_ROOT_VARS:
        monkeypatch.delenv(var, raising=False)


def test_windows_starts_the_docker_desktop_it_finds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_known_installs: None
) -> None:
    exe = tmp_path / "Docker" / "Docker Desktop.exe"
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setattr(yulon_platform, "detect", lambda: "windows")
    run = _Run(found=str(exe))

    said = yulon_platform.open_docker_desktop(run=run)

    assert said is None
    started = [argv for argv in run.calls if any("Start-Process" in p for p in argv)]
    assert len(started) == 1 and str(exe) in started[0][-1]


def test_windows_without_docker_desktop_is_told_where_to_get_it(
    monkeypatch: pytest.MonkeyPatch, no_known_installs: None
) -> None:
    monkeypatch.setattr(yulon_platform, "detect", lambda: "windows")
    run = _Run(found="")

    said = yulon_platform.open_docker_desktop(run=run)

    assert said == yulon_platform._MANUAL_START_DOCKER_DESKTOP
    assert not any(any("Start-Process" in p for p in argv) for argv in run.calls)


def test_a_start_that_windows_refuses_is_the_manual_sentence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_known_installs: None
) -> None:
    exe = tmp_path / "Docker Desktop.exe"
    exe.write_text("")
    monkeypatch.setattr(yulon_platform, "detect", lambda: "windows")

    said = yulon_platform.open_docker_desktop(run=_Run(found=str(exe), start_rc=1))

    assert said == yulon_platform._MANUAL_START_DOCKER_DESKTOP


def test_a_run_that_cannot_spawn_is_the_manual_sentence_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, no_known_installs: None
) -> None:
    monkeypatch.setattr(yulon_platform, "detect", lambda: "windows")

    def broken(_argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise OSError("no powershell")

    assert yulon_platform.open_docker_desktop(run=broken) == (
        yulon_platform._MANUAL_START_DOCKER_DESKTOP
    )


def test_macos_opens_docker_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yulon_platform, "detect", lambda: "macos")
    run = _Run()

    assert yulon_platform.open_docker_desktop(run=run) is None
    assert run.calls == [["open", "-a", "Docker"]]


def test_a_mac_without_docker_desktop_is_told_where_to_get_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(yulon_platform, "detect", lambda: "macos")

    said = yulon_platform.open_docker_desktop(run=_Run(start_rc=1))

    assert said is not None and "Applications" in said
    assert text_faults(said) == []


def test_linux_has_no_docker_desktop_to_open_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(yulon_platform, "detect", lambda: "linux")
    run = _Run()

    said = yulon_platform.open_docker_desktop(run=run)

    assert said is not None and "sudo systemctl start docker" in said
    assert run.calls == [], "the Windows probe ran on Linux"


def test_a_probe_that_hangs_is_given_up_on_and_the_press_comes_back(
    monkeypatch: pytest.MonkeyPatch, no_known_installs: None
) -> None:
    """A wedged PowerShell must not hold Open Docker Desktop grey for ever."""
    from yulon import runner

    monkeypatch.setattr(yulon_platform, "detect", lambda: "windows")
    asked: list[float | None] = []

    def hung(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        timeout = kw.get("timeout")
        asked.append(timeout)  # type: ignore[arg-type]
        assert timeout is not None, f"{argv[0]} was run with no deadline"
        # What `runner.run()` hands back when the deadline passes.
        return subprocess.CompletedProcess(argv, 124, "", f"timed out after {timeout}s")

    monkeypatch.setattr(runner, "run", hung)

    said = yulon_platform.open_docker_desktop()

    assert said == yulon_platform._MANUAL_START_DOCKER_DESKTOP
    assert asked and all(t is not None and t <= 60 for t in asked), asked
