"""Tests for `yulon.install_wiring` — the one place the app wires an install engine.

`main.py` and `ui/controller_view.py` each hand-wrote the same `acore_*` probe
pair and the same fixed-password fallback; the CLI harness in
`catalog/installer.py` wired a third copy. Now there is one function for each,
and these tests pin them by the seams they call, not by grepping source.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import threading
import traceback
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from yulon import docker, install_wiring, platform
from yulon import log as log_module
from yulon.apply import DockerSql
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import InstallEngine, InstallerError, InstallOptions
from yulon.controller_wow_wotlk import repair as wotlk_repair
from yulon.controller_wow_wotlk.maintenance import DockerMysql

WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture(autouse=True)
def _the_log_this_module_opens_goes_to_a_scratch_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """No test here may write the real `yulon.log`, nor leave its handler behind.

    `main()` has called `configure(config_dir=platform.config_dir())` since
    2026-09-05, so every test that drives the harness would otherwise open
    `~/.local/share/yulon/yulon.log` on the box running the suite and leave a
    `RotatingFileHandler` on the ROOT logger for the 2500 tests that follow it.

    Deliberately NOT `log._reset_for_tests()`, which is what `test_log.py`
    uses: that removes every `StreamHandler` on the root logger, and pytest's
    own `caplog` handler is a `StreamHandler` subclass installed around the
    whole item -- `test_the_wiring_never_renders_the_password_it_carries`
    reads it. So this snapshots and restores instead, LEVELS as well as
    membership, because `configure(stderr_level=...)` changes a handler that
    already exists rather than adding one.

    It also has to CLEAR `_file_configured` on the way in, not only restore it
    on the way out, because this module is not the first thing to run in its
    process. `test_spine.py` drives the real `install_wiring.main()` at four
    sites, so in a worker that took that file first, file logging was already
    marked done and these tests got no log at all. Measured on m910q
    2026-09-05: green serially (i sorts before s) and two failures under
    `-n auto --dist loadfile`, on gw3 -- the shape of a test that passes
    because of what ran before it.
    """
    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path / "config-dir")
    root = logging.getLogger()
    levels = {handler: handler.level for handler in root.handlers}
    displaced = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
    for handler in displaced:
        root.removeHandler(handler)  # not closed: it belongs to whoever opened it
    flags = (
        log_module._stderr_configured,
        log_module._file_configured,
        log_module._file_problem,
        log_module._stderr_handler,
    )
    log_module._file_configured = False
    log_module._file_problem = None
    yield
    for handler in list(root.handlers):
        if handler in levels:
            handler.setLevel(levels[handler])
        else:
            root.removeHandler(handler)
            handler.close()
    for handler in displaced:
        root.addHandler(handler)
    (
        log_module._stderr_configured,
        log_module._file_configured,
        log_module._file_problem,
        log_module._stderr_handler,
    ) = flags


def _without_import_service(entry: CatalogEntry) -> CatalogEntry:
    return entry.model_copy(
        update={"containers": entry.containers.model_copy(update={"db_import": None})}
    )


def test_the_fixed_password_is_the_entry_value_or_the_acore_default() -> None:
    assert install_wiring.DEFAULT_DB_ROOT_PASSWORD == "password"
    assert install_wiring.fixed_db_password(WOTLK) == WOTLK.install.password.value
    blank = WOTLK.model_copy(
        update={
            "install": WOTLK.install.model_copy(
                update={"password": WOTLK.install.password.model_copy(update={"value": None})}
            )
        }
    )
    assert install_wiring.fixed_db_password(blank) == "password"


def test_the_import_gate_exists_only_for_a_one_shot_import_service() -> None:
    """`repair.import_state()` names the `acore_*` schemas; a game without them gets no probe."""
    probe, reset = install_wiring.import_gate_for(WOTLK)
    assert probe is not None and reset is not None
    assert install_wiring.import_gate_for(_without_import_service(WOTLK)) == (None, None)


def test_the_probe_reaches_repair_through_this_entry_s_db_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_import_state(sql: DockerSql, mysql: DockerMysql) -> docker.ImportState:
        seen.append((sql.db_container, mysql.db_container))
        assert sql.root_password == install_wiring.fixed_db_password(WOTLK)
        return docker.ImportState("imported", "every acore_* schema has tables", complete=True)

    def fake_reset(sql: DockerSql, mysql: DockerMysql) -> tuple[str, ...]:
        seen.append((sql.db_container, mysql.db_container))
        return ("acore_world",)

    monkeypatch.setattr(wotlk_repair, "import_state", fake_import_state)
    monkeypatch.setattr(wotlk_repair, "reset_unfinished", fake_reset)
    probe, reset = install_wiring.import_gate_for(WOTLK)
    assert probe is not None and reset is not None
    state = probe()
    assert state.state == "imported" and state.complete is True
    assert reset() == ("acore_world",)
    db = WOTLK.container_spec().db
    assert seen == [(db, db), (db, db)]


def test_the_probe_seams_carry_this_entry_s_schemas_and_the_distro_they_live_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two facts the pre-merge snippet dropped: WHICH schemas, and which daemon.

    `schemas=` is what keeps a CMaNGOS install off AzerothCore's `acore_*`
    names; `wsl_distro=` is what keeps a WSL-resident server's `docker exec`
    from going to the Windows-local daemon, which has never heard of
    `ac-database`. Both were passed by every call site this module replaces.
    """
    captured: list[tuple[DockerSql, DockerMysql]] = []

    def fake_import_state(sql: DockerSql, mysql: DockerMysql) -> docker.ImportState:
        captured.append((sql, mysql))
        return docker.ImportState("imported", "", complete=True)

    monkeypatch.setattr(wotlk_repair, "import_state", fake_import_state)
    probe, _reset = install_wiring.import_gate_for(WOTLK, wsl_distro="Ubuntu-24.04")
    assert probe is not None
    probe()
    sql, mysql = captured[-1]
    assert sql.schemas == WOTLK.schema_map()
    assert sql.wsl_distro == "Ubuntu-24.04"
    assert mysql.wsl_distro == "Ubuntu-24.04"

    captured.clear()
    probe, _reset = install_wiring.import_gate_for(WOTLK)
    assert probe is not None
    probe()
    sql, mysql = captured[-1]
    assert sql.wsl_distro is None and mysql.wsl_distro is None


