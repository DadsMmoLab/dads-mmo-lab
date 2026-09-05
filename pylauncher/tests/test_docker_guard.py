"""The autouse guard that keeps a unit test off the box's own docker daemon.

`tests/conftest.py` grew `_no_unit_test_talks_to_a_real_docker_daemon` on
2026-09-05, and it caught twelve tests the day it was written. Nothing then
tested the guard itself: `grep -rn 'DOCKER_ARGV0S|shelled out' tests/` outside
`conftest.py` returned nothing (review, m910q 2026-09-05). A guard nobody drives
is a guard that stops working silently, and this one's whole job is to catch
things nobody wrote a test for.

THE BOX THIS SUITE RUNS ON HAS A LIVE DAEMON. This paragraph said the opposite
until 2026-09-05 — "the failure it prevents is invisible on a box with no docker
CLI, which is every box this suite normally runs on" — and that is false for the
box the mandated gate uses. Measured on m910q that day: `which -a docker` gives
`/usr/bin/docker` and `/bin/docker`, `docker info` answers server version
29.7.2, and the account running the suite is in the `docker` group. An unguarded
reach there is not a silent no-op; it inspects a real daemon, and the round-3
vanilla-marker failure was this guard firing on that box. What IS invisible is
the reach itself: a real `docker inspect` against a container that does not
exist returns a perfectly usable "no", which is what the test wanted anyway.

So: it fires, it lets everything else through, it covers both `runner` routes,
and the argv it recognises is derived from `platform.docker_programs()` on each
of the three platforms rather than retyped — that last one is the assertion that
survives somebody teaching the resolver a new spelling.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.conftest import DOCKER_ARGV0S, argv_reaches_the_docker_cli
from yulon import platform, runner


def test_the_guard_fails_a_unit_test_that_shells_out_to_docker() -> None:
    """The fixture is autouse, so this call is already guarded when the body runs.

    Driven through `runner.run` and not through `argv_reaches_the_docker_cli`,
    because the rule being right is not the claim — the claim is that the rule
    is WIRED IN, and an autouse fixture that has quietly stopped applying looks
    exactly like a passing suite.
    """
    with pytest.raises(pytest.fail.Exception) as caught:
        runner.run(["docker", "inspect", "no-such-container"])

    assert "shelled out to the real docker CLI" in str(caught.value)
    assert "no-such-container" in str(caught.value), "the refusal quotes the argv it refused"


def test_the_guard_lets_a_subprocess_that_is_not_docker_run() -> None:
    """A guard that refused everything would pass the test above and break the suite.

    This spawns a real child process on purpose. That is the point: the guard
    intercepts `runner.run` for every test in the suite, and "only docker argv
    is refused" is a claim about the OTHER branch, which no docker-shaped
    fixture can exercise.
    """
    done = runner.run([sys.executable, "-c", "print('not docker')"])

    assert done.returncode == 0
    assert "not docker" in done.stdout


def test_the_guard_covers_the_streaming_route_and_refuses_before_the_first_line() -> None:
    """`follow_logs()` and `run_attached()` reach the CLI through `runner.stream()`.

    The guard hooked `runner.run` alone until 2026-09-05. Widening it changed no
    result on m910q (whole suite, 0 failures either way), so what is asserted
    here is the shape rather than a fixed defect: the refusal happens when
    `stream()` is CALLED, not when it is first iterated. `runner.stream` is a
    generator function, and a guard written as one would run no code at all
    until a caller asked for a line — so a caller that builds the generator and
    drops it (or iterates it inside its own `except`) would reach the daemon
    with the guard installed and green.
    """
    with pytest.raises(pytest.fail.Exception):
        runner.stream(["docker", "logs", "-f", "no-such-container"])


def _off_path_docker(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin `platform` to a HOST of `host` whose docker is installed but off this PATH.

    That state is the only one in which `docker_programs()` returns anything but
    the bare name, and it is the state the two off-Linux branches exist for: a
    launcher started from Finder or from a just-run Docker Desktop installer
    (see `platform.docker_programs()`' own docstring for both incidents).

    `_which` answering None is what makes `_windows_docker_programs()` and
    `_macos_docker_programs()` go past their early return; every disk probe
    answering True is what makes them believe the layouts they know about. The
    `os.environ` roots are needed because `_windows_docker_bins()` reads them
    and skips a root that is unset, which every Linux box's is.
    """
    monkeypatch.setattr(platform, "detect", lambda: host)
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\pk\AppData\Local")
    monkeypatch.setenv("ProgramW6432", r"C:\Program Files")


