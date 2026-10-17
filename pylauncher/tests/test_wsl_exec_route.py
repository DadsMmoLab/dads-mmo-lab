"""T744: every command sent to a server inside a WSL distro reaches docker word for word.

`wsl.exe -d <distro> -- <command>` does not run `<command>`: it hands the command
line to the distro's shell, which expands `$1`, `$f` and `$(...)`, strips
backslashes and parses quotes before docker is started. Measured by the
2026-10-12 security audit (lane D, finding 1) through the real wsl.exe, with
`docker` swapped for `printf [%s]\\n`: the Tortoise update listing reached the
container as `[ -e "" ]`, the DBC copy as `cat > ".yulon-part"`, and My Party's
`$(grep ...)` ran on the distro instead of in the container. `--exec` starts the
program itself, with the argv as given (`wsl.py` holds and `git.distro_owner`
already went that way and were measured working).

The tests model what wsl.exe and the distro's shell do to an argv
(`_what_docker_receives`), check that model against the three measured replays,
and drive every function that splices `platform.docker_prefix()` or
`platform.wsl_prefix()` into an argv. The model is deliberately small: POSIX
word splitting, quotes, backslashes, parameter/command substitution (unset, so
empty, which is what the replays showed), and a refusal for operators.
"""

from __future__ import annotations

import ast
import io
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from yulon import apply, docker, git, party, platform, runner, wsl
from yulon.controller_wow_tortoise import autoupdate
from yulon.controller_wow_wotlk import console, maintenance

DISTRO = "Ubuntu-24.04"
LAUNCHER = "C:\\Windows\\System32\\wsl.exe"
YULON = Path(__file__).resolve().parents[1] / "yulon"


# ------------------------------------------------------------------ the model


class ShellRefused(AssertionError):
    """The distro's shell would read this line as something other than one command."""


def _substitution_end(line: str, i: int) -> int:
    """Index just past the `$(...)` / `${...}` / `` `...` `` / `$name` starting at `line[i]`."""
    if line[i] == "`":
        end = line.find("`", i + 1)
        if end < 0:
            raise ShellRefused(f"unpaired backquote in {line!r}")
        return end + 1
    nxt = line[i + 1 : i + 2]
    if nxt in ("(", "{"):
        close = ")" if nxt == "(" else "}"
        depth, j = 0, i + 1
        while j < len(line):
            if line[j] == nxt:
                depth += 1
            elif line[j] == close:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ShellRefused(f"unclosed {nxt} in {line!r}")
    if nxt and (nxt.isdigit() or nxt in "@*#?$!-"):
        return i + 2
    j = i + 1
    while j < len(line) and (line[j].isalnum() or line[j] == "_"):
        j += 1
    return j if j > i + 1 else i  # a bare `$` stays a `$`


