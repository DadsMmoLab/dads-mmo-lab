"""The one bash file that survives 7.2: `catalog/installers/steam-deck/setup-gaming-mode.sh`.

Gate the tool, not the payload. Nothing here starts Docker, but most of it is
still behavioural: bash sources `$BASH_ENV` when it runs a script
non-interactively, and a shell function beats a `PATH` lookup, so `_stubs()`
replaces `docker`, `pgrep`, `clear` and `sleep` with functions and the script
runs end to end in milliseconds. A shim DIRECTORY would not work — the script's
own `export PATH="/usr/bin:..."` puts the real `/usr/bin/docker` in front of
anything the caller prepended — which is why the stubs are functions.

`sleep` records its argument instead of waiting. That is what lets a
300-second client wait be asserted inside one test second, and it is the seam
the no-tty tests read: the defect they pin is a `read` that returns instantly
at EOF *where a sleep belonged*, so "did it sleep" is the question.

Some tests remain source-shape reads of the script text (`_code()`): they pin
how a line is spelled, not what the script does, and a harmless respelling fails
them exactly as a real defect would. Comment lines are stripped before those
reads, so a rule quoted in the header cannot satisfy an assertion about the code.
Each such test says so in its own docstring, which is where the fact belongs --
this sentence used to give the number, and the number was wrong (it said three;
there are four `_code()` call sites). A count of its own contents is the one
thing a module docstring cannot keep true.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.support_bash import bash_available
from yulon import resources

SCRIPT = resources.installers_dir() / "steam-deck" / "setup-gaming-mode.sh"

needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)

HANG_BOUND = 300.0
"""A DEADLOCK BREAKER, not an assertion about how fast the script runs.

Every test here stubs `sleep` so it records instead of waiting, so a healthy
run of the longest of them costs milliseconds. Measured on yulon-ubuntu
2026-09-02: this whole file took 3.57s serially, of which the 300-second client
wait was 0.16s, and under `-n auto --dist loadfile` alongside the rest of the
suite no test in it reached the slowest-twelve list at all (the twelfth was
0.61s).

The number is nonetheless large on purpose, because the bound is on the CHILD
and the contention is on the BOX. A gate reviewer watched
`test_without_a_terminal_the_client_wait_sleeps_instead_of_reading_stdin` spend
the whole of the previous 60-second bound under 15-way parallel load and then
pass 14 times out of 14 re-run serially, and that VM runs its fifteen workers
with about 2 GB free -- a starved or swapping box can stall a process for a
minute without anything being wrong with it. A bound sized like a stopwatch
turns that into a red suite, and the run it happened in was read as a real
single failure (bug-checklist section 33): three clean re-runs later, a merge
went through on a result nobody had explained.

So it is sized against the failure it can actually catch. Contention is bounded
-- some multiple of a run that costs milliseconds -- while a hang is unbounded,
and any finite bound catches an unbounded wait eventually. Deleting the bound
is not an option: without one this file's pty test WEDGED the suite when its
subject regressed, which CI reports as a stuck job rather than a red one (see
`test_with_a_terminal_enter_still_stops_the_server`). Five minutes is the price
of reporting such a regression as a failure.
"""

DWELL_PROOF = 3.0
"""The one bound here that IS an assertion, and it points the other way.

`test_the_default_pause_holds_the_closing_message_on_screen` requires the
script to be STILL RUNNING when this elapses, so a slow or loaded box makes it
pass more surely rather than less. It is small for the same reason
`HANG_BOUND` is large.
"""


def _code() -> str:
    """The script minus comment and blank lines."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#"))


class _Stubs:
    """A `$BASH_ENV` file plus the scratch files its functions write."""

    def __init__(self, path: Path, sleep_log: Path, counter: Path) -> None:
        self.path = path
        self._sleep_log = sleep_log
        self._counter = counter

    @property
    def env(self) -> dict[str, str]:
        return {
            "BASH_ENV": str(self.path),
            "YULON_TEST_SLEEP_LOG": str(self._sleep_log),
            "YULON_TEST_PGREP_COUNTER": str(self._counter),
        }

    def sleeps(self) -> list[str]:
        """Every `sleep <n>` the script asked for, in order."""
        if not self._sleep_log.exists():
            return []
        return self._sleep_log.read_text(encoding="utf-8").split()