def test_installer_for_app_hands_the_gate_to_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs_seen: list[dict[str, object]] = []

    class _Engine:
        def preflight(
            self, options: InstallOptions, cancel: object = None, *, ask: object = None
        ) -> None:
            return None

        def run(
            self,
            options: InstallOptions | None = None,
            *,
            cancel: object = None,
            ask: object = None,
        ) -> Iterator[str]:
            yield "ok"

    def fake_installer_for(entry: CatalogEntry, **kwargs: object) -> _Engine:
        kwargs_seen.append(kwargs)
        return _Engine()

    monkeypatch.setattr(install_wiring, "installer_for", fake_installer_for)

    install_wiring.installer_for_app(WOTLK, platform_id=lambda: "linux", installers_root=tmp_path)
    assert kwargs_seen[-1]["installers_root"] == tmp_path
    assert kwargs_seen[-1]["import_probe"] is not None
    assert kwargs_seen[-1]["reset_unfinished"] is not None

    install_wiring.installer_for_app(_without_import_service(WOTLK), platform_id=lambda: "linux")
    assert kwargs_seen[-1]["import_probe"] is None
    assert kwargs_seen[-1]["reset_unfinished"] is None


def test_main_streams_the_engine_and_maps_failures_to_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    options_seen: list[InstallOptions] = []

    class _Engine:
        def __init__(self, fail: bool) -> None:
            self.fail = fail

        def preflight(
            self, options: InstallOptions, cancel: object = None, *, ask: object = None
        ) -> None:
            return None

        def run(
            self,
            options: InstallOptions | None = None,
            *,
            cancel: object = None,
            ask: object = None,
        ) -> Iterator[str]:
            assert options is not None
            options_seen.append(options)
            yield "cloning"
            if self.fail:
                raise InstallerError("build failed")
            yield "ready"

    monkeypatch.setattr(
        install_wiring, "installer_for_app", lambda entry, **_k: _Engine(fail=False)
    )
    assert install_wiring.main(["wow-wotlk", "--server-dir", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == ["cloning", "ready"]
    assert options_seen[-1].server_dir == tmp_path and options_seen[-1].client_dir is None

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **_k: _Engine(fail=True))
    assert install_wiring.main(["wow-wotlk"]) == 1
    assert "install failed: build failed" in capsys.readouterr().err

    assert install_wiring.main(["not-a-game"]) == 2
    assert "unknown game 'not-a-game'" in capsys.readouterr().err


def test_main_reports_an_engine_that_cannot_be_built_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A game with no engine here (7.2: `installer_for` raises) is exit 1, not a traceback."""

    def refuse(entry: CatalogEntry, **_k: object) -> object:
        raise InstallerError("wow-tbc has no native install plan yet")

    monkeypatch.setattr(install_wiring, "installer_for_app", refuse)
    assert install_wiring.main(["wow-tbc"]) == 1
    assert "install failed: wow-tbc has no native install plan yet" in capsys.readouterr().err


def test_main_hands_the_engine_a_prompter_so_a_sudo_box_is_not_a_hang(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`run(ask=…)` is not optional: without it a password-sudo box parks forever.

    `runner.interact()` writes nothing for a missing answer, so the harness that
    passed no `ask` at all sat at sudo's prompt with no timeout and no error
    (yulon-arch, 2026-08-28). The engine gets a terminal prompter, and it is
    THIS module's, because the harness moved here.
    """
    asks: list[object] = []

    class _Engine:
        def preflight(
            self, options: InstallOptions, cancel: object = None, *, ask: object = None
        ) -> None:
            return None

        def run(
            self,
            options: InstallOptions | None = None,
            *,
            cancel: object = None,
            ask: object = None,
        ) -> Iterator[str]:
            asks.append(ask)
            yield "done"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **_k: _Engine())
    assert install_wiring.main(["wow-wotlk", "--server-dir", str(tmp_path)]) == 0
    assert asks[-1] is install_wiring._terminal_prompter


