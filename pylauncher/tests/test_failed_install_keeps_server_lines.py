"""A failed install leaves its servers' last lines where a support file finds them (T249).

The report, Windows 11, Tortoise, 0.8.90-Public: the install failed at `ready`
three times with "tortoise-mangosd restarted 4 times while this waited, which is
a crash loop". The player pressed **Save logs for support…**, and the zip's
MANIFEST said `live/  Yu'lon knows no servers`. An install that fails is never
remembered, so the bundle had no install to read a container for -- and the
world server's own log was the only thing that could say why it crashed.

What a failed install DOES leave is its run log: the panel writes every line the
job yields to `logs/runs/install-<game>-<stamp>.log`, and the bundle zips every
run log whether or not any install is known. So the `ready` stage now yields the
world and login servers' last lines before it raises, and those lines travel
with the run into the zip, onto the screen, and into the CLI's transcript.

Nothing here talks to a daemon. The engine's tail seam is a dict, except in the
last tests, which drive the real `docker.last_lines` against the fake CLI from
`tests/support_fake_docker.py`.
"""

from __future__ import annotations

import os
import secrets
import threading
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support_fake_docker import calls, lay_fake_docker, set_fake_log
from tests.support_native import Recorder
from yulon import docker, platform, resources
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError, installer_for
from yulon.support import bundle, runlog
from yulon.support.redact import Redactor
from yulon.support.sources import Sources
from yulon.ui import lines

TORTOISE = load_catalog().get("wow-tortoise")
SPEC = TORTOISE.container_spec()
WORLD, AUTH, DB = SPEC.world, SPEC.auth, SPEC.db

PASSWORD = "s3cretPass99xyz"
"""The install's database password, in a shape ONLY the known-value rule masks.

Not `<word>-<16 hex>` (the generated-shape pattern), not in a `DatabaseInfo`
field and not after `password=` (the two position patterns): a line holding it
in free text is masked by `Redactor`'s known values or by nothing.
"""

CRASH = "ERROR: Map file './maps/0004331.map' is from an incompatible clientversion."
"""The line the player needed: the reason the world server died, from its own log."""

WORLD_LOG = f"Loading maps...\nUsing {PASSWORD} for the world database\n{CRASH}\n"
AUTH_LOG = "Realm daemon listening on 0.0.0.0:3724\n"


class World:
    """The world container as the ready wait sees it, ending in one verdict."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.reads = 0
        self.elapsed = 0.0

    def clock(self) -> float:
        return self.elapsed

    def wait_ready(self, spec: object, ready: docker.ReadySpec) -> bool:
        """The window is spent whole; only a world that `stops` ever prints its banner."""
        self.elapsed += ready.timeout
        return self.verdict == "stops"

    def output(self, spec: object) -> native.WorldOutput:
        self.reads += 1
        first = self.reads == 1
        if self.verdict == "ceiling":
            return native.WorldOutput(f"loading, part {self.reads}", 0, "running")
        if self.verdict == "stops":
            return native.WorldOutput("World initialized\n>> ABORTED", 0, "exited")
        if self.verdict == "loop":
            return native.WorldOutput(f"boot {self.reads}", 0 if first else 9, "running")
        if self.verdict == "gone":
            return native.WorldOutput("boot", 0, "running" if first else "exited")
        if self.verdict == "fatal":
            said = "boot" if first else "boot\nCorrect *.map files not found in data directory."
            return native.WorldOutput(said, 0, "running")
        if self.verdict == "quiet":
            return native.WorldOutput("boot", 0, "running")
        assert self.verdict == "unreadable"
        return native.WorldOutput("", None, "")


class Tails:
    """`Seams.container_tail`: each container's log as a dict, and every ask kept."""

    def __init__(self, logs: dict[str, str | None] | None = None) -> None:
        self.logs = logs if logs is not None else {WORLD: WORLD_LOG, AUTH: AUTH_LOG}
        self.asked: list[str] = []

    def __call__(self, container: str) -> str | None:
        self.asked.append(container)
        return self.logs.get(container)