def _stubs(tmp_path: Path, *, pgrep_true_calls: int) -> _Stubs:
    """Shell-function stand-ins for the four commands the script shells out to.

    `pgrep` reports a client for its first `pgrep_true_calls` calls and none
    after, which is how "a client started and later closed" is staged without a
    client. `docker compose logs` prints the ready line so the readiness loop
    matches on its first pass.
    """
    sleep_log = tmp_path / "sleeps.txt"
    counter = tmp_path / "pgrep-calls.txt"
    stub_file = tmp_path / "stubs.sh"
    stub_file.write_text(
        "docker() {\n"
        '    if [ "${2:-}" = "logs" ]; then printf "worldserver: ready...\\n"; fi\n'
        "    return 0\n"
        "}\n"
        "clear() { :; }\n"
        'sleep() { printf "%s\\n" "${1:-}" >> "$YULON_TEST_SLEEP_LOG"; }\n'
        "pgrep() {\n"
        '    __n=$(cat "$YULON_TEST_PGREP_COUNTER" 2>/dev/null || printf 0)\n'
        "    __n=$((__n + 1))\n"
        '    printf "%s" "$__n" > "$YULON_TEST_PGREP_COUNTER"\n'
        f'    [ "$__n" -le {pgrep_true_calls} ]\n'
        "}\n",
        encoding="utf-8",
    )
    return _Stubs(stub_file, sleep_log, counter)


def _server_dir(tmp_path: Path) -> Path:
    """A folder shaped like one Yu'lon installed a game into."""
    d = tmp_path / "wow-server-playerbots"
    d.mkdir()
    (d / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return d


def _run(
    *args: str,
    pause: str | None = "0",
    stubs: _Stubs | None = None,
    timeout: float = HANG_BOUND,
) -> subprocess.CompletedProcess[str]:
    """Run the script with stdin at EOF, as a Steam-launched process would have it.

    `pause` is the on-screen dwell it would otherwise sleep through; `None`
    leaves `YULON_GAMING_MODE_PAUSE` unset so the script's own default applies.
    """
    env = {**os.environ}
    env.pop("YULON_GAMING_MODE_PAUSE", None)
    if pause is not None:
        env["YULON_GAMING_MODE_PAUSE"] = pause
    if stubs is not None:
        env.update(stubs.env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env=env,
    )


@needs_bash
def test_the_gaming_mode_script_parses() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=HANG_BOUND
    )
    assert result.returncode == 0, result.stderr


def test_the_gaming_mode_script_is_lf_with_a_bash_shebang() -> None:
    """CRLF would leave Linux hunting for an interpreter whose name ends in a carriage return.

    `.gitattributes` pins `*.sh` to `eol=lf`; this asserts the bytes on disk
    rather than trusting the rule was applied to a file added after it.
    """
    raw = SCRIPT.read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in raw


@needs_bash
def test_too_few_arguments_exit_2_with_the_usage_line() -> None:
    result = _run()
    assert result.returncode == 2
    assert "<server_dir> <game_id> <ready_regex>" in result.stderr


@needs_bash
def test_too_many_arguments_exit_2() -> None:
    """Four arguments: the count is the only rule broken, each one is otherwise usable.

    The usage line is asserted as well as the code, because bash itself exits 2
    on a file it cannot parse — a return code alone would let a syntax error
    impersonate the arity check.
    """
    result = _run("a", "b", "c", "d")
    assert result.returncode == 2
    assert "<server_dir> <game_id> <ready_regex>" in result.stderr


@needs_bash
def test_a_server_dir_without_a_compose_file_exits_1(tmp_path: Path) -> None:
    """Right arity, real directory, missing `docker-compose.yml` — the one rule broken.

    The script has to reach that check before it needs Docker, so this passes on
    a machine with no Docker installed at all.

    `str(tmp_path)` in the message is the load-bearing assertion: it is what
    makes this test fail when the positionals are swapped. `SERVER_DIR="$2"`
    still exits 1 and still prints "docker-compose.yml" (hardcoded prose) and
    "wow-wotlk" (`$GAME_ID`, which interpolates whatever it holds) — so both of
    those corroborators survive the swap and only the directory tells them apart.
    """
    result = _run(str(tmp_path), "wow-wotlk", "ready")
    assert result.returncode == 1
    assert "docker-compose.yml" in result.stdout
    assert str(tmp_path) in result.stdout
    # Restored 2026-09-02 after review. This diff had replaced the `"wow-wotlk"`
    # assertion with the `tmp_path` one rather than keeping both, and that made
    # `GAME_ID="$2"` -> `"$3"` killable ONLY by the source-shape pin below --
    # which is precisely what that pin's docstring says it is not for. `$1` is
    # covered behaviourally by the path; `$2` needs this line.
    assert "wow-wotlk" in result.stdout


