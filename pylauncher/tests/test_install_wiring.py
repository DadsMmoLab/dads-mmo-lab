"""Tests for `yulon.install_wiring` — the one place the app wires an install engine.

`main.py` and `ui/controller_view.py` each hand-wrote the same `acore_*` probe
pair and the same fixed-password fallback; the CLI harness in
`catalog/installer.py` wired a third copy. Now there is one function for each,
and these tests pin them by the seams they call, not by grepping source.
"""

from __future__ import annotations

import subprocess
import sys
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import docker, install_wiring, platform
from yulon.apply import DockerSql
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller_wow_wotlk import repair as wotlk_repair
from yulon.controller_wow_wotlk.maintenance import DockerMysql

WOTLK = load_catalog().get("wow-wotlk")


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