def test_main_hands_the_engine_a_cancel_event_of_its_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`run(cancel=…)` is not optional either: it is the only seam that stops a worker.

    bug-checklist §21's closing test abandons the bridge WITHOUT setting the
    cancel event and demands that no worker survive. `stop_abandoned_worker()`
    honours that by setting the event it was handed — and a bridge handed
    `None` has nothing to set, logs "left running", and returns. Until
    2026-09-04 this harness passed no event at all (`main()` had zero
    occurrences of `cancel`), so the one install path every gate box runs was
    exactly the case §21's test could not pass. The Catalog tab creates an
    `Event` per install (`catalog_view.py`); this is the harness doing the same.
    """
    cancels: list[object] = []

    class _Engine:
        def preflight(
            self, options: InstallOptions, cancel: object = None, *, ask: object = None
        ) -> None:
            return None

        def run(
            self,
            options: InstallOptions | None = None,
            *,
            cancel: object = None,
            ask: object = None,
        ) -> Iterator[str]:
            cancels.append(cancel)
            yield "done"

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **_k: _Engine())
    assert install_wiring.main(["wow-wotlk", "--server-dir", str(tmp_path)]) == 0
    assert isinstance(cancels[-1], threading.Event), cancels[-1]


def test_main_turns_a_ctrl_c_in_the_bridge_into_a_sentence_and_exit_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Ctrl+C is raised INSIDE the engine's generator, and the harness owes a sentence for it.

    The harness's thread spends an install blocked in the bridge's
    `lines.get()`, so that is where the `KeyboardInterrupt` lands: inside
    `engine.run()`'s frame, from where it propagates out of `for line in
    engine.run(...)`. The engine's own hook has stopped the worker by then
    (`test_spine.py`, `test_families_cmangos.py`); what is left is what the
    person at the terminal sees. Written with `pytest.fail` rather than
    letting the interrupt escape, because an escaping `KeyboardInterrupt`
    does not fail one test — it stops the whole session.

    The event is asserted SET on the way out, and this fake engine is the case
    where `main()` is the ONLY thing that could have set it: its `run()` is a
    plain generator with no hook of its own, so the assertion below is about
    `main()`'s own `cancel.set()` and nothing else. That is the whole reason
    the fake is shaped this way — with a real engine the same assertion would
    pass for two reasons and distinguish neither.

    The docstring here used to justify that line by saying an interrupt landing
    in `main()`'s own `write` "reaches no hook". Measured false on m910q
    2026-09-05, through the real `main()`:
    `test_a_ctrl_c_in_the_harness_own_write_still_reaches_the_engines_hook`
    below records what actually happens.
    """
    cancels: list[object] = []

    class _Engine:
        def preflight(
            self, options: InstallOptions, cancel: object = None, *, ask: object = None
        ) -> None:
            return None

        def run(
            self,
            options: InstallOptions | None = None,
            *,
            cancel: object = None,
            ask: object = None,
        ) -> Iterator[str]:
            cancels.append(cancel)
            yield "cloning"
            raise KeyboardInterrupt

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **_k: _Engine())
    try:
        code = install_wiring.main(["wow-wotlk", "--server-dir", str(tmp_path)])
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt escaped main(): a traceback where a sentence was owed")
    assert code == 130
    out = capsys.readouterr()
    assert out.out.splitlines() == ["cloning"]
    assert "install stopped" in out.err
    assert isinstance(cancels[-1], threading.Event) and cancels[-1].is_set()