@needs_bash
def test_a_server_dir_holding_docker_compose_yml_is_accepted(tmp_path: Path) -> None:
    """The positive half of the filename pin: this exact name gets past the check.

    The negative test above uses an empty directory, where every spelling of the
    filename is equally absent — so on its own it pins the message, not the name.
    Here the directory holds `docker-compose.yml` and nothing else, so a script
    looking for `compose.yml`, `docker-compose.yaml` or `compose.yaml` rejects a
    correctly installed server directory and never reaches `up -d`.
    """
    stubs = _stubs(tmp_path, pgrep_true_calls=0)
    result = _run(str(_server_dir(tmp_path)), "wow-wotlk", "ready", stubs=stubs)
    assert result.returncode == 0
    assert "No docker-compose.yml" not in result.stdout
    assert "Containers started." in result.stdout
    assert "The server is READY." in result.stdout


@needs_bash
def test_the_default_pause_holds_the_closing_message_on_screen(tmp_path: Path) -> None:
    """Unset `YULON_GAMING_MODE_PAUSE`, and the script must still be dwelling after 3s.

    The seam exists because gaming mode runs this inside a konsole window that
    vanishes on exit, so a bare exit blinks the closing message away. A default
    of `0` — or of 1, 2 or 3 — reinstates exactly that defect. The contract is a
    dwell a person can read, not the specific number, so this asserts the
    process is still alive rather than timing it.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        _run(str(tmp_path), "wow-wotlk", "ready", pause=None, timeout=DWELL_PROOF)


@needs_bash
def test_without_a_terminal_the_client_wait_sleeps_instead_of_reading_stdin(
    tmp_path: Path,
) -> None:
    """No tty and no client: the wait must spend its 300 seconds, not skip them.

    Steam runs a non-Steam-game shortcut with no terminal on stdin. `read -r -t 5`
    then returns 1 at EOF immediately, so the loop's only delay disappears:
    measured on yulon-ubuntu, 300 seconds of waiting collapsed into 2 (60 `pgrep`
    calls), the bare `read -r` after it returned at once, and the stack was
    stopped seconds after starting. Both halves are asserted — sixty five-second
    sleeps really happened, and the closing prompt does not ask for an ENTER that
    can never arrive.

    This is the test a gate reviewer watched spend the whole of the old
    60-second bound under 15-way parallel load, then pass 14 of 14 re-run
    serially (bug-checklist section 33). The bound is now `HANG_BOUND`, which is
    sized so contention cannot reach it, and a timeout is reported here with the
    script's own progress attached — because the damage a rare flake does is not
    the red run, it is that a reader cannot tell it from a real single failure
    and re-runs instead of looking.
    """
    stubs = _stubs(tmp_path, pgrep_true_calls=0)
    try:
        result = _run(str(_server_dir(tmp_path)), "wow-wotlk", "ready", stubs=stubs)
    except subprocess.TimeoutExpired as expired:
        pytest.fail(
            f"the script was killed after {expired.timeout}s, having recorded "
            f"{len(stubs.sleeps())} sleeps. A count near the number asserted below is a box "
            "that stalled part-way, not a script that hung, and nothing is wrong with the "
            "script; a count near zero is the regression this test exists for."
        )
    assert result.returncode == 0
    assert stubs.sleeps().count("5") == 60
    assert "No client detected in 300s - stopping the server." in result.stdout
    assert "press ENTER" not in result.stdout


@needs_bash
def test_without_a_terminal_the_client_is_watched_until_it_closes(tmp_path: Path) -> None:
    """The second wait has the same EOF hole, and its own consequence.

    `pgrep` reports a client for three calls and none after: one for the search
    loop, two for the watch loop. Without the guard the watch loop's `read -r -t 3`
    returns at EOF instead of waiting, so the loop spins through a running client
    at full speed — the sleeps below are the proof it does not.
    """
    stubs = _stubs(tmp_path, pgrep_true_calls=3)
    result = _run(str(_server_dir(tmp_path)), "wow-wotlk", "ready", stubs=stubs)
    assert result.returncode == 0
    assert "Client detected - enjoy." in result.stdout
    assert "Client closed - stopping the server..." in result.stdout
    assert stubs.sleeps().count("3") == 2


@needs_bash
@pytest.mark.skipif(os.name != "posix", reason="opening a pty needs POSIX")
def test_with_a_terminal_enter_still_stops_the_server(tmp_path: Path) -> None:
    """The guard must not cost the documented konsole flow its ENTER seam.

    The other tests hand the script `/dev/null`, so every one of them takes the
    no-tty branch and a guard that always answered "no terminal" would satisfy
    them all. This gives it a real pty, types the one newline the header
    promises works, and requires the server to stop because of it.
    """
    import pty

    stubs = _stubs(tmp_path, pgrep_true_calls=0)
    env = {**os.environ, "YULON_GAMING_MODE_PAUSE": "0", **stubs.env}
    master, slave = pty.openpty()
    try:
        # NOT a `with` block, deliberately. `Popen.__exit__` ends in an
        # unbounded `self.wait()` (CPython 3.12 subprocess.py special-cases only
        # KeyboardInterrupt), so on TimeoutExpired the block exits, waits
        # forever, and the `finally` below never runs -- and closing `master` is
        # the only thing that gives the child EOF and lets it die. Measured
        # 2026-09-02: with the ENTER seam mutated away, this test under the old
        # `with` form returned rc=124 from a 100s outer timeout instead of
        # failing. A test that WEDGES the suite when its subject regresses is
        # worse than no test, because CI reports a stuck job rather than a red one.
        proc = subprocess.Popen(
            ["bash", str(SCRIPT), str(_server_dir(tmp_path)), "wow-wotlk", "ready"],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            os.close(slave)
            slave = -1
            os.write(master, b"\n")
            stdout, _ = proc.communicate(timeout=HANG_BOUND)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise
    finally:
        if slave != -1:
            os.close(slave)
        os.close(master)
    assert proc.returncode == 0
    assert "or press ENTER here to stop it now." in stdout
    assert "Stopping the server..." in stdout
    # It stopped because ENTER arrived, not because the 300s wait ran out.
    assert "No client detected" not in stdout
    assert stubs.sleeps().count("5") == 0


def test_the_three_positionals_are_server_dir_game_id_and_ready_regex() -> None:
    """A source-shape pin: it reads the text, not the behaviour.

    `SERVER_DIR="${1}"` would fail it while behaving identically, and it is not
    what makes a `$1`/`$2` swap fail — `test_a_server_dir_without_a_compose_file_exits_1`
    does that, by asserting the directory it named comes back in the message.
    Kept because it names the contract in one place a reader can check.
    """
    code = _code()
    assert 'SERVER_DIR="$1"' in code
    assert 'GAME_ID="$2"' in code
    assert 'READY_REGEX="$3"' in code


def test_it_stops_its_own_stack_with_stop_and_never_down() -> None:
    """`down` removes the containers, and the next `up` then re-runs the database import.

    A source-shape pin, and honest about which half earns its keep: `stop` → `down`
    is a real hazard and this catches it, while the `up -d` line would also fail on
    a harmless respelling such as `up --detach`.
    """
    code = _code()
    assert "docker compose up -d" in code
    assert "docker compose stop" in code
    assert "docker compose down" not in code


def test_it_never_reaches_for_another_games_containers() -> None:
    """The original swept `docker ps` for every container named like WoW and stopped it.

    A source-shape pin: it asserts the script never spells those commands, which
    is the only way to state "it does not touch a container it did not start"
    without standing up two games' stacks.
    """
    code = _code()
    assert re.search(r"\bdocker (stop|rm|kill)\b", code) is None
    assert "docker ps" not in code


def test_it_detects_the_client_and_launches_nothing() -> None:
    """It waits on `pgrep`; a line ending in a bare `&` would mean it started something."""
    code = _code()
    assert "pgrep" in code
    assert re.search(r"[^&>]&[ \t]*$", code, re.MULTILINE) is None


def test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number() -> None:
    """Every `timeout=` here must be one of the two named ones, and nothing else.

    A source-shape read of THIS file, not of the script — the kind its module
    docstring warns about, and deliberate: what it pins is that the reasoning
    stays attached to the number. The bounds are the only thing in this file a
    loaded box can move, and the last bare one that was in here (60 seconds on a
    run measured at 0.16) produced a red suite nobody could explain and a merge
    made on three clean re-runs instead (bug-checklist section 33). A number
    typed at a call site carries no docstring, so the next person to add a
    subprocess here cannot inherit the argument for its size.

    `HANG_BOUND` and `DWELL_PROOF` are named individually rather than counted:
    they mean opposite things — one must never be reached, the other must be —
    and a test that only asked "is it a name?" would let a hang bound be sized
    like the dwell one.
    """
    bounds = [
        keyword.value
        for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "timeout"
    ]
    assert bounds, "no wall-clock bound was found at all, so this pins nothing"
    spelled = [ast.unparse(value) for value in bounds]
    # `timeout` itself is `_run()`'s own parameter being forwarded to
    # `subprocess.run()`; its default is `HANG_BOUND`, one hop up.
    assert set(spelled) == {"HANG_BOUND", "DWELL_PROOF", "timeout"}, spelled