def _sh_words(line: str) -> list[str]:
    """The words `sh -c <line>` runs, every expansion empty (nothing is set, nothing answers)."""
    words: list[str] = []
    word: str | None = None
    i = 0
    while i < len(line):
        c = line[i]
        if c in " \t\n":
            if word is not None:
                words.append(word)
                word = None
            i += 1
        elif c == "'":
            end = line.find("'", i + 1)
            if end < 0:
                raise ShellRefused(f"unpaired ' in {line!r}")
            word = (word or "") + line[i + 1 : end]
            i = end + 1
        elif c == '"':
            word = word or ""
            i += 1
            while True:
                if i >= len(line):
                    raise ShellRefused(f'unpaired " in {line!r}')
                d = line[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\" and line[i + 1 : i + 2] in ("$", "`", '"', "\\", "\n"):
                    word += line[i + 1]
                    i += 2
                elif d in "$`" and _substitution_end(line, i) > i:
                    i = _substitution_end(line, i)
                else:
                    word += d
                    i += 1
        elif c == "\\":
            word = (word or "") + line[i + 1 : i + 2]
            i += 2
        elif c in "$`" and _substitution_end(line, i) > i:
            i = _substitution_end(line, i)  # unquoted and empty: no word unless one is open
        elif c in ";&|<>()":
            raise ShellRefused(f"the shell reads {c!r} as an operator in {line!r}")
        elif c == "#" and word is None:
            break
        else:
            word = (word or "") + c
            i += 1
    if word is not None:
        words.append(word)
    return words


_WSL_OPTIONS_WITH_A_VALUE = ("-d", "--distribution", "--cd", "-u", "--user")


def _wsl_split(argv: list[str]) -> tuple[str, list[str]]:
    """(`"exec"` or `"shell"`, the command after wsl.exe's own options)."""
    assert argv[0] == LAUNCHER, argv
    i = 1
    while i < len(argv):
        if argv[i] in _WSL_OPTIONS_WITH_A_VALUE:
            i += 2
        elif argv[i] in ("--exec", "-e"):
            return "exec", argv[i + 1 :]
        elif argv[i] == "--":
            return "shell", argv[i + 1 :]
        else:
            return "shell", argv[i:]
    raise AssertionError(f"no command in {argv}")


def _what_docker_receives(argv: list[str]) -> list[str]:
    """The argv the distro starts for `argv`, as wsl.exe and the distro's shell deliver it.

    `--exec`: the command line Windows builds (`list2cmdline`) is split back by the
    same Microsoft rules, so the argv arrives as given. `--` (or no separator):
    that command line is a line of shell.
    """
    route, command = _wsl_split(argv)
    if route == "exec":
        return command
    return _sh_words(subprocess.list2cmdline(command))


def _shell_hazards(argv: list[str]) -> list[str]:
    """The arguments the distro's shell would rewrite, if `argv` goes through it at all."""
    route, command = _wsl_split(argv)
    if route == "exec":
        return []
    return [
        part
        for part in command
        if any(ch in part for ch in "$`\\") or part.count('"') % 2 or part.count("'") % 2
    ]


# ------------------------------------------------- the model against measurement

# Argv captured from the real functions and what `printf [%s]\n`, put in docker's
# place, printed when replayed through the real wsl.exe on Windows (audit
# 2026-10-12, proofs/security-platform/*_argv.json and *_replay_output.txt).
_MEASURED = [
    (
        [LAUNCHER, "-d", DISTRO, "--", "docker", "exec", "tw-worldserver", "sh", "-c",
         'cd "/opt/updates/world" 2>/dev/null || exit 3; echo OK; for f in *.sql; do '
         '[ -e "$f" ] || continue; sha1sum "$f"; done'],
        3,
        ["sh", "-c", 'cd "/opt/updates/world" 2>/dev/null || exit 3; echo OK; for f in *.sql; '
         'do [ -e "" ] || continue; sha1sum ""; done'],
    ),
    (
        [LAUNCHER, "-d", DISTRO, "--cd", "/tmp", "--", "docker", "compose", "run", "--rm",
         "--no-deps", "-T", "--entrypoint", "sh", "ac-client-data-init", "-c",
         'cat > "$1.yulon-part" && mv -f "$1.yulon-part" "$1"', "sh",
         "/azerothcore/env/dist/data/dbc/ChrRaces.dbc"],
        3,
        ["--rm", "--no-deps", "-T", "--entrypoint", "sh", "ac-client-data-init", "-c",
         'cat > ".yulon-part" && mv -f ".yulon-part" ""', "sh",
         "/azerothcore/env/dist/data/dbc/ChrRaces.dbc"],
    ),
    (
        [LAUNCHER, "-d", DISTRO, "--", "docker", "exec", "ac-worldserver", "sh", "-c",
         'printf "ALE %s\\n" "$(grep -c -a -F "ALE.ScriptPath" '
         '"/azerothcore/env/dist/bin/worldserver" || true)"; printf "CONTROL %s\\n" '
         '"$(grep -c -a -F -e "AiPlayerbot.Enabled" -e "Playerbots.Enabled" '
         '"/azerothcore/env/dist/bin/worldserver" || true)"'],
        3,
        ["sh", "-c", 'printf "ALE %s\\n" ""; printf "CONTROL %s\\n" ""'],
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    "argv, docker_words, measured", _MEASURED, ids=["tortoise", "dbc", "party"]
)
def test_the_shell_model_gives_what_the_real_wsl_exe_delivered(
    argv: list[str], docker_words: int, measured: list[str]
) -> None:
    """The model is only worth what it reproduces: the three measured replays, exactly."""
    assert _what_docker_receives(argv)[docker_words:] == measured
    assert _shell_hazards(argv), "a measured breakage the guard would have let through"


def test_the_model_passes_an_exec_argv_through_and_flags_a_shell_one() -> None:
    exec_argv = [LAUNCHER, "-d", DISTRO, "--exec", "docker", "exec", "c", "sh", "-c", 'echo "$1"']
    assert _what_docker_receives(exec_argv) == exec_argv[4:]
    assert _shell_hazards(exec_argv) == []
    plain = [LAUNCHER, "-d", DISTRO, "--", "docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"]
    assert _what_docker_receives(plain) == plain[4:]
    assert _shell_hazards(plain) == []
    with pytest.raises(ShellRefused):
        _what_docker_receives([LAUNCHER, "-d", DISTRO, "--", "docker", "logs", "it's"])


# ------------------------------------------------------------------ the prefix


def test_the_wsl_prefix_execs_the_command_rather_than_handing_it_to_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "_which", lambda name, path=None: LAUNCHER)
    assert platform.wsl_prefix(DISTRO) == (LAUNCHER, "-d", DISTRO, "--exec")
    assert platform.wsl_prefix(DISTRO, inside="/srv/x") == (
        LAUNCHER, "-d", DISTRO, "--cd", "/srv/x", "--exec",
    )  # fmt: skip
    assert platform.docker_prefix(DISTRO) == (LAUNCHER, "-d", DISTRO, "--exec", "docker")


_DISTRO_FLAGS = ("-d", "--distribution")
_EXEC_FLAGS = ("--exec", "-e")


def _shell_separators(tree: ast.AST) -> Iterator[int]:
    """Lines building a wsl.exe argv that reaches the distro through its shell.

    Two shapes: a list or tuple holding `-d` (or `--distribution`) and then a
    bare `--`; and one that STARTS `<launcher>, -d, ...` (a non-literal first,
    the distro flag second, which is how wsl.exe is called here) with no
    `--exec` after it, since wsl.exe hands a command with no separator to the
    shell as well. The second shape is anchored at index 1 so docker's own
    `run -d` / `up -d` are not mistaken for it.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
        flags = [i for i, w in enumerate(words) if w in _DISTRO_FLAGS]
        if not flags:
            continue
        after = words[flags[0] :]
        starts_like_wsl = len(words) > 2 and words[0] is None and words[1] in _DISTRO_FLAGS
        if "--" in after or (starts_like_wsl and not any(w in _EXEC_FLAGS for w in after)):
            yield node.lineno


def test_no_code_reaches_a_distro_through_its_shell() -> None:
    """A guard for the next site: every wsl.exe argv in the app uses `--exec`, none `--`.

    Read from the source, so a new `[launcher, "-d", distro, "--", ...]` written
    anywhere fails here before it ships, whichever function it lives in.
    """
    found = [
        f"{path.relative_to(YULON.parent)}:{line}"
        for path in sorted(YULON.rglob("*.py"))
        for line in _shell_separators(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert found == [], f"a wsl.exe argv goes through the distro's shell: {found}"


def test_the_source_guard_sees_the_spelling_it_is_for() -> None:
    """The guard is not blind: the old prefix and a hand-built argv both trip it."""
    old = 'def f(launcher, d, location):\n    return (launcher, "-d", d, *location, "--")\n'
    built = 'x = [launcher, "-d", distro, "--", "sh", "-c", script]\n'
    long_flag = 'x = [launcher, "--distribution", distro, "--", "docker", "ps"]\n'
    no_separator = 'x = [launcher, "-d", distro, "docker", "ps"]\n'
    git_style = 'y = ["diff", old, new, "--", path]\n'
    docker_detached = 'z = [*prefix, "compose", "up", "-d", service]\n'
    docker_run = 'z = [program, "run", "-d", image]\n'
    exec_route = 'w = [launcher, "-d", distro, "--cd", inside, "--exec", "sh", "-c", s]\n'
    assert list(_shell_separators(ast.parse(old))) == [2]
    assert list(_shell_separators(ast.parse(built))) == [1]
    assert list(_shell_separators(ast.parse(long_flag))) == [1]
    assert list(_shell_separators(ast.parse(no_separator))) == [1]
    for clean in (git_style, docker_detached, docker_run, exec_route):
        assert list(_shell_separators(ast.parse(clean))) == [], clean


# ------------------------------------------- every function that splices the prefix


Capture = Callable[[pytest.MonkeyPatch, Path], list[list[str]]]


def _completed(argv: list[str], out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, out, "")


def _recorder(
    seen: list[list[str]], out: str = ""
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A run seam that keeps each argv it is handed and answers rc 0 with `out`."""

    def record(argv: list[str], *_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return _completed(argv, out)

    return record


def _via_runner_run(monkeypatch: pytest.MonkeyPatch, out: str = "") -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr(runner, "run", _recorder(seen, out))
    return seen


def _via_pumped(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        docker,
        "_pumped",
        _recorder(seen),
    )
    return seen


def _tortoise_update_listing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen = _via_runner_run(monkeypatch, "OK\n")
    autoupdate._list_update_files("tw-worldserver", "/opt/updates/world", wsl_distro=DISTRO)
    return seen


def _dbc_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen = _via_pumped(monkeypatch)
    monkeypatch.setattr(platform, "wsl_linux_path_in", lambda path, distro: "/home/pk/server")
    src = tmp_path / "dbc"
    src.mkdir()
    (src / "ChrRaces.dbc").write_bytes(b"WDBC")
    copier = apply.ComposeDbc(tmp_path, "ac-client-data-init", "/azerothcore/env/dist/data", DISTRO)
    copier.copy_dbc_dir(src)
    return seen


def _my_party_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(list(argv))
        return _completed(argv, "ALE 1\nCONTROL 1\n")

    party.read_engine_in_binary("ac-worldserver", wsl_distro=DISTRO, run=run)
    return seen


def _docker_seam(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen = _via_runner_run(monkeypatch)
    docker.exec_output("ac-worldserver", ["sh", "-c", 'echo "$HOME" \\ `id -u`'], wsl_distro=DISTRO)
    return seen


def _exec_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen = _via_pumped(monkeypatch)
    docker.exec_stdin(
        "ac-database",
        ["sh", "-c", 'cat > "$1"', "sh", "/x"],
        io.BytesIO(),
        env={},
        wsl_distro=DISTRO,
    )
    return seen


def _compose_run_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen = _via_pumped(monkeypatch)
    monkeypatch.setattr(platform, "wsl_linux_path_in", lambda path, distro: "/home/pk/it's here")
    docker.compose_run_stdin(
        tmp_path, "svc", "sh", ["-c", 'cat > "$1"', "sh", "/x"], io.BytesIO(), wsl_distro=DISTRO
    )
    return seen


def _follow_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen: list[list[str]] = []

    def stream(argv: list[str], **kw: object) -> Iterator[str]:
        seen.append(list(argv))
        yield from ()

    monkeypatch.setattr(runner, "stream", stream)
    list(docker.follow_logs("ac-worldserver", wsl_distro=DISTRO))
    return seen


def _run_attached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen: list[list[str]] = []

    def stream(argv: list[str], **kw: object) -> Iterator[str]:
        seen.append(list(argv))
        yield from ()

    monkeypatch.setattr(runner, "stream", stream)
    monkeypatch.setattr(platform, "wsl_linux_path_in", lambda path, distro: "/home/pk/server")
    docker.run_attached(["compose", "build", "--build-arg", "X=$Y"], tmp_path, wsl_distro=DISTRO)
    return seen


def _sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    monkeypatch.setattr(apply, "mysql_client", lambda *a, **kw: "mysql")
    sql = apply.DockerSql("ac-database", "pw", wsl_distro=DISTRO)
    return [sql._argv("world")]


def _backup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr("yulon.controller_wow_wotlk.maintenance.subprocess.run", _recorder(seen))
    maintenance.DockerMysql("ac-database", "pw", wsl_distro=DISTRO).dump_into(
        "acore_auth", io.BytesIO()
    )
    return seen


def _restore_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """The T710 load, which prints its ending through `sh -c` and a `$1` (off WSL until T744)."""
    seen: list[list[str]] = []
    monkeypatch.setattr(
        maintenance,
        "_run_reporting_pid",
        _recorder(seen),
    )
    monkeypatch.setattr(maintenance, "mysql_client", lambda *a, **kw: "mysql")
    monkeypatch.setattr(maintenance, "mysql_env", lambda *a, **kw: {})
    mysql = maintenance.DockerMysql("ac-database", "pw", wsl_distro=DISTRO)
    with maintenance.recording_load_pid(lambda pid: None, output_dir=tmp_path, proof="p1"):
        mysql.load_from(io.BytesIO(b"SELECT 1;\n"))
    assert any(maintenance._LOAD_PROOF_SCRIPT in argv for argv in seen), seen
    return seen


def _console(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    return [
        console.distro_attach_argv("ac-worldserver", DISTRO),
        console.attach_argv("ac-worldserver", wsl_distro=DISTRO),
    ]


def _container_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    return [[*git.ContainerGit(wsl_distro=DISTRO)._launcher(), "run", "--rm", "x"]]


def _compose_ls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen: list[list[str]] = []
    monkeypatch.setattr("yulon.wsl.subprocess.run", _recorder(seen, "[]"))
    wsl._compose_ls(DISTRO)
    return seen


def _claim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    seen: list[list[str]] = []

    def popen(argv: list[str], **kw: object) -> object:
        seen.append(list(argv))
        raise OSError("not started in a test")

    monkeypatch.setattr("yulon.docker.subprocess.Popen", popen)
    with pytest.raises(docker.ClaimDockerDown):
        docker._take_claim("yulon-claim", "busybox", None, again=False, wsl_distro=DISTRO)
    return seen


SITES: dict[str, Capture] = {
    "tortoise update listing": _tortoise_update_listing,
    "module DBC copy": _dbc_copy,
    "My Party engine read": _my_party_engine,
    "docker._docker": _docker_seam,
    "docker.exec_stdin": _exec_stdin,
    "docker.compose_run_stdin": _compose_run_stdin,
    "docker.follow_logs": _follow_logs,
    "docker.run_attached": _run_attached,
    "apply.DockerSql": _sql,
    "maintenance.DockerMysql": _backup,
    "maintenance load proof": _restore_load,
    "console attach": _console,
    "git.ContainerGit": _container_git,
    "wsl._compose_ls": _compose_ls,
    "docker._take_claim": _claim,
}


@pytest.mark.parametrize("site", list(SITES))
def test_every_command_reaches_the_distros_docker_word_for_word(
    site: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """What the code built is what the distro starts: no shell between them to rewrite it."""
    monkeypatch.setattr(platform, "_which", lambda name, path=None: LAUNCHER)
    seen = SITES[site](monkeypatch, tmp_path)
    assert seen, f"{site} ran nothing"
    for argv in seen:
        assert _shell_hazards(argv) == [], f"{site}: the distro's shell would rewrite {argv}"
        route, command = _wsl_split(argv)
        assert _what_docker_receives(argv) == command, f"{site}: {argv}"
        assert route == "exec", f"{site} reaches the distro through its shell: {argv}"