def _engine(world: World, tails: Tails, *, db_healthy: bool = True) -> native.StagedInstaller:
    rec = Recorder()
    rec.db_healthy = db_healthy
    seams = rec.seams(
        world_output=world.output,
        wait_ready=world.wait_ready,
        monotonic=world.clock,
        container_tail=tails,
    )
    engine = installer_for(
        TORTOISE,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=seams,
    )
    assert isinstance(engine, native.StagedInstaller)
    return engine


def _ctx(server_dir: Path) -> native.StageContext:
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState("wow-tortoise", "abc", "cmangos"),
        cancel=threading.Event(),
        secrets=native.Secrets(PASSWORD),
    )


def _fail(engine: native.StagedInstaller, tmp_path: Path) -> tuple[list[str], InstallerError]:
    """Drive `stage_ready` to its failure. The lines it yielded, and what it raised."""
    said: list[str] = []
    stage: Iterator[str] = engine.stage_ready(_ctx(tmp_path))
    with pytest.raises(InstallerError) as caught:
        for line in stage:
            said.append(line)
    return said, caught.value


def _shown(said: list[str]) -> str:
    """What a reader sees of the yielded lines: `lines.parse`'s text, as the panel shows it."""
    return "\n".join(lines.parse(line).text for line in said)


# -- the report ---------------------------------------------------------------


def test_a_crash_looping_install_s_support_file_carries_the_world_server_s_last_lines(
    tmp_path: Path,
) -> None:
    """The reported case, from the ready stage to the zip, with no install remembered.

    The run log is written the way the panel writes it (`LogPanel._on_line`:
    every non-progress line's parsed text; `_on_finished`: the verdict and its
    Details), and the bundle is the real one, built with `installs=()`.
    """
    said, error = _fail(_engine(World("loop"), Tails()), tmp_path)
    config = tmp_path / "config"
    record = runlog.RunLog.open(runlog.runs_dir(config), "install-wow-tortoise")
    for line in said:
        parsed = lines.parse(line)
        if parsed.kind != "progress":
            record.write(parsed.text)
    record.write(f"--- FAILED: {error}\nDetails:\n{error.detail}")
    record.close()

    dest = tmp_path / "support.zip"
    report = bundle.save(
        dest,
        Sources(config_dir=config, app_log=None, installs=()),
        seams=bundle.Seams(
            live_logs=lambda install, silent: [],
            docker_version=lambda distro: None,
            now=lambda: datetime(2026, 10, 4, 16, 52, tzinfo=UTC),
        ),
        home=tmp_path / "home",
    )

    with zipfile.ZipFile(dest) as archive:
        runs = [name for name in archive.namelist() if name.startswith("runs/")]
        assert len(runs) == 1, report
        text = archive.read(runs[0]).decode("utf-8")
    assert CRASH in text, "the world server's own reason must reach the support file"
    assert AUTH_LOG.strip() in text, "and the login server's lines with it"


VERDICT_WORDS = {
    "loop": "which is a crash loop",
    "gone": "is not running any more",
    "fatal": "It printed a line that means it never will",
    "quiet": "stopped printing anything at all",
    "ceiling": "so it is doing something without finishing it",
    "stops": "came up and then stopped",
}
"""Words only that verdict's sentence has, so each case shows which branch raised."""


@pytest.mark.parametrize("verdict", ["loop", "gone", "fatal", "quiet", "ceiling", "stops"])
def test_every_verdict_that_ends_the_wait_shows_both_servers_last_lines(
    tmp_path: Path, verdict: str
) -> None:
    tails = Tails()
    said, error = _fail(_engine(World(verdict), tails), tmp_path)
    shown = _shown(said)

    assert VERDICT_WORDS[verdict] in str(error), "the verdict named is the one that fired"
    assert CRASH in shown
    assert AUTH_LOG.strip() in shown
    assert tails.asked == [WORLD, AUTH]