@pytest.mark.parametrize("host", ["linux", "macos", "windows"])
def test_every_argv0_the_local_route_can_produce_is_one_the_guard_knows(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DOCKER_ARGV0S` is a list of spellings, and `platform` is where they come from.

    Every candidate the resolver can put at argv[0], on each of the three
    platforms, asked of the real `platform.docker_programs()`. The hole this
    closes is a resolver that learns a spelling the guard's tuple does not have,
    and two lists typed by the same person do not catch that.

    THIS TEST ASSERTED NOTHING FOR ITS WINDOWS HALF UNTIL 2026-09-05. It
    parametrized a hand-typed `...\\resources\\bin\\docker.exe`, pinned `_which`
    to return it and called `docker_prefix(None)` — but on Linux
    `docker_programs()` is the hardcoded `("docker",)` and `docker_program()`
    returns the CANDIDATE rather than what `_which` resolved, so both parameters
    asserted `argv_reaches_the_docker_cli(["docker", "ps"])`, twice. Measured on
    m910q that day: replacing the Windows parameter with
    `C:\\nope\\definitely-not-a-docker-cli.exe` left it green, replacing
    `platform.py`'s own `directory / "docker.exe"` with nonsense left the whole
    file green (`8 passed`), and dropping `"docker.exe"` from `DOCKER_ARGV0S`
    was caught only by the hand-typed `["DOCKER.EXE", "ps"]` line below
    (`1 failed, 7 passed`). Pinning `detect()` is what reaches
    `_windows_docker_programs()`; both mutations are red here now.

    What this does NOT own is the SEPARATOR flavour: `Path` is the running
    platform's, so the candidates built here get `/` for their tail on Linux and
    an all-backslash argv[0] never appears. That spelling is owned by the wsl
    case below: `PureWindowsPath` in the rule replaced by `Path` gave `1 failed,
    9 passed` in this file, the one failure being that case's `wsl.exe` spelling
    (m910q, 2026-09-05, round-5 review). The folded `["DOCKER.EXE", "ps"]`
    assertion stays green under the same mutation and must: a bare `DOCKER.EXE`
    has no separator for the flavour to act on, so it owns the case fold and
    nothing else. An earlier version of this paragraph said both go red.
    """
    _off_path_docker(host, monkeypatch)

    candidates = platform.docker_programs()

    assert (len(candidates) > 1) == (host != "linux"), (
        f"on {host} the resolver offered {candidates}. Linux is the bare name and nothing else; "
        "the other two must reach their off-PATH branch or this run asserts only what Linux does"
    )
    for candidate in candidates:
        assert argv_reaches_the_docker_cli([candidate, "ps"]), (
            f"platform.docker_programs() offers {candidate!r} on {host} and the guard's "
            f"{DOCKER_ARGV0S} does not recognise it, so a unit test using it reaches the daemon"
        )


def test_the_argv0_the_local_route_settles_on_is_one_of_those_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end of the same route: what `docker_prefix(None)` actually hands a caller.

    `docker_programs()` answers "everything worth trying" and `docker_program()`
    picks the first one `_which` resolves, so this drives a Windows host where
    the bare name does NOT resolve and the absolute path does — the machine
    whose PATH entry the installer wrote after this process started, which is
    the whole reason that branch exists.

    Kept separate from the enumeration above because it needs the opposite pin
    (`_which` must succeed for something), and `_adopt_cli_directory()` writes
    to `os.environ["PATH"]` on the way through; `monkeypatch.setenv` is what
    puts that back.
    """
    monkeypatch.setattr(platform, "detect", lambda: "windows")
    monkeypatch.setattr(platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\pk\AppData\Local")
    monkeypatch.setenv("ProgramW6432", r"C:\Program Files")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        platform, "_which", lambda name, path=None: None if name == "docker" else name
    )

    prefix = platform.docker_prefix(None)

    assert prefix is not None, "the resolver was pinned to a docker it can find"
    assert prefix[0] != "docker", (
        f"argv[0] is {prefix[0]!r}: the bare name was pinned unresolvable, so a run that still "
        "picks it never reached the off-PATH branch this test is about"
    )
    assert argv_reaches_the_docker_cli([*prefix, "ps"]), (
        f"platform.docker_prefix(None) puts {prefix[0]!r} at argv[0] and the guard's "
        f"{DOCKER_ARGV0S} does not recognise it, so a unit test using it reaches the daemon"
    )


@pytest.mark.parametrize("spelling", ["wsl", r"C:\Windows\System32\wsl.exe"])
def test_every_argv0_the_wsl_route_can_produce_is_one_the_guard_knows(
    spelling: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: a WSL install's docker is reached through `wsl.exe`, not `docker`.

    This is the route the ready-budget lane's own defect lived on — the wait
    watched a distro's daemon and the verdict was read off the host's — so the
    guard missing it would leave exactly those tests free to shell out.
    """
    monkeypatch.setattr(platform, "_which", lambda name, path=None: spelling)

    prefix = platform.docker_prefix("dml-arch")

    assert prefix is not None
    assert prefix[-1] == "docker", "the distro resolves `docker` on its own PATH"
    assert argv_reaches_the_docker_cli([*prefix, "ps"]), (
        f"platform.docker_prefix('dml-arch') puts {prefix[0]!r} at argv[0] and the guard's "
        f"{DOCKER_ARGV0S} does not recognise it"
    )


def test_an_argv_that_is_not_a_list_of_strings_is_not_a_docker_call() -> None:
    """The rule is asked about whatever a caller passed, including nothing at all.

    `runner.run` is wrapped for the whole suite, so the rule sees every argv any
    test builds. An empty list is what a caller with nothing to run produces,
    and raising `IndexError` out of a guard would turn a test's own bug into a
    failure that names docker.
    """
    assert argv_reaches_the_docker_cli([]) is False
    assert argv_reaches_the_docker_cli(None) is False
    assert argv_reaches_the_docker_cli("docker ps") is False, "a string is not an argv here"
    assert argv_reaches_the_docker_cli(["git", "status"]) is False
    assert argv_reaches_the_docker_cli(["DOCKER.EXE", "ps"]) is True, "argv[0] is matched folded"
