"""T812 C5: the CoA world bootstrap stage (after C2).

The base data of `acore_world` is not an import of SQL updates but a versioned
baseline laid by `apps/coa-world/world_data.py` out of the core checkout, run by
the compose one-shot the entry's `world_bootstrap_service` names, between
`start-db` and `import`. The done-marker is the `version` table
(`world_data.py` loads `updates`, `updates_include` and `version` LAST), so a
world with tables but no `version` is an interrupted load and is dropped first,
and one WITH `version` exits 0 in seconds while the import that follows still
decides on its own probe.

The machine is `tests/support_native.Recorder`, and the RENDERED shell of the
service runs under bash with a fake `mysql` on PATH: nothing here is evidence
that a database loads, it is evidence about order, about what is never in
argv, and about which branch the shell takes for each database state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support_bash import bash_available
from tests.support_native import ENTRY, Recorder
from yulon import docker, resources
from yulon.catalog import build_context, composegen, native
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallOptions

BOOTSTRAP_SERVICE = "coa-world-bootstrap"

needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)


def coa_entry(**block: Any) -> CatalogEntry:
    """WotLK's entry with the CoA block carrying `block`, VALIDATED as catalog data is."""
    data = ENTRY.model_dump(mode="json", by_alias=True, exclude_none=True)
    data["id"] = "wow-scratch-ac"
    data["install"]["native"]["azerothcore"].update(block)
    return CatalogEntry.model_validate(data)


def coa() -> CatalogEntry:
    from yulon.catalog.catalog import load_catalog

    return load_catalog().get("wow-coa")


def make(entry: CatalogEntry, rec: Recorder) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def run(entry: CatalogEntry, rec: Recorder, server_dir: Path, **overrides: object) -> list[str]:
    """First press into an empty folder: prime the remote answers a real clone leaves."""
    rec.remotes[server_dir] = entry.emulator.sources[0].url
    for source in entry.emulator.sources[1:]:
        rec.remotes[server_dir / source.dest] = source.url
    made = (
        make(entry, rec)
        if not overrides
        else AzerothCoreInstaller(
            entry,
            installers_root=resources.installers_dir(),
            import_probe=rec.probe,
            reset_unfinished=rec.reset,
            seams=rec.seams(**overrides),
        )
    )
    return list(made.run(InstallOptions(server_dir=server_dir)))


def _rendered_bootstrap() -> dict:  # type: ignore[type-arg]
    plan = composegen.render(
        coa(),
        Path("/tmp/coa-bootstrap-probe"),
        templates_root=resources.installers_dir(),
        platform_id=lambda: "linux",
    )
    return yaml.safe_load(plan.base)["services"][BOOTSTRAP_SERVICE]


def _script(service: dict) -> str:  # type: ignore[type-arg]
    """The shell text the container runs, after compose's own `$$` -> `$` unescaping.

    Unlike the database's hold script (which lives in `entrypoint[2]` because
    setting `entrypoint:` clears the image's CMD), this service's body is its
    `command:` — one block scalar — under the stock `mysql:8.4` entrypoint.
    """
    assert service["entrypoint"] == ["/bin/bash", "-c"], service["entrypoint"]
    assert len(service["command"]) == 1, service["command"]
    return service["command"][0].replace("$$", "$")