class _InterruptingStdout:
    """A stdout whose `write` raises Ctrl+C: the OTHER place an interrupt can land."""

    def __init__(self) -> None:
        self.wrote: list[str] = []

    def write(self, text: str) -> int:
        self.wrote.append(text)
        raise KeyboardInterrupt

    def flush(self) -> None:
        return None


class _SnapshottingStderr:
    """A stderr that records what `watch` held at the moment `main()` wrote to it.

    `main()`'s only write to stderr on this path is inside the
    `except KeyboardInterrupt` handler, so this is a probe of HANDLER TIME. It
    has to be: a generator closed a moment later — when `main()`'s frame is
    destroyed on return — leaves the same list behind, so a check made after
    the call cannot tell the two apart.
    """

    def __init__(self, watch: list[str]) -> None:
        self._watch = watch
        self.at_handler_time: list[list[str]] = []
        self.wrote: list[str] = []

    def write(self, text: str) -> int:
        self.at_handler_time.append(list(self._watch))
        self.wrote.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_a_ctrl_c_in_the_harness_own_write_still_reaches_the_engines_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An interrupt in `main()`'s own `write` closes the engine's generator too.

    `install_wiring.py`'s handler used to be justified by a comment saying this
    case "reaches no hook", so `main()` had to set the cancel event itself.
    **Measured false on m910q (CPython 3.11.15) 2026-09-05, through the real
    `main()`: the hook fires, as a `GeneratorExit`, and it fires BEFORE the
    handler body runs.** The reason is refcounting — the `for` loop's iterator
    is the only reference to `engine.run(...)`, unwinding to the handler pops
    it, and CPython closes the generator on the spot.

    That is worth a test rather than a corrected sentence, because it is a
    property of how `main()` is WRITTEN and one edit away from being false. The
    mutation that says so, measured the same day: binding the generator to a
    name first (`gen = engine.run(...)` and `for line in gen:`) keeps it alive
    until `main()` returns and leaves this test at

        AssertionError: assert [[]] == [['GeneratorExit']]

    — the hook had not run when the handler did, though it had by the time the
    call returned, which is why the observation is taken through stderr rather
    than afterwards. `main()` still calls `cancel.set()`; this test does not
    stop that being deleted, it stops the CLAIM beside it being wrong again.
    """
    hooked: list[str] = []

    class _Engine:
        def preflight(
            self, options: InstallOptions, cancel: object = None, *, ask: object = None
        ) -> None:
            return None

        def run(
            self,
            options: InstallOptions | None = None,
            *,
            cancel: object = None,
            ask: object = None,
        ) -> Iterator[str]:
            # Where `_pump()` calls `stop_abandoned_worker()`; recording the
            # exception type is the same observable one layer up.
            try:
                yield "cloning"
            except BaseException as exc:
                hooked.append(type(exc).__name__)
                raise

    stdout = _InterruptingStdout()
    stderr = _SnapshottingStderr(hooked)
    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **_k: _Engine())
    monkeypatch.setattr(install_wiring.sys, "stdout", stdout)
    monkeypatch.setattr(install_wiring.sys, "stderr", stderr)
    try:
        code = install_wiring.main(["wow-wotlk", "--server-dir", str(tmp_path)])
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt escaped main(): a traceback where a sentence was owed")
    assert code == 130
    assert stdout.wrote == ["cloning\n"], stdout.wrote
    assert stderr.wrote == ["install stopped\n"], stderr.wrote
    assert stderr.at_handler_time == [["GeneratorExit"]], stderr.at_handler_time


class _Stdin:
    """Just enough stdin for `_terminal_prompter`'s one question about it."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_the_terminal_prompter_hides_a_password_and_shows_a_consent_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native path's sudo question is `SUDO_PASSWORD_QUESTION`, not the script's marker.

    The harness this replaces only recognised `installer.SUDO_PROMPT_PREFIX` —
    the bash `Installer`'s per-run marker — so on the native path the sudo
    question fell through to `input()` and the root password was echoed to the
    terminal. Both spellings are secret; a consent question is not.
    """
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=True))
    typed: list[str] = []

    def fake_getpass(prompt: str) -> str:
        typed.append(f"hidden:{prompt}")
        return "s3cret"

    def fake_input(prompt: str) -> str:
        typed.append(f"echoed:{prompt}")
        return "yes"

    monkeypatch.setattr(install_wiring.getpass, "getpass", fake_getpass)
    monkeypatch.setattr("builtins.input", fake_input)

    assert install_wiring._terminal_prompter(platform.SUDO_PASSWORD_QUESTION) == "s3cret"
    assert typed[-1].startswith("hidden:")
    assert install_wiring._terminal_prompter("[sudo via Yu'lon abc123] password:") == "s3cret"
    assert typed[-1].startswith("hidden:")
    assert install_wiring._terminal_prompter("Add 'dad' to the docker group? (y/n)") == "yes"
    assert typed[-1].startswith("echoed:")


def test_the_terminal_prompter_declines_when_there_is_no_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty answer ends; a missing one hangs. Off a tty this must never block."""
    monkeypatch.setattr(sys, "stdin", _Stdin(tty=False))
    assert install_wiring._terminal_prompter(platform.SUDO_PASSWORD_QUESTION) == ""
    assert "declining" in capsys.readouterr().err