def test_the_failure_sentence_names_the_support_button_and_no_command(tmp_path: Path) -> None:
    _said, error = _fail(_engine(World("loop"), Tails()), tmp_path)
    sentence = str(error)

    assert "crash loop" in sentence, "the verdict's own sentence is still the line"
    assert "Save logs for support" in sentence
    assert "docker compose" not in sentence, "a command goes under Details (T248)"


def test_the_last_lines_never_carry_the_install_s_database_password(tmp_path: Path) -> None:
    """Masked where the lines are made: the screen and the CLI see them unredacted otherwise."""
    said, _error = _fail(_engine(World("loop"), Tails()), tmp_path)
    shown = _shown(said)

    assert PASSWORD not in shown
    assert "Using *** for the world database" in shown, "masked, not dropped"


def test_every_server_line_is_relayed_as_a_program_s_output_under_the_container_s_name(
    tmp_path: Path,
) -> None:
    """A server line is never read as one of the engine's own shapes.

    `Step N of M` and `--- ` are what the progress strip, gate scripts and the
    import watchers match; a world log line spelled like one would move the
    strip or end a watcher's wait.
    """
    tails = Tails({WORLD: "Step 3 of 9: not ours\n--- not a stage either\n", AUTH: ""})
    said, _error = _fail(_engine(World("loop"), tails), tmp_path)
    parsed = [lines.parse(line) for line in said]
    relayed = [item for item in parsed if item.text.startswith(f"{WORLD} | ")]

    assert [item.text for item in relayed] == [
        f"{WORLD} | Step 3 of 9: not ours",
        f"{WORLD} | --- not a stage either",
    ]
    assert all(item.kind == "tool" for item in relayed)


def test_a_log_that_cannot_be_read_is_said_and_the_other_is_still_shown(tmp_path: Path) -> None:
    tails = Tails({WORLD: None, AUTH: AUTH_LOG})
    said, error = _fail(_engine(World("loop"), tails), tmp_path)
    shown = _shown(said)

    assert f"{WORLD}'s log could not be read" in shown
    assert AUTH_LOG.strip() in shown
    assert "Save logs for support" in str(error), "the login server's lines were shown"


def test_a_server_that_printed_nothing_is_said_so_and_promised_nothing(tmp_path: Path) -> None:
    """An empty log is an answer, not a failed read: said as such, with no heading over nothing."""
    tails = Tails({WORLD: "", AUTH: ""})
    said, error = _fail(_engine(World("loop"), tails), tmp_path)
    shown = _shown(said)

    assert f"{WORLD} has printed nothing." in shown
    assert f"The last lines {WORLD} printed:" not in shown
    assert "could not be read" not in shown
    assert "Save logs for support" not in str(error)


def test_no_line_read_means_the_sentence_promises_none(tmp_path: Path) -> None:
    tails = Tails({WORLD: None, AUTH: None})
    _said, error = _fail(_engine(World("loop"), tails), tmp_path)

    assert "Save logs for support" not in str(error)


def test_a_docker_that_stopped_answering_is_not_asked_for_the_lines(tmp_path: Path) -> None:
    """Each ask of a daemon that is not answering costs its whole bound, and gets nothing."""
    tails = Tails()
    _said, error = _fail(_engine(World("unreadable"), tails), tmp_path)

    assert "docker stopped answering" in str(error)
    assert tails.asked == []


def test_a_database_that_never_became_healthy_shows_its_own_last_lines(tmp_path: Path) -> None:
    tails = Tails({DB: "[ERROR] InnoDB: Cannot allocate memory for the buffer pool\n"})
    said, error = _fail(_engine(World("loop"), tails, db_healthy=False), tmp_path)

    assert "never reported healthy" in str(error)
    assert "Cannot allocate memory for the buffer pool" in _shown(said)
    assert tails.asked == [DB]