def _run_script(tmp_path: Path, state: str) -> tuple[str, str, int]:
    """Run the rendered shell with a fake `mysql` answering database `state`.

    `state` is `loaded` (a `version` table), `interrupted` (tables, no
    `version`), or `empty` (no tables). Returns (stdout, stderr, exit code).
    The fake `microdnf`/`python3.11` record their call in a file and exit 0.
    """
    service = _rendered_bootstrap()
    binds = tmp_path / "bin"
    binds.mkdir()
    marker = tmp_path / "called.txt"
    if state == "loaded":
        version, tables = "1", "5"
    elif state == "interrupted":
        version, tables = "0", "5"
    else:
        version, tables = "0", "0"
    (binds / "mysql").write_text(
        "#!/usr/bin/env bash\n" "for last; do :; done\n"
        # The option file is parsed the way the real client parses it: a line
        # that is not `name=value` is an unknown option, which is exactly how
        # the shipped bootstrap died on `host coa-database` (live proof,
        # 2026-10-28). The file is the one the rendered `--defaults-file=`
        # names.
        'cnf=""\n'
        'for arg; do case "$arg" in --defaults-file=*) cnf="${arg#--defaults-file=}";; esac; done\n'
        'if [ -n "$cnf" ]; then\n'
        "  seen_client=0\n"
        "  while IFS= read -r line; do\n"
        '    case "$line" in\n'
        '      "") continue;;\n'
        '      "[client]") seen_client=1; continue;;\n'
        '      "["*) echo "mysql: [ERROR] unknown group $line" >&2; exit 2;;\n'
        "    esac\n"
        '    case "$line" in\n'
        "      *=*) ;;\n"
        "      *) echo \"mysql: [ERROR] unknown option '--$line'\" >&2; exit 2;;\n"
        "    esac\n"
        '  done < "$cnf"\n'
        '  [ "$seen_client" = 1 ] || { echo "mysql: [ERROR] no [client] group" >&2; exit 2; }\n'
        "fi\n"
        # The version probe's query CONTAINS the table-count query's substring,
        # so it is matched first — as the rendered shell asks it first.
        "if [[ \"$last\" == *\"TABLE_SCHEMA='acore_world' AND TABLE_NAME='version'\"* ]]; then\n"
        f'  echo "{version}"; exit 0\n'
        "fi\n"
        'if [[ "$last" == *"TABLE_SCHEMA=\'acore_world\'"* ]]; then\n'
        f'  echo "{tables}"; exit 0\n'
        "fi\n"
        f'echo "CREATE/DROP got: $last" >> "{marker}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (binds / "microdnf").write_text(
        "#!/usr/bin/env bash\n" f'echo "microdnf $*" >> "{marker}"\n' "exit 0\n",
        encoding="utf-8",
    )
    (binds / "python3.11").write_text(
        "#!/usr/bin/env bash\n" f'echo "world_data $*" >> "{marker}"\n' "exit 0\n",
        encoding="utf-8",
    )
    for name in ("mysql", "microdnf", "python3.11"):
        (binds / name).chmod(0o755)
    env = {
        "PATH": f"{binds}:/usr/bin:/bin",
        "DB_ROOT_PASSWORD": "coa-test-password",
    }
    proc = subprocess.run(
        ["bash", "-c", _script(service), "yulon-db"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    called = marker.read_text(encoding="utf-8") if marker.exists() else ""
    return proc.stdout, called, proc.returncode


def test_the_bootstrap_runs_between_start_db_and_import() -> None:
    names = make(coa_entry(world_bootstrap_service=BOOTSTRAP_SERVICE), Recorder()).stage_names()
    assert names.index("start-db") < names.index("world-bootstrap") < names.index("import")


def test_wotlk_runs_the_historical_tuple_with_no_bootstrap_stage() -> None:
    made = make(ENTRY, Recorder())
    assert "world-bootstrap" not in made.stage_names()
    assert made.stage_names() == AzerothCoreInstaller.STAGE_NAMES


def test_the_bootstrap_stage_is_not_recorded() -> None:
    made = make(coa_entry(world_bootstrap_service=BOOTSTRAP_SERVICE), Recorder())
    stage = next(s for s in made.stages() if s.name == "world-bootstrap")
    assert stage.recorded is False


def test_the_bootstrap_runs_the_named_service_after_ending_a_left_over(tmp_path: Path) -> None:
    rec = Recorder()
    rec.one_shots_running.append(BOOTSTRAP_SERVICE)
    said = run(coa_entry(world_bootstrap_service=BOOTSTRAP_SERVICE), rec, tmp_path / "server")
    assert BOOTSTRAP_SERVICE in rec.ended_one_shots
    assert f"one-shot:{BOOTSTRAP_SERVICE}" in rec.calls
    assert "The CoA world's base data is in place." in said


def test_a_left_over_bootstrap_that_cannot_be_ended_refuses(tmp_path: Path) -> None:
    from yulon.catalog.installer import OneShotLeftRunning

    rec = Recorder()
    rec.one_shots_running.append(BOOTSTRAP_SERVICE)
    rec.one_shot_left = docker.OneShotLeft(names=("c",), reason="busy")
    with pytest.raises(OneShotLeftRunning):
        run(coa_entry(world_bootstrap_service=BOOTSTRAP_SERVICE), rec, tmp_path / "server")


def test_the_password_is_never_in_argv_or_echoed() -> None:
    service = _rendered_bootstrap()
    text = yaml.safe_dump(service)
    assert "coa-test-password" not in text
    assert "t98-password-for-tests" not in text
    script = _script(service)
    # The password leaves through the option file only: its uses are the
    # `printf ... > /tmp/w.cnf` write and the `q()` reader. `echo` says only
    # the two status lines, neither naming the variable.
    users = [line for line in script.splitlines() if "DB_ROOT_PASSWORD" in line]
    assert len(users) == 1 and users[0].lstrip().startswith("printf"), users
    assert "--password" not in script
    assert "MYSQL_PWD" not in script


def test_the_mysql_option_file_is_name_equals_value() -> None:
    """The option file the bootstrap hands `mysql --defaults-file` is parsed as MySQL parses it.

    Written `user root` / `host coa-database` it is not an option file at all:
    the one-shot dies `mysql: [ERROR] unknown option '--host coa-database'`
    before a single table is loaded (live proof, yulon-ubuntu2 2026-10-28).
    """
    script = _script(_rendered_bootstrap())
    line = next(line for line in script.splitlines() if "printf '[client]" in line)
    proc = subprocess.run(
        ["bash", "-c", line.split(">")[0]],
        env={"PATH": "/usr/bin:/bin", "DB_ROOT_PASSWORD": "coa-test-password"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "[client]", lines
    assert lines[1] == "user=root", lines
    assert lines[2].startswith("host=") and lines[2] != "host=", lines
    assert lines[3] == "password=coa-test-password", lines
    assert all("=" in entry or entry.startswith("[") for entry in lines), lines


def test_the_option_file_is_what_the_one_shot_runs_with(tmp_path: Path) -> None:
    """The rendered shell leaves 0 with the strict client, not exit 2 at its first query."""
    out, _called, code = _run_script(tmp_path, "empty")
    assert code == 0, out


def test_the_bootstrap_mounts_the_migrations_the_baseline_hashes() -> None:
    """`coveredMigrations` names files under `/src/data/sql` and `/src/modules/mod-ascension`.

    Without the two mounts `world_data.py` stops before the first table with
    `[Errno 2] No such file or directory` (live proof, yulon-ubuntu2 2026-10-28).
    """
    volumes = _rendered_bootstrap()["volumes"]
    assert "./data/sql:/src/data/sql:ro" in volumes, volumes
    assert "./modules/mod-ascension:/src/modules/mod-ascension:ro" in volumes, volumes


def test_the_service_reads_the_core_checkout_at_src() -> None:
    service = _rendered_bootstrap()
    assert service["image"] == "mysql:8.4"
    assert service["entrypoint"] == ["/bin/bash", "-c"]
    volumes = service["volumes"]
    assert "./apps/coa-world:/src/apps/coa-world:ro" in volumes
    assert "./data/coa-world:/src/data/coa-world:ro" in volumes
    assert "world_data.py bootstrap" in service["command"][0]


def test_the_bootstrap_creates_the_world_with_the_core_charset() -> None:
    chunk = _script(_rendered_bootstrap())
    assert chunk.count("CREATE DATABASE IF NOT EXISTS acore_world") == 2
    assert "DEFAULT CHARACTER SET UTF8MB4 COLLATE utf8mb4_general_ci" in chunk


def test_a_world_with_version_is_skipped(tmp_path: Path) -> None:
    out, called, code = _run_script(tmp_path, "loaded")
    assert code == 0
    assert "yulon: the CoA world is already loaded" in out
    assert "DROP DATABASE" not in called
    assert "world_data" not in called


def test_a_world_without_version_is_dropped_first(tmp_path: Path) -> None:
    out, called, code = _run_script(tmp_path, "interrupted")
    assert code == 0
    assert "yulon: an interrupted CoA world load was found; loading it again" in out
    assert "DROP DATABASE acore_world" in called
    assert "world_data" in called


def test_an_empty_world_is_loaded_without_a_drop(tmp_path: Path) -> None:
    out, called, code = _run_script(tmp_path, "empty")
    assert code == 0
    assert "already loaded" not in out
    assert "loading it again" not in out
    assert "DROP DATABASE" not in called
    assert "world_data" in called


def test_a_failed_bootstrap_fails_the_install(tmp_path: Path) -> None:
    from yulon.catalog.installer import InstallerError

    rec = Recorder()
    calls = {"n": 0}
    ok = docker.AttachedRun(0, ("ran",))

    def one_shot(service: str, server_dir: Path, **kw: object) -> docker.AttachedRun:  # type: ignore[no-untyped-def]
        calls["n"] += 1
        rec.calls.append(f"one-shot:{service}")
        if callable(kw.get("sink")):
            kw["sink"](f"{service} said something")  # type: ignore[operator-call]
        if service == BOOTSTRAP_SERVICE:
            return docker.AttachedRun(1, ("world_data failed",))
        return ok

    with pytest.raises(InstallerError, match="the world bootstrap failed \\(exit 1\\)"):
        run(
            coa_entry(world_bootstrap_service=BOOTSTRAP_SERVICE),
            rec,
            tmp_path / "server",
            one_shot=one_shot,
        )


def test_the_import_still_runs_over_a_bootstrapped_world(tmp_path: Path) -> None:
    """The bootstrap fills the world schema; the import's probe then runs, not skips it."""
    rec = Recorder()
    rec.probe_answers = [
        docker.ImportState("absent", "no schemas at all"),
        docker.ImportState("imported", "every schema is full", complete=True),
    ]
    said = run(coa_entry(world_bootstrap_service=BOOTSTRAP_SERVICE), rec, tmp_path / "server")
    assert f"one-shot:{BOOTSTRAP_SERVICE}" in rec.calls
    assert any(c == f"one-shot:{ENTRY.containers.db_import}" for c in rec.calls), said


def test_the_done_marker_is_version_not_updates() -> None:
    chunk = _script(_rendered_bootstrap())
    assert "TABLE_NAME='version'" in chunk
    assert "TABLE_NAME='updates'" not in chunk


def coa_linux() -> CatalogEntry:
    entry = coa()
    return entry.model_copy(
        update={"install": entry.install.model_copy(update={"platforms": ("linux",)})}
    )


def _stage_context(server_dir: Path) -> native.StageContext:
    entry = coa_linux()
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
        ),
        cancel=None,
        secrets=native.Secrets(db_password="pw"),
    )


def test_the_extracted_data_is_left_out_of_the_build_context(tmp_path: Path) -> None:
    """D9: after install `data/` is 6.6 GB, and a Rebuild uploaded all of it.

    BuildKit logged "transferring context: 2.02GB" and the fingerprint gave no
    answer ("the build context holds more than 4294967296 bytes") while the
    install's disk filled (yulon-ubuntu2, 2026-10-28). The generated ignore
    leaves the extracted folders out and keeps `data/sql`, the only part the
    db-import image copies.
    """
    server = tmp_path / "server"
    server.mkdir()
    ignore = server / ".dockerignore"
    ignore.write_text("/cmake-build-debug/*\n", encoding="utf-8")
    made = make(coa_linux(), Recorder())
    list(made.stage_generate_compose(_stage_context(server)))
    text = ignore.read_text(encoding="utf-8")
    assert text.startswith("/cmake-build-debug/*"), text
    assert "data/*" in text.splitlines(), text
    assert "!data/sql" in text.splitlines(), text
    # Docker's own rules, as this project reads them for the build fingerprint:
    # the extracted folders are not sent, the SQL the importer needs still is.
    rules = build_context.parse_dockerignore(text)
    assert rules is not None
    assert rules.excludes("data/maps/0004331.map")
    assert rules.excludes("data/vmaps/1.vmo")
    assert not rules.excludes("data/sql/base/db_world/instance_template.sql")
    # A repair or an update's put-back runs this stage again: no second block.
    list(made.stage_generate_compose(_stage_context(server)))
    assert ignore.read_text(encoding="utf-8") == text


def test_a_game_that_extracts_nothing_gets_no_ignore_block(tmp_path: Path) -> None:
    server = tmp_path / "server"
    server.mkdir()
    made = make(ENTRY, Recorder())
    list(made.stage_generate_compose(_stage_context(server)))
    assert not (server / ".dockerignore").exists()


def test_the_coa_install_owns_the_ignore_block_an_update_restores(tmp_path: Path) -> None:
    """An update's reset restores upstream's .dockerignore; the guard must read it as ours."""
    owned = make(coa_linux(), Recorder()).app_written_paths(tmp_path / "server")
    assert ".dockerignore" in owned["."]
    assert (
        ".dockerignore" not in make(ENTRY, Recorder()).app_written_paths(tmp_path / "server")["."]
    )