def test_main_has_no_reinstall_flag() -> None:
    """The harness is `<game> [--server-dir] [--client-dir]`; 7.2 drops `reinstall`."""
    with pytest.raises(SystemExit) as caught:
        install_wiring.main(["wow-wotlk", "--reinstall"])
    assert caught.value.code == 2


def test_python_dash_m_reaches_main() -> None:
    """`python -m yulon.install_wiring --help` is the harness spelling the docs give."""
    proc = subprocess.run(
        [sys.executable, "-m", "yulon.install_wiring", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--server-dir" in proc.stdout and "--client-dir" in proc.stdout
    assert "--reinstall" not in proc.stdout


def test_the_old_harness_is_gone_from_the_catalog_package() -> None:
    """`catalog/` may not import a controller package, so it can wire no probe — nor the CLI."""
    from yulon.catalog import installer

    assert not hasattr(installer, "_main")
    assert not hasattr(installer, "_terminal_prompter")


def test_the_catalog_package_never_imports_a_controller_at_any_depth() -> None:
    """The reason the harness moved: `installer.py` had a lazy controller import inside `_main`."""
    import ast

    from yulon.catalog import installer

    source = Path(installer.__file__).read_text(encoding="utf-8")
    named: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            named.add(node.module)
    assert not [name for name in named if "controller_wow" in name], sorted(named)


def test_modules_re_exports_the_default_password() -> None:
    from yulon.controller_wow_wotlk import modules

    assert modules.DEFAULT_DB_ROOT_PASSWORD is install_wiring.DEFAULT_DB_ROOT_PASSWORD


def test_the_wiring_never_renders_the_password_it_carries(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A canary through every channel that has leaked a secret in this repo before.

    `repr`, `str`, an f-string, `%s`/`%r`, `pprint`, `vars`, a DEBUG log line,
    an exception message, `.args` and a formatted traceback. The gate's two
    callables are what leave this module, so they are what is swept.
    """
    import pprint

    canary = "CANARY-e7f1a9c4"
    entry = WOTLK.model_copy(
        update={
            "install": WOTLK.install.model_copy(
                update={"password": WOTLK.install.password.model_copy(update={"value": canary})}
            )
        }
    )
    probe, reset = install_wiring.import_gate_for(entry)
    assert probe is not None and reset is not None

    rendered = [
        repr(probe),
        str(probe),
        f"{probe}",
        "%s %r" % (probe, reset),  # noqa: UP031 - the old spelling is part of the sweep
        pprint.pformat(vars(install_wiring)),
        repr(vars(probe)),
        repr(probe.__closure__),
    ]

    def boom(sql: object, mysql: object) -> docker.ImportState:
        raise InstallerError("the import probe could not reach the database")

    monkeypatch.setattr(wotlk_repair, "import_state", boom)
    with caplog.at_level("DEBUG"):
        try:
            probe()
        except InstallerError as exc:
            rendered.append(str(exc))
            rendered.append(repr(exc.args))
            rendered.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    rendered.append(caplog.text)
    for text in rendered:
        assert canary not in text, text


def test_main_exits_1_when_no_engine_can_be_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A1: `installer_for()` raises for `native: None` since 7.2; the harness must not traceback.

    The refusal is raised by the FACTORY, not by `run()`, so it is only caught
    if building the engine happens inside the `try` — which is the whole edit
    A1 asks for and the one a reader of `main()` cannot see is load-bearing.
    The double refuses on the same terms the real factory does; what is under
    test is where the call sits, not the sentence it raises.

    Both streams are asserted: the sentence is what a person reads, and the
    exit code is what a gate script branches on.
    """

    def refuse(entry: CatalogEntry, **_k: object) -> InstallEngine:
        raise InstallerError(
            f"{entry.name} cannot be installed yet: its catalog entry has no `install.native` "
            "section. Nothing was started."
        )

    monkeypatch.setattr(install_wiring, "installer_for_app", refuse)
    assert install_wiring.main(["wow-wotlk"]) == 1
    captured = capsys.readouterr()
    assert "install failed: WoW WotLK cannot be installed yet" in captured.err
    assert "no `install.native` section" in captured.err
    assert captured.out == "", "nothing was installed, so nothing may be streamed"


def test_main_still_tells_an_unknown_game_apart_from_a_refused_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 2 and exit 1 are different answers, and a gate script branches on which.

    Moving the factory inside the `try` (A1) puts a second `return` in front of
    the unknown-game path's; this pins that it still gets its own code and its
    own sentence.
    """
    assert install_wiring.main(["wow-nonesuch"]) == 2
    captured = capsys.readouterr()
    assert "unknown game 'wow-nonesuch'" in captured.err
    assert "install failed" not in captured.err


def test_the_harness_makes_its_streams_utf8_before_it_prints_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows hands a redirected stream cp1252, and this harness prints arrows.

    Measured on `yulon-win11` 2026-09-03: `python -m yulon.install_wiring
    wow-wotlk` reached the end of preflight and died with `'charmap' codec can't
    encode character '→' in position 210`. `main.py` had carried this fix
    since the provisioning work; the CLI harness every gate runs through never
    had it, so no Windows gate could reach a single stage.

    Asserts the call happens BEFORE argument parsing, by driving the path that
    exits earliest -- an unknown game, which returns 2 without touching a
    catalog entry. A reconfigure that ran later would still leave the first
    printed line to crash.
    """
    calls: list[dict[str, object]] = []

    class _Stream:
        def reconfigure(self, **kw: object) -> None:
            calls.append(kw)

        def write(self, _text: str) -> int:
            return 0

        def flush(self) -> None:
            return None

    monkeypatch.setattr(sys, "stdout", _Stream())
    monkeypatch.setattr(sys, "stderr", _Stream())

    assert install_wiring.main(["not-a-game"]) == 2
    assert calls, "the harness printed without making its streams encodable"
    assert all(c.get("encoding") == "utf-8" for c in calls), calls
    assert all(
        c.get("errors") == "replace" for c in calls
    ), "a diagnostic that raises kills the thing it is diagnosing"


# --- The log a headless install leaves behind (bug-checklist section 42) -----
#
# Filed 2026-09-05: `grep -c 'configure(config_dir' main.py` answered 1 and
# `grep -c 'configure(' yulon/install_wiring.py` answered 0, and
# `~/.local/share/yulon/` did not exist on `yulon-ubuntu` after the whole 7.2
# gate had run through this harness. The GUI, whose stderr goes nowhere, kept a
# file; the CLI, whose stderr is a terminal the user closes, kept nothing.
#
# The tests below are written against the DRIFT rather than against the missing
# call: one entry point logging and the other not is the defect, and a test that
# only pinned `install_wiring` would let the next entry point repeat it.

_DRIVERS = {
    # Both drivers exit early on purpose -- after logging is configured, before
    # anything is installed or provisioned for real.
    "yulon.install_wiring": """\
import sys
sys.argv = ["yulon.install_wiring", "wow-nonesuch"]
from yulon import install_wiring
raise SystemExit(install_wiring.main())
""",
    "main": """\
import sys
sys.argv = ["yulon", "--provision"]
import main
from yulon import platform
main._regain_docker_group = lambda: None
main.platform.ensure_docker = lambda **kwargs: platform.ProvisionReport(platform="linux")
main.platform.docker_program = lambda: None
raise SystemExit(main.main())
""",
}

_NOT_A_USER_ENTRY_POINT = {
    "yulon.manifest": (
        "a developer tool -- `python -m yulon.manifest --dump-schema` regenerates the "
        "checked-in JSON Schema and never runs anything for a user, so there is no "
        "session for it to leave a record of"
    ),
}


def _modules_with_a_main_block() -> set[str]:
    """Every module in the app that can be run as a program, found by parsing, not by list.

    A hand-kept list is exactly what let the two known entry points drift:
    `main.py` grew file logging and nothing said the harness had to keep up.
    """
    root = Path(__file__).resolve().parents[1]
    found: set[str] = set()
    for path in [root / "main.py", *sorted((root / "yulon").rglob("*.py"))]:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
                continue
            left = node.test.left
            if not (isinstance(left, ast.Name) and left.id == "__name__"):
                continue
            if any(
                isinstance(other, ast.Constant) and other.value == "__main__"
                for other in node.test.comparators
            ):
                found.add(".".join(path.relative_to(root).with_suffix("").parts))
    return found


def test_every_way_to_run_this_app_is_classified_as_logging_or_not() -> None:
    """A new entry point fails here until someone says which kind it is.

    `yulon/__main__.py` is absent on purpose and not an omission: it has no
    `if __name__` block because it is a redirect -- it imports `main.main` and
    raises `SystemExit(main())`, so `python -m yulon` IS the `main` row below.
    """
    assert _modules_with_a_main_block() == set(_DRIVERS) | set(_NOT_A_USER_ENTRY_POINT)


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="config_dir() has no environment override on macOS, so the log cannot be redirected",
)
def test_every_entry_point_that_runs_for_a_user_leaves_the_same_log_behind(
    tmp_path: Path,
) -> None:
    """Both entry points, one assertion: whichever writes no `yulon.log` names itself.

    Run as real child processes because that is the only honest reproduction --
    the call under test is the first thing each `main()` does, before this
    suite's own logging state would be anywhere near it.
    """
    root = Path(__file__).resolve().parents[1]
    wrote: dict[str, bool] = {}
    for name, driver in sorted(_DRIVERS.items()):
        data_home = tmp_path / name
        data_home.mkdir(parents=True)
        env = dict(os.environ)
        env.update(
            {
                "XDG_DATA_HOME": str(data_home),  # Linux
                "APPDATA": str(data_home),  # Windows: the same question, its own variable
                "PYTHONPATH": str(root),
            }
        )
        env.pop("YULON_PROVISION", None)
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Traceback" not in proc.stderr, f"{name} died: {proc.stderr}"
        log = data_home / "yulon" / "yulon.log"
        wrote[name] = log.is_file() and log.read_text(encoding="utf-8").strip() != ""
    assert all(wrote.values()), (
        "entry points disagree about keeping a log: "
        f"{sorted(n for n, w in wrote.items() if w)} wrote one, "
        f"{sorted(n for n, w in wrote.items() if not w)} wrote none"
    )


def test_the_harness_puts_the_stage_lines_it_streamed_into_the_log(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The point of the file: what a failed headless install did, after the terminal is gone.

    Also pins that streaming them does not double them on the terminal. A gate
    runs this harness as `> log 2>&1`, so an INFO record reaching the stderr
    handler would print every line of a 30-minute install twice; `main()` asks
    `configure()` for a stderr handler that starts at WARNING, and
    `test_log.py` is where that level is proven to mean what it says.
    """
    stages = ["preflight ok", "downloading -> /tmp/x", "import finished"]

    class _Engine:
        def run(self, _options: InstallOptions, **_kwargs: object) -> Iterator[str]:
            yield from stages

    monkeypatch.setattr(install_wiring, "installer_for_app", lambda entry, **_k: _Engine())
    assert install_wiring.main(["wow-wotlk", "--server-dir", str(tmp_path / "server")]) == 0

    written = (platform.config_dir() / "yulon.log").read_text(encoding="utf-8")
    for stage in stages:
        assert stage in written, f"{stage!r} was streamed but never recorded"
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if line] == stages
    assert log_module._stderr_handler is not None
    assert log_module._stderr_handler.level == logging.WARNING


def test_a_harness_run_that_could_not_open_its_log_says_so_rather_than_dying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing log is worth a sentence, and the CLI has a stream the GUI does not.

    `main.py` needs `_warn_about_the_log_file()` because a frozen `console=False`
    build has nowhere else to say it. The harness does not: `configure()` logs
    its own `file_log_problem()` at WARNING, which is exactly the level its
    stderr handler keeps, so the sentence reaches the terminal without a second
    reporter -- and, either way, an unwritable config dir does not stop the
    install the way it once stopped the launcher.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, so no directory can be made under it", encoding="utf-8")
    monkeypatch.setattr(platform, "config_dir", lambda: blocker / "yulon")
    monkeypatch.setattr(log_module.tempfile, "gettempdir", lambda: str(blocker))

    assert install_wiring.main(["wow-nonesuch"]) == 2
    problem = log_module.file_log_problem()
    assert problem is not None and "could not write its log" in problem