def test_a_longer_log_keeps_its_end_and_whole_lines(tmp_path: Path) -> None:
    filler = "".join(f"filler line {n:07d} {'x' * 60}\n" for n in range(20_000))
    tails = Tails({WORLD: filler + CRASH + "\n", AUTH: ""})
    said, _error = _fail(_engine(World("loop"), tails), tmp_path)
    world = [lines.parse(line).text for line in said if lines.parse(line).kind == "tool"]
    kept = "\n".join(world)

    assert world[-1] == f"{WORLD} | {CRASH}"
    assert len(kept.encode("utf-8")) <= native.FAILURE_TAIL_BYTES + 64 * 1024
    assert len(kept.encode("utf-8")) >= native.FAILURE_TAIL_BYTES // 2
    assert all(line.startswith(f"{WORLD} | filler line ") for line in world[:-1])


def _noise(size: int) -> str:
    """`size` bytes of lines that deflate hardly at all."""
    return "".join(secrets.token_urlsafe(57) + "\n" for _ in range(size // 77 + 1))


def test_the_newest_run_is_cut_to_its_end_and_never_left_out_to_fit_the_cap(
    tmp_path: Path,
) -> None:
    """Codex T249 review: the run log is the only copy, so the size cap must not drop it.

    Older runs still go first, oldest first. The newest is cut shorter instead,
    its END kept, which is where a failed install's server lines are.
    """
    config = tmp_path / "config"
    runs = runlog.runs_dir(config)
    runs.mkdir(parents=True)
    older = []
    for n in range(3):
        path = runs / f"install-wow-tortoise-2026100{n}T101010Z.log"
        path.write_text(_noise(150_000), encoding="utf-8")
        os.utime(path, (1_000_000 + n, 1_000_000 + n))
        older.append(f"runs/{path.name}")
    newest = runs / "install-wow-tortoise-20261004T165226Z.log"
    newest.write_text(_noise(600_000) + f"{WORLD} | {CRASH}\n", encoding="utf-8")
    os.utime(newest, (2_000_000, 2_000_000))
    dest = tmp_path / "support.zip"
    cap = 300_000

    report = bundle.build(
        dest,
        Sources(config_dir=config, app_log=None, installs=()),
        Redactor.build([]),
        seams=bundle.Seams(
            live_logs=lambda install, silent: [],
            docker_version=lambda distro: None,
            now=lambda: datetime(2026, 10, 4, 16, 52, tzinfo=UTC),
        ),
        cap_bytes=cap,
    )

    assert os.path.getsize(dest) <= cap
    assert report.dropped == tuple(older)
    assert f"runs/{newest.name}" in report.cut
    with zipfile.ZipFile(dest) as archive:
        kept = archive.read(f"runs/{newest.name}").decode("utf-8")
    assert kept.startswith("[earlier lines dropped")
    assert kept.endswith(f"{WORLD} | {CRASH}\n")


@pytest.mark.parametrize("order", ["newest-written-first", "newest-written-last"])
def test_runs_with_one_modification_time_keep_the_one_named_newest(
    tmp_path: Path, order: str
) -> None:
    """Codex T249 adversarial review: a tie on mtime left the choice to listing order.

    The names carry the UTC start and a same-second suffix, so they break the tie.
    """
    config = tmp_path / "config"
    runs = runlog.runs_dir(config)
    runs.mkdir(parents=True)
    names = [
        "install-wow-tortoise-20261004T164000Z.log",
        "install-wow-tortoise-20261004T165226Z.log",
        "install-wow-tortoise-20261004T165226Z-2.log",
    ]
    for name in names if order == "newest-written-last" else reversed(names):
        path = runs / name
        path.write_text(_noise(150_000) + f"{name}\n", encoding="utf-8")
        os.utime(path, (2_000_000, 2_000_000))
    dest = tmp_path / "support.zip"

    report = bundle.build(
        dest,
        Sources(config_dir=config, app_log=None, installs=()),
        Redactor.build([]),
        seams=bundle.Seams(
            live_logs=lambda install, silent: [],
            docker_version=lambda distro: None,
            now=lambda: datetime(2026, 10, 4, 16, 52, tzinfo=UTC),
        ),
        cap_bytes=150_000,
    )

    assert f"runs/{names[-1]}" in report.included
    assert set(report.dropped) == {f"runs/{name}" for name in names[:-1]}


def test_the_newest_run_is_cut_only_after_the_app_log(tmp_path: Path) -> None:
    """Last of all: Yu'lon's own log gives way first, and here that alone fits the cap."""
    config = tmp_path / "config"
    runs = runlog.runs_dir(config)
    runs.mkdir(parents=True)
    run = runs / "install-wow-tortoise-20261004T165226Z.log"
    run.write_text(_noise(300_000) + f"{WORLD} | {CRASH}\n", encoding="utf-8")
    app_log = config / "yulon.log"
    app_log.write_text(_noise(600_000), encoding="utf-8")
    dest = tmp_path / "support.zip"
    cap = 400_000

    report = bundle.build(
        dest,
        Sources(config_dir=config, app_log=app_log, installs=()),
        Redactor.build([]),
        seams=bundle.Seams(
            live_logs=lambda install, silent: [],
            docker_version=lambda distro: None,
            now=lambda: datetime(2026, 10, 4, 16, 52, tzinfo=UTC),
        ),
        cap_bytes=cap,
    )

    assert os.path.getsize(dest) <= cap
    assert report.dropped == ()
    assert "app/yulon.log" in report.cut
    assert f"runs/{run.name}" not in report.cut


# -- the real read, against the fake docker CLI --------------------------------


def test_the_default_tail_asks_docker_for_the_last_lines_of_that_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    set_fake_log(state, WORLD, stdout=WORLD_LOG)

    text = native.Seams().container_tail(WORLD)

    assert text == WORLD_LOG
    assert calls(state) == [f"logs --tail {native.FAILURE_TAIL_LINES} {WORLD}"]


def test_what_a_container_wrote_to_stderr_is_kept_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container without a tty (Tortoise's realmd) writes its errors to stderr.

    `docker logs` hands the container's stderr back on its OWN stderr, which
    `docker.log_tail()` drops.
    """
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    set_fake_log(state, AUTH, stdout=AUTH_LOG, stderr="Could not connect to MySQL database\n")

    text = docker.last_lines(AUTH, 50)

    assert text is not None
    assert AUTH_LOG.strip() in text
    assert "Could not connect to MySQL database" in text


def test_a_long_stderr_never_pushes_the_end_of_stdout_out_of_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex T249 review: the streams come back apart and stderr follows stdout.

    Cut together to the cap's end, a stderr of 256 KiB or more left nothing of
    stdout, wherever the crash reason was. Each stream keeps its own end.
    """
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    noisy = "".join(f"warning {n:04d} {'w' * 700}\n" for n in range(native.FAILURE_TAIL_LINES))
    set_fake_log(state, WORLD, stdout=WORLD_LOG, stderr=noisy)

    text = native.Seams().container_tail(WORLD)

    assert text is not None
    assert CRASH in text, "the end of stdout"
    assert text.endswith(f"warning {native.FAILURE_TAIL_LINES - 1:04d} {'w' * 700}\n")
    assert len(text.encode("utf-8")) <= native.FAILURE_TAIL_BYTES


def test_a_last_line_longer_than_the_cap_keeps_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex T249 adversarial review: a stream with no newline in its kept bytes was emptied."""
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    huge = "{" + "x" * 300_000 + "} " + CRASH + "\n"
    set_fake_log(state, WORLD, stdout=huge, stderr="one warning\n")

    text = native.Seams().container_tail(WORLD)

    assert text is not None
    assert CRASH in text
    assert text.endswith("one warning\n")
    assert len(text.encode("utf-8")) <= native.FAILURE_TAIL_BYTES


def test_a_container_docker_does_not_know_is_no_lines_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`None`, never the daemon's refusal passed off as the container's log."""
    cli, _state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))

    assert docker.last_lines("nobody-here", 50) is None
