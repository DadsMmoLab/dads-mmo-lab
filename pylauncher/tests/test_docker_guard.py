"""The autouse guard that keeps a unit test off the box's own docker daemon.

`tests/conftest.py` grew `_no_unit_test_talks_to_a_real_docker_daemon` on
2026-09-05, and it caught twelve tests the day it was written. Nothing then
tested the guard itself: `grep -rn 'DOCKER_ARGV0S|shelled out' tests/` outside
`conftest.py` returned nothing (review, m910q 2026-09-05). A guard nobody drives
is a guard that stops working silently, and this one's whole job is to catch
things nobody wrote a test for — the failure it prevents is invisible on a box
with no docker CLI, which is every box this suite normally runs on.

So: it fires, it lets everything else through, it covers both `runner` routes,
and the argv it recognises is derived from `platform.docker_prefix()` rather
than retyped — that last one is the assertion that survives somebody teaching
the resolver a new spelling.
"""

from __future__ import annotations

import sys

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


@pytest.mark.parametrize(
    "spelling", ["docker", r"C:\Program Files\Docker\resources\bin\docker.exe"]
)
def test_every_argv0_the_local_route_can_produce_is_one_the_guard_knows(
    spelling: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DOCKER_ARGV0S` is a list of spellings, and `platform` is where they come from.

    Asserted by driving `platform.docker_prefix()` with the host's resolver
    pinned, rather than by comparing two hand-written tuples: the hole this
    closes is a resolver that learns a spelling the guard's tuple does not
    have, and two lists typed by the same person do not catch that.
    """
    monkeypatch.setattr(platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(
        platform, "_which", lambda name, path=None: spelling if "docker" in name else None
    )

    prefix = platform.docker_prefix(None)

    assert prefix is not None, "the resolver was pinned to a docker it can find"
    assert argv_reaches_the_docker_cli([*prefix, "ps"]), (
        f"platform.docker_prefix() puts {prefix[0]!r} at argv[0] and the guard's "
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
