"""The one bash file that survives 7.2: `catalog/installers/steam-deck/setup-gaming-mode.sh`.

Gate the tool, not the payload. `bash -n` proves the file parses and the runs
with a wrong argument count prove its contract, so neither needs Docker; the
rest are argv-shaped reads of the source, which is how the rules that made the
original launcher unsafe next to a second game get pinned without starting a
stack. Comment lines are stripped before those reads, so a rule quoted in the
header cannot satisfy an assertion about the code.
"""

from __future__ import annotations

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


def _code() -> str:
    """The script minus comment and blank lines."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#"))


def _run(*args: str, pause: str = "0") -> subprocess.CompletedProcess[str]:
    """Run the script. `pause` is the on-screen dwell it would otherwise sleep through."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "YULON_GAMING_MODE_PAUSE": pause},
    )


@needs_bash
def test_the_gaming_mode_script_parses() -> None:
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=30)
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
    """Right arity, real directory, no `docker-compose.yml`.

    The script has to reach that check before it needs Docker, so this passes on
    a machine with no Docker installed at all.
    """
    result = _run(str(tmp_path), "wow-wotlk", "ready")
    assert result.returncode == 1
    assert "docker-compose.yml" in result.stdout
    assert "wow-wotlk" in result.stdout


def test_the_three_positionals_are_server_dir_game_id_and_ready_regex() -> None:
    code = _code()
    assert 'SERVER_DIR="$1"' in code
    assert 'GAME_ID="$2"' in code
    assert 'READY_REGEX="$3"' in code


def test_it_stops_its_own_stack_with_stop_and_never_down() -> None:
    """`down` removes the containers, and the next `up` then re-runs the database import."""
    code = _code()
    assert "docker compose up -d" in code
    assert "docker compose stop" in code
    assert "docker compose down" not in code


def test_it_never_reaches_for_another_games_containers() -> None:
    """The original swept `docker ps` for every container named like WoW and stopped it."""
    code = _code()
    assert re.search(r"\bdocker (stop|rm|kill)\b", code) is None
    assert "docker ps" not in code


def test_it_detects_the_client_and_launches_nothing() -> None:
    """It waits on `pgrep`; a line ending in a bare `&` would mean it started something."""
    code = _code()
    assert "pgrep" in code
    assert re.search(r"[^&>]&[ \t]*$", code, re.MULTILINE) is None
