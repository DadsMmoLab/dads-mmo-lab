"""T748: a NEW WotLK or Unbound install gets a generated database root password.

Before this change every WotLK and Unbound install shared the published root
password `password` (catalog.json, `"mode": "fixed"`). Now a fresh folder mints
`wotlk-` or `unbound-` plus 16 hex, the same mechanism the CMaNGOS families use:
`.db_password` (0600) and `.env` carry it, and the compose text never does.

An install made before this change is told apart by its own install record
(`.yulon-install.json`), never by a missing file: a new install's record says
`"db_password": "generated"` from the moment the folder is claimed, and a record
written before T748 does not. Such an install keeps `password` (or the
`DB_ROOT_PASSWORD` already in its own `.env`), untouched.

Every test here is about one reader or one step of that rule, and each reads
the password the way the app does at that point: through the install's own
folder, not through a constant.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine, install
from yulon import dbsecret, resources
from yulon.apply import mysql_env
from yulon.catalog import composegen, native
from yulon.catalog.catalog import RECORD_GENERATED, load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.install_wiring import DEFAULT_DB_ROOT_PASSWORD

GENERATED = "wotlk-1a2b3c4d5e6f7a8b"
"""A value shaped like `prefix + token_hex(8)`, for the tests that write one by hand."""


def db_volume(server_dir: Path) -> str:
    """The database volume compose gives this folder: `<project>_db-data`."""
    install_id = composegen.install_id(server_dir, platform_id=lambda: "linux")
    project = composegen.project_name(
        ENTRY.id, server_dir, platform_id=lambda: "linux", install_id=install_id
    )
    return f"{project}_db-data"


def stage_context(
    server_dir: Path, *, record: str = RECORD_GENERATED, secret: str = GENERATED
) -> native.StageContext:
    """A `StageContext` for calling one stage body directly, as the spine would build it.

    `record` is the install record's `db_password` key: a NEW install's by default, `""` for
    an install whose record predates T748."""
    return native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="azerothcore",
            completed=(),
            db_password=record,
        ),
        cancel=None,
        secrets=native.Secrets(db_password=secret),
    )


def db_password_stage(eng: native.StagedInstaller) -> native.Stage:
    """The `db-password` stage by name, failing with the names there are when it is absent."""
    by_name = {stage.name: stage for stage in eng.stages()}
    assert "db-password" in by_name, sorted(by_name)
    return by_name["db-password"]


def a_record(
    server_dir: Path,
    *,
    generated: bool,
    completed: tuple[str, ...] = ("clone-core", "generate-compose"),
) -> None:
    """The install record the spine writes: a NEW install's says its password is generated, and
    one written before T748 does not have the key at all."""
    server_dir.mkdir(parents=True, exist_ok=True)
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="azerothcore",
            completed=completed,
            db_password=RECORD_GENERATED if generated else "",
        ),
    )


def an_old_folder(server_dir: Path) -> None:
    """A folder an install made BEFORE this change leaves behind: its record, with no
    `db_password` key, and no `.db_password`."""
    a_record(server_dir, generated=False)


# -- the install path ------------------------------------------------------


def test_a_new_wotlk_install_writes_a_generated_password_to_its_file(tmp_path: Path) -> None:
    """RED through the real `run()`: the secret is minted, recorded, and not the published one."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)

    record = server_dir / ".db_password"
    assert record.is_file(), "a new install must record its own password in .db_password"
    text = record.read_text(encoding="utf-8")
    assert text.endswith("\n"), "one line, with a trailing newline, as the families write it"
    secret = text.strip()
    assert secret.startswith("wotlk-"), secret
    assert len(secret) == len("wotlk-") + 16, secret
    assert secret != DEFAULT_DB_ROOT_PASSWORD
    if os.name == "posix":
        assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_a_new_wotlk_install_hands_compose_its_password_through_env_only(tmp_path: Path) -> None:
    """The secret is in `.env`, and the compose text names it nowhere, not even as a default."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)

    assert (server_dir / ".db_password").is_file(), "no record, so there is no secret to check"
    secret = (server_dir / ".db_password").read_text(encoding="utf-8").strip()
    env = (server_dir / ".env").read_text(encoding="utf-8")
    assert f"DB_ROOT_PASSWORD={secret}" in env.splitlines()

    for name in composegen.COMPOSE_FILES:
        path = server_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert secret not in text, f"the generated password reached {name}"
        assert "${DB_ROOT_PASSWORD:-" not in text, f"{name} still falls back to a literal"
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert "${DB_ROOT_PASSWORD:?" in base


def test_the_db_password_stage_is_never_recorded_and_comes_before_the_compose_file() -> None:
    """Ordering is what makes "a compose file implies a record" true for every new install.

    The record exists before `generate-compose` writes the compose file, and before the
    database starts."""
    names = [stage.name for stage in engine(Recorder()).stages()]
    assert names.index("db-password") < names.index("generate-compose") < names.index("start-db")
    assert names.index("clone-core") < names.index("db-password"), "the clone needs an empty folder"
    stage = db_password_stage(engine(Recorder()))
    assert stage.recorded is False


def test_a_fresh_folder_mints_and_writes_a_password_the_stage_can_check(tmp_path: Path) -> None:
    """The stage itself, not only the run: a fresh folder gets a `wotlk-` value written."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    stage = db_password_stage(engine(Recorder()))
    lines = list(stage.run(stage_context(server_dir)))
    assert (server_dir / ".db_password").read_text(encoding="utf-8") == GENERATED + "\n"
    assert any(".db_password" in line for line in lines)


def test_a_new_install_refuses_to_mint_next_to_an_existing_database(tmp_path: Path) -> None:
    """A minted value never lands beside a database: that volume was initialised with another
    password. The CMaNGOS refusal, asked of WotLK."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    rec = Recorder()
    rec.volumes.add(db_volume(server_dir))
    stage = db_password_stage(engine(rec))
    with pytest.raises(native.InstallerError) as refusal:
        list(stage.run(stage_context(server_dir)))
    assert ".db_password" in str(refusal.value)
    assert db_volume(server_dir) in str(refusal.value)
    assert not (server_dir / ".db_password").exists()


def test_a_database_kept_from_an_install_made_before_t748_keeps_the_published_password(
    tmp_path: Path,
) -> None:
    """ "Keep my characters" on an install made before T748 kept the volume and no copy of the
    password, because it was the published one. A reinstall to that folder uses it again, and
    writes it down, instead of telling the player to delete their characters."""
    server_dir = tmp_path / "wow"
    # The reinstall's fresh claim: it has not reached `db-password` yet.
    a_record(server_dir, generated=True, completed=("clone-core",))
    rec = Recorder()
    rec.volumes.add(db_volume(server_dir))
    eng = engine(rec)
    secret = eng.resolve_secrets(server_dir).db_password
    assert secret == DEFAULT_DB_ROOT_PASSWORD
    lines = list(db_password_stage(eng).run(stage_context(server_dir, secret=secret)))
    assert (server_dir / ".db_password").read_text(encoding="utf-8") == secret + "\n"
    assert any("kept from an install made before" in line for line in lines), lines


def test_an_old_folder_keeps_the_published_password_and_the_stage_writes_nothing(
    tmp_path: Path,
) -> None:
    """The rule for an install made before this change: its record has no `db_password` key,
    so it keeps `password`, and nothing is written for it."""
    server_dir = tmp_path / "wow"
    an_old_folder(server_dir)
    eng = engine(Recorder())
    assert eng.resolve_secrets(server_dir).db_password == DEFAULT_DB_ROOT_PASSWORD
    context = stage_context(server_dir, record="", secret=DEFAULT_DB_ROOT_PASSWORD)
    list(db_password_stage(eng).run(context))
    assert not (server_dir / ".db_password").exists(), "an old install must not get a record"


def test_a_new_install_whose_password_file_is_gone_is_never_taken_for_an_old_one(
    tmp_path: Path,
) -> None:
    """The record decides, never the missing file. A new install that lost `.db_password` has
    no knowable password: the readers answer None and the spine never hands it `password`."""
    server_dir = tmp_path / "wow"
    a_record(server_dir, generated=True)
    (server_dir / ".env").write_text(f"DB_ROOT_PASSWORD={GENERATED}\n", encoding="utf-8")
    assert load_catalog().get("wow-wotlk").install.db_password(server_dir) is None
    assert engine(Recorder()).resolve_secrets(server_dir).db_password != DEFAULT_DB_ROOT_PASSWORD


def test_a_new_install_past_its_password_stage_never_falls_back_to_the_published_one(
    tmp_path: Path,
) -> None:
    """A NEW install that wrote `.db_password`, made its database and then lost the file is not
    a database kept from an old install: its record says generated AND it got past
    `db-password`. The spine must not hand it `password`, and the stage refuses instead of
    writing the wrong value down."""
    server_dir = tmp_path / "wow"
    a_record(server_dir, generated=True)  # completed generate-compose: the file was written
    rec = Recorder()
    rec.volumes.add(db_volume(server_dir))
    eng = engine(rec)
    with pytest.raises(native.InstallerError):
        eng.resolve_secrets(server_dir)  # outside the install: refused, never minted
    secret = eng.resolve_secrets(server_dir, mint=True).db_password  # the install's own run
    assert secret != DEFAULT_DB_ROOT_PASSWORD
    with pytest.raises(native.InstallerError):
        list(db_password_stage(eng).run(stage_context(server_dir, secret=secret)))
    assert not (server_dir / ".db_password").exists()


def test_an_old_install_rerun_by_this_build_keeps_its_password_and_its_record(
    tmp_path: Path,
) -> None:
    """RED through the real `run()`: an update or rebuild of an install made before T748 writes
    no `.db_password`, never adds the key to its record, and hands compose `password`."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    install(rec, server_dir)
    # Make it the folder a pre-T748 build left: no key, no file, no DB_ROOT_PASSWORD in .env,
    # and the compose files to be written again.
    record = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    record.pop("db_password")
    record["completed"] = [name for name in record["completed"] if name != "generate-compose"]
    (server_dir / native.STATE_FILE).write_text(json.dumps(record), encoding="utf-8")
    (server_dir / ".db_password").unlink()
    (server_dir / ".env").write_text("", encoding="utf-8")

    install(rec, server_dir)

    assert not (server_dir / ".db_password").exists()
    after = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert "db_password" not in after, after
    env = (server_dir / ".env").read_text(encoding="utf-8").splitlines()
    assert f"DB_ROOT_PASSWORD={DEFAULT_DB_ROOT_PASSWORD}" in env
    assert load_catalog().get("wow-wotlk").install.db_password(server_dir) == "password"


def test_a_new_install_says_so_in_its_record(tmp_path: Path) -> None:
    """The fact the old/new rule reads is written by the install itself."""
    server_dir = tmp_path / "wow"
    install(Recorder(images=False), server_dir)
    record = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert record.get("db_password") == RECORD_GENERATED, record


def test_a_copy_kept_when_a_new_wotlk_install_was_purged_is_written_back(tmp_path: Path) -> None:
    """ "Keep my characters" on a new install: the same password comes back with the same volume."""
    server_dir = tmp_path / "wow"
    # The reinstall's fresh claim: it has not reached `db-password` yet.
    a_record(server_dir, generated=True, completed=("clone-core",))
    install_id = composegen.install_id(server_dir, platform_id=lambda: "linux")
    dbsecret.remember(
        ENTRY.id,
        install_id,
        password=GENERATED,
        volume=db_volume(server_dir),
    )
    rec = Recorder()
    rec.volumes.add(db_volume(server_dir))
    eng = engine(rec)
    assert eng.resolve_secrets(server_dir).db_password == GENERATED
    list(db_password_stage(eng).run(stage_context(server_dir)))
    assert (server_dir / ".db_password").read_text(encoding="utf-8") == GENERATED + "\n"


# -- the catalog and its accessor ------------------------------------------


@pytest.mark.parametrize(("game", "prefix"), [("wow-wotlk", "wotlk-"), ("wow-unbound", "unbound-")])
def test_wotlk_and_unbound_plan_a_generated_password_with_the_published_one_as_legacy(
    game: str, prefix: str
) -> None:
    plan = load_catalog().get(game).install.password
    assert plan.mode == "generated"
    assert plan.file == ".db_password"
    assert plan.prefix == prefix
    assert plan.legacy == DEFAULT_DB_ROOT_PASSWORD


def test_db_password_is_the_file_then_what_the_record_says(tmp_path: Path) -> None:
    """The accessor every reader calls. The file wins; with no file, a folder whose record
    predates T748 (or that has no record: adopted where it is) answers `password`, exactly as
    before; a new install's record answers nothing, so no caller is handed a guess."""
    wotlk = load_catalog().get("wow-wotlk").install
    assert wotlk.db_password(tmp_path) == DEFAULT_DB_ROOT_PASSWORD, "no record: as before T748"

    an_old_folder(tmp_path)
    assert wotlk.db_password(tmp_path) == DEFAULT_DB_ROOT_PASSWORD

    (tmp_path / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    assert wotlk.db_password(tmp_path) == GENERATED

    new = tmp_path / "new"
    a_record(new, generated=True)
    assert wotlk.db_password(new) is None


def test_an_old_folder_uses_the_db_root_password_its_own_env_already_sets(tmp_path: Path) -> None:
    """Compose already honoured `DB_ROOT_PASSWORD` in `.env`; an old folder keeps that value."""
    an_old_folder(tmp_path)
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=mine-123\n", encoding="utf-8")
    assert load_catalog().get("wow-wotlk").install.db_password(tmp_path) == "mine-123"


# -- the readers ------------------------------------------------------------


def test_the_console_tab_hands_a_new_install_its_own_generated_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED through the Server tab's SQL seam: Create account is sent with the folder's password.

    This is the same end-to-end check the TBC, Vanilla and Tortoise tabs already have
    (`test_a_generated_password_game_is_never_handed_the_fixed_one`), asked of WotLK.
    """
    from yulon.ui import controller_view as controller_view_module

    (tmp_path / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    handed_to_sql: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if isinstance(env, dict) and "MYSQL_PWD" in env:
            handed_to_sql.append(str(env["MYSQL_PWD"]))
        return subprocess.CompletedProcess(argv, 0, "" if len(handed_to_sql) < 2 else "1", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    services = controller_view_module.ControllerServices.for_wotlk(ENTRY, tmp_path, None)
    services.create_account("bob", "hunter2", 0)

    assert handed_to_sql, "nothing was sent to mysql at all"
    assert set(handed_to_sql) == {GENERATED}, handed_to_sql


def test_the_backup_seam_hands_mysql_the_folders_password(tmp_path: Path) -> None:
    """RED through the Backup and Restore seam: `DockerMysql` is built with the new password,
    and the password reaches the client only as `MYSQL_PWD` in its environment."""
    from yulon.ui import controller_view as controller_view_module

    (tmp_path / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    password = controller_view_module._db_password(ENTRY, tmp_path)
    mysql = controller_view_module._mysql_for(ENTRY, password, wsl_distro=None)
    assert mysql.root_password == GENERATED
    assert mysql_env(mysql.root_password)["MYSQL_PWD"] == GENERATED


def test_the_backup_snapshot_for_an_update_reads_the_folders_password(tmp_path: Path) -> None:
    """The update route's database copy (`install_wiring._MaintenanceSnapshot`) is a backup too."""
    from yulon import install_wiring

    (tmp_path / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    snapshot = install_wiring._MaintenanceSnapshot(ENTRY, wsl_distro=None)
    assert snapshot._mysql(tmp_path).root_password == GENERATED


def test_module_sql_with_no_runner_given_uses_the_folders_password(tmp_path: Path) -> None:
    """`modules.applier()` builds its own `DockerSql` when no runner is passed; that must be the
    folder's password, not the constant."""
    from yulon.controller_wow_wotlk import modules

    (tmp_path / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    applier = modules.applier(tmp_path, world_running=lambda: False)
    assert applier.sql is not None
    assert applier.sql.root_password == GENERATED


def test_the_import_probe_reads_the_password_of_the_folder_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import gate asks the database with the folder's password. It is the one seam that
    gets no folder today; this asks it for one."""
    from yulon import install_wiring
    from yulon.controller_wow_wotlk import repair as wotlk_repair

    (tmp_path / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    seen: list[str] = []

    def fake_import_state(sql: object, mysql: object, _health: object) -> object:
        password = getattr(sql, "root_password", "?")
        seen.append(str(password() if callable(password) else password))
        return object()

    monkeypatch.setattr(wotlk_repair, "import_state", fake_import_state)
    probe, _reset = install_wiring.import_gate_for(ENTRY, server_dir=tmp_path)
    assert probe is not None
    probe()
    assert seen == [GENERATED]


def test_the_folder_import_probe_answers_a_new_install_from_its_env_line_when_the_file_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T785: `.db_password` is gone, `.env` still holds the line `generate-compose` wrote. The
    probe is answered with it, not refused as "not knowable"."""
    from yulon import install_wiring
    from yulon.controller_wow_wotlk import repair as wotlk_repair

    a_record(tmp_path, generated=True)
    (tmp_path / ".env").write_text(f"DB_ROOT_PASSWORD={GENERATED}\n", encoding="utf-8")
    seen: list[str] = []

    def fake_import_state(sql: object, mysql: object, _health: object) -> object:
        password = getattr(sql, "root_password", "?")
        seen.append(str(password() if callable(password) else password))
        return object()

    monkeypatch.setattr(wotlk_repair, "import_state", fake_import_state)
    probe, _reset = install_wiring.import_gate_for(ENTRY, server_dir=tmp_path)
    assert probe is not None
    probe()
    assert seen == [GENERATED]


def test_the_folder_import_probe_refuses_an_env_line_compose_reads_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T785: the T748 round-3 text reaches the probe's answer, not a generic "not knowable"."""
    from yulon import install_wiring
    from yulon.catalog.catalog import DbPasswordUnknown
    from yulon.controller_wow_wotlk import repair as wotlk_repair

    a_record(tmp_path, generated=True)
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=has$dollar\n", encoding="utf-8")
    with pytest.raises(DbPasswordUnknown) as unknown:
        ENTRY.install.known_db_password(tmp_path)
    monkeypatch.setattr(wotlk_repair, "import_state", lambda sql, mysql, _h: sql.root_password())
    probe, _reset = install_wiring.import_gate_for(ENTRY, server_dir=tmp_path)
    assert probe is not None
    state = probe()
    assert state.state == "unreadable"
    assert str(unknown.value) in state.detail, state.detail


def test_reset_defaults_reads_an_old_folders_published_password(tmp_path: Path) -> None:
    """Reset server settings to default writes the password into the confs. An old folder's
    is `password`."""
    from yulon import reset_defaults

    an_old_folder(tmp_path)
    seams = platform_seams_for_tests()
    assert reset_defaults._password(ENTRY, tmp_path, seams) == DEFAULT_DB_ROOT_PASSWORD


def platform_seams_for_tests() -> object:
    """The Seams `reset_defaults` takes, pinned to Linux as the other tests pin it."""
    from yulon.catalog import native as native_module

    return native_module.Seams(platform_id=lambda: "linux")


def test_the_shipped_wotlk_template_asks_for_the_password_from_env() -> None:
    """A template that spells the old `${DB_ROOT_PASSWORD:-{{DB_PASSWORD}}}` is a fixed-password
    template, and a generated one must not carry the token at all."""
    template = resources.installers_dir() / "wow-wotlk" / "native" / "base.yml.tmpl"
    text = template.read_text(encoding="utf-8")
    assert "{{DB_PASSWORD}}" not in text
    assert "${DB_ROOT_PASSWORD:-" not in text
    assert "${DB_ROOT_PASSWORD:?" in text


def test_the_generated_password_is_never_printed_by_the_install(tmp_path: Path) -> None:
    """Not in the install's own log lines: a support file and the console echo carry those."""
    server_dir = tmp_path / "wow"
    lines = install(Recorder(images=False), server_dir)
    secret = (server_dir / ".db_password").read_text(encoding="utf-8").strip()
    printed = "\n".join(lines)
    assert secret not in printed
    assert secret.removeprefix("wotlk-") not in printed


def test_the_engines_import_gate_answers_for_the_folder_it_is_given(tmp_path: Path) -> None:
    """The install engine's refusals and import stage ask the gate for their own folder."""
    seen: list[Path] = []

    def gate(server_dir: Path) -> tuple[object, object]:
        seen.append(server_dir)
        return (None, None)

    eng = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_gate=gate,  # type: ignore[arg-type]
        seams=Recorder().seams(),
    )
    folder = tmp_path / "wow"
    eng._gate_for(folder)
    assert seen == [folder]


def test_the_folder_import_gate_reads_the_password_only_when_asked_and_says_when_it_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Building the gate reads nothing (a tab is built for a server in a stopped WSL distro, and
    reading its folder starts it). Asked, a new install whose file is gone is "unreadable", and
    reset drops nothing."""
    from yulon import install_wiring
    from yulon.controller_wow_wotlk import repair as wotlk_repair

    a_record(tmp_path, generated=True)
    reads: list[Path] = []
    real = type(ENTRY.install).db_password

    def counting(self: object, server_dir: Path) -> str | None:
        reads.append(server_dir)
        return real(self, server_dir)  # type: ignore[arg-type]

    monkeypatch.setattr(type(ENTRY.install), "db_password", counting)
    monkeypatch.setattr(wotlk_repair, "import_state", lambda sql, mysql, _h: sql.root_password())
    monkeypatch.setattr(
        wotlk_repair, "reset_unfinished", lambda sql, mysql, **_kw: sql.root_password()
    )
    probe, reset = install_wiring.import_gate_for(ENTRY, server_dir=tmp_path)
    assert probe is not None and reset is not None
    assert reads == [], "the gate read the folder before anything asked the database"
    state = probe()
    assert state.state == "unreadable", state
    assert ".db_password" in state.detail
    with pytest.raises(native.InstallerError, match="Nothing was dropped"):
        reset()


def test_the_database_healthcheck_keeps_the_password_out_of_argv(tmp_path: Path) -> None:
    """A container's processes are in the host's `ps`: the probe hands mysql its password
    through MYSQL_PWD, never `--password=` / `-p`."""
    plan = composegen.render(
        ENTRY, tmp_path, templates_root=resources.installers_dir(), db_password=GENERATED
    )
    for text in (plan.base, plan.override, plan.build):
        assert text is not None
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert "--password" not in code
        assert " -p$" not in code and ' -p"' not in code
    assert 'MYSQL_PWD=\\"$$MYSQL_ROOT_PASSWORD\\"' in plan.base


@pytest.mark.parametrize("record", [".db_password", ".env", ".yulon-install.json"])
def test_a_moved_in_server_keeps_this_computers_password_and_record(
    tmp_path: Path, record: str
) -> None:
    """Requirement 4: a server moved from another computer keeps the password of the install it
    lands in. The package can never carry the password file, `.env` or the install record, so
    neither the value nor the old/new fact is replaced; the packed confs get this folder's own
    password where the placeholder is."""
    from yulon import move, move_server

    with pytest.raises(ValueError, match="never travels"):
        move._safe_target(record)

    server_dir = tmp_path / "wow"
    a_record(server_dir, generated=True)
    (server_dir / ".db_password").write_text(GENERATED + "\n", encoding="utf-8")
    password = ENTRY.install.db_password(server_dir)  # what `ui/move_in.py` hands the move
    laid = move_server.lay_conf(
        b'LoginDatabaseInfo = "old;3306;root;{{DB_PASSWORD}};acore_auth"\n', None, (), password
    )
    assert laid == f'LoginDatabaseInfo = "old;3306;root;{GENERATED};acore_auth"\n'.encode()

    old = tmp_path / "old"
    an_old_folder(old)
    assert ENTRY.install.db_password(old) == DEFAULT_DB_ROOT_PASSWORD


@pytest.mark.parametrize("prefix", ["wotlk-", "unbound-"])
def test_the_support_redactor_masks_a_new_wotlk_password_it_was_never_told(prefix: str) -> None:
    """A log line can carry the generated value from a folder the support file never read (a
    worldserver's DATABASE_INFO echo): the redactor masks it by the minted shape alone."""
    from yulon.support.redact import Redactor

    value = prefix + "0f1e2d3c4b5a6978"
    line = f"Cannot connect: ac-database;3306;root;{value};acore_auth and later {value}"
    assert value not in Redactor.build([]).redact(line)


# -- round 2: no mint outside the install, recovery from .env (cold review 2026-10-13) ------


def _update(rec: Recorder, server_dir: Path) -> list[str]:
    """Update to latest's put-back of this app's own files: `_rewrite_what_we_own()`."""
    from yulon.catalog.installer import InstallOptions

    state = native.read_state(server_dir, valid=engine(rec).stage_names())
    assert state is not None
    options = InstallOptions(server_dir=server_dir)
    return list(engine(rec)._rewrite_what_we_own(server_dir, options, state))


def test_an_update_with_the_password_file_gone_restores_it_from_env(tmp_path: Path) -> None:
    """The `.env` line is the last copy of the password the database was made with: an
    Update writes `.db_password` back from it and never mints a new one over it."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    install(rec, server_dir)
    real = (server_dir / ".db_password").read_text(encoding="utf-8").strip()
    (server_dir / ".db_password").unlink()
    env_before = (server_dir / ".env").read_bytes()

    _update(rec, server_dir)

    assert (server_dir / ".db_password").read_text(encoding="utf-8") == real + "\n"
    assert (server_dir / ".env").read_bytes() == env_before
    if os.name == "posix":
        assert stat.S_IMODE((server_dir / ".db_password").stat().st_mode) == 0o600


def test_an_update_with_the_file_and_the_env_line_gone_is_refused_and_writes_nothing(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    install(rec, server_dir)
    (server_dir / ".db_password").unlink()
    (server_dir / ".env").write_text("OTHER=1\n", encoding="utf-8")
    compose_before = (server_dir / "docker-compose.yml").read_bytes()

    with pytest.raises(native.InstallerError) as refusal:
        _update(rec, server_dir)

    assert ".db_password" in str(refusal.value)
    assert "docker" not in str(refusal.value), "player words, no command"
    assert not (server_dir / ".db_password").exists()
    assert (server_dir / ".env").read_text(encoding="utf-8") == "OTHER=1\n"
    assert (server_dir / "docker-compose.yml").read_bytes() == compose_before


def test_no_caller_but_the_install_can_mint_a_password(tmp_path: Path) -> None:
    """A new install's folder with neither the file nor the `.env` line: every reader outside
    `run()` refuses instead of handing back a fresh value nobody's database knows."""
    server_dir = tmp_path / "wow"
    a_record(server_dir, generated=True)
    with pytest.raises(native.InstallerError):
        engine(Recorder()).resolve_secrets(server_dir)
    assert not (server_dir / ".db_password").exists()


# -- round 3: the old install's .env line as compose reads it (cold review 2026-10-13) -----

READ_AS_COMPOSE = [
    ('"my pass;word"', "my pass;word"),
    ('"a\\"b\\\\c"', 'a"b\\c'),
    ("'pa$word'", "pa$word"),
    ("'pa$s#1' # my note", "pa$s#1"),
    ("'it\\'s'", "it's"),
    ("pass;1 # my note", "pass;1"),
    ("pa\t#1", "pa\t#1"),
]
"""`.env` value text -> what compose-go's dotenv parser hands the database for it."""

READ_AS_COMPOSE_IDS = [
    "double-quoted",
    "double-quoted-escapes",
    "single-quoted-dollar",
    "single-quoted-then-comment",
    "single-quoted-escaped-quote",
    "unquoted-space-comment",
    "unquoted-tab-hash-is-not-a-comment",
]

DOCKER_READS_OTHERWISE = [
    "pa$s#1 # my note",
    "pa$$word",
    '"pa$word"',
    '"pa\\nword"',
    "'it''s'",
    "'never closed",
]
"""Values compose expands (`$`, escapes) or cannot read as one value: Yu'lon refuses them."""

DOCKER_READS_OTHERWISE_IDS = [
    "unquoted-dollar-and-hash",
    "unquoted-double-dollar",
    "double-quoted-dollar",
    "double-quoted-escape",
    "text-after-the-closing-quote",
    "unterminated-quote",
]


@pytest.mark.parametrize(("text", "value"), READ_AS_COMPOSE, ids=READ_AS_COMPOSE_IDS)
def test_dotenv_secret_reads_a_value_as_compose_go_does(
    tmp_path: Path, text: str, value: str
) -> None:
    """compose-go's dotenv parser (parser.go): unquoted ends at " #" (a space, never a tab),
    double quotes take `\\"` and `\\\\`, single quotes take everything but `\\'` as written."""
    (tmp_path / ".env").write_text(f"DB_ROOT_PASSWORD={text}\n", encoding="utf-8")
    assert composegen.dotenv_secret(tmp_path, "DB_ROOT_PASSWORD") == value


@pytest.mark.parametrize("text", DOCKER_READS_OTHERWISE, ids=DOCKER_READS_OTHERWISE_IDS)
def test_dotenv_secret_refuses_a_value_compose_would_expand(tmp_path: Path, text: str) -> None:
    """Never a guess at compose's `$` expansion: `pa$$word` reaches MySQL as `pa$word`, and
    `pa$s#1 # my note` as `pa#1`."""
    (tmp_path / ".env").write_text(f"DB_ROOT_PASSWORD={text}\n", encoding="utf-8")
    with pytest.raises(composegen.DotenvSecretError) as refusal:
        composegen.dotenv_secret(tmp_path, "DB_ROOT_PASSWORD")
    message = str(refusal.value)
    assert "single quotes" in message
    assert text not in message, "the password itself is never echoed"


def _an_old_install_with_env(rec: Recorder, server_dir: Path, text: str) -> bytes:
    """An install whose record predates T748, its `.db_password` gone, `.env` setting `text`."""
    install(rec, server_dir)
    record = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    record.pop("db_password")
    (server_dir / native.STATE_FILE).write_text(json.dumps(record), encoding="utf-8")
    (server_dir / ".db_password").unlink()
    (server_dir / ".env").write_text(f"OTHER=1\nDB_ROOT_PASSWORD={text}\n", encoding="utf-8")
    return (server_dir / ".env").read_bytes()


@pytest.mark.parametrize(("text", "value"), READ_AS_COMPOSE, ids=READ_AS_COMPOSE_IDS)
def test_an_old_installs_own_env_password_is_read_as_compose_reads_it(
    tmp_path: Path, text: str, value: str
) -> None:
    """Round 2 (T748 review): an old install's own `DB_ROOT_PASSWORD` only goes into `.env`,
    never compose text, so an Update renders whatever it holds, leaves the line as written,
    and every reader gets the value compose hands the database."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    env_before = _an_old_install_with_env(rec, server_dir, text)

    _update(rec, server_dir)

    assert (server_dir / ".env").read_bytes() == env_before
    assert not (server_dir / ".db_password").exists()
    assert load_catalog().get("wow-wotlk").install.db_password(server_dir) == value
    assert engine(rec).resolve_secrets(server_dir).db_password == value
    assert value not in (server_dir / "docker-compose.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("text", DOCKER_READS_OTHERWISE, ids=DOCKER_READS_OTHERWISE_IDS)
def test_an_old_installs_env_password_docker_reads_otherwise_is_refused(
    tmp_path: Path, text: str
) -> None:
    """Round 3 (T748 review): the database has compose's reading of the line, so a raw read
    would hand every Yu'lon reader a password MySQL refuses. The Update refuses in player
    words, changes nothing, and the readers answer "not knowable" rather than a guess."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    env_before = _an_old_install_with_env(rec, server_dir, text)
    compose_before = (server_dir / "docker-compose.yml").read_bytes()

    with pytest.raises(native.InstallerError) as refusal:
        _update(rec, server_dir)

    assert "single quotes" in str(refusal.value)
    assert (server_dir / ".env").read_bytes() == env_before
    assert not (server_dir / ".db_password").exists()
    assert (server_dir / "docker-compose.yml").read_bytes() == compose_before
    assert load_catalog().get("wow-wotlk").install.db_password(server_dir) is None
    with pytest.raises(native.InstallerError):
        engine(rec).resolve_secrets(server_dir)
    from yulon import reset_defaults

    assert reset_defaults._password(ENTRY, server_dir, platform_seams_for_tests()) is None


# -- round 3: an empty .db_password is a missing one (cold review 2026-10-13) -----------


@pytest.mark.parametrize("blank", ["", "\n", "  \n"], ids=["empty", "newline", "spaces"])
def test_an_empty_password_file_is_written_back_from_env(tmp_path: Path, blank: str) -> None:
    """`write_secret` truncates in place, so a crash mid-write leaves an empty file. It must
    not beat the intact `.env`: the read treats it as gone and puts the value back."""
    server_dir = tmp_path / "wow"
    rec = Recorder(images=False)
    install(rec, server_dir)
    real = (server_dir / ".db_password").read_text(encoding="utf-8").strip()
    (server_dir / ".db_password").write_text(blank, encoding="utf-8")

    assert engine(rec).resolve_secrets(server_dir).db_password == real
    assert (server_dir / ".db_password").read_text(encoding="utf-8") == real + "\n"


def test_the_password_stage_does_not_keep_an_empty_file(tmp_path: Path) -> None:
    """The `db-password` stage writes the resolved value over an empty file rather than
    "keeping" a file that holds no password."""
    server_dir = tmp_path / "wow"
    a_record(server_dir, generated=True, completed=())
    (server_dir / ".db_password").write_text("", encoding="utf-8")
    rec = Recorder()
    eng = engine(rec)
    list(db_password_stage(eng).run(stage_context(server_dir)))
    assert (server_dir / ".db_password").read_text(encoding="utf-8") == GENERATED + "\n"


def test_render_never_rewrites_an_old_installs_env_line_it_cannot_read(tmp_path: Path) -> None:
    """A caller that names a password (`channel_setup`'s `db_password=`) must not get an old
    install's unreadable `.env` line replaced: that line is its database's only record."""
    an_old_folder(tmp_path)
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=pa$$word\n", encoding="utf-8")
    with pytest.raises(composegen.DotenvSecretError):
        composegen.render(
            ENTRY,
            tmp_path,
            templates_root=resources.installers_dir(),
            db_password=DEFAULT_DB_ROOT_PASSWORD,
            platform_id=lambda: "linux",
        )


def test_render_replaces_a_new_installs_env_line_it_cannot_read(tmp_path: Path) -> None:
    """A new install's `.db_password` is its record, so a hand-edited `.env` line compose
    would read otherwise is written over with it, as any other stale line is."""
    a_record(tmp_path, generated=True)
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=pa$$word\n", encoding="utf-8")
    plan = composegen.render(
        ENTRY,
        tmp_path,
        templates_root=resources.installers_dir(),
        db_password=GENERATED,
        platform_id=lambda: "linux",
    )
    assert plan.dotenv == {"DB_ROOT_PASSWORD": GENERATED}


# -- round 4: a reader never turns "not knowable" into the published password ----------


def _unknowable(server_dir: Path, case: str) -> str:
    """A folder whose password no reader can know, and the words its refusal must carry."""
    if case == "old-env-docker-reads-otherwise":
        an_old_folder(server_dir)
        (server_dir / ".env").write_text("DB_ROOT_PASSWORD=pa$word\n", encoding="utf-8")
        return "single quotes"
    a_record(server_dir, generated=True)
    (server_dir / ".db_password").write_text("", encoding="utf-8")
    return ".db_password"


UNKNOWABLE = ["old-env-docker-reads-otherwise", "new-blank-file-no-env-line"]


@pytest.mark.parametrize("case", UNKNOWABLE)
def test_module_sql_with_no_runner_refuses_a_password_it_cannot_know(
    tmp_path: Path, case: str
) -> None:
    """Round 4 (T748 review): `modules.applier()` used to log in as root with `password` when
    `db_password()` answered None, which only ever ends in "Access denied"."""
    from yulon.apply import ApplyRefusal
    from yulon.controller_wow_wotlk import modules

    words = _unknowable(tmp_path, case)
    with pytest.raises(ApplyRefusal) as refusal:
        modules.applier(tmp_path, world_running=lambda: False)
    assert words in str(refusal.value)


@pytest.mark.parametrize("case", UNKNOWABLE)
def test_the_update_snapshot_refuses_a_password_it_cannot_know(tmp_path: Path, case: str) -> None:
    """Round 4: the update's database copy builds no `DockerMysql` holding `password`."""
    from yulon import install_wiring
    from yulon.controller_wow_wotlk import maintenance

    words = _unknowable(tmp_path, case)
    snapshot = install_wiring._MaintenanceSnapshot(ENTRY, wsl_distro=None)
    with pytest.raises(maintenance.MaintenanceError) as refusal:
        snapshot._mysql(tmp_path)
    assert words in str(refusal.value)


def test_a_reader_takes_a_new_installs_password_from_env_when_its_file_is_gone(
    tmp_path: Path,
) -> None:
    """The `.env` line `generate-compose` wrote is the same value: a reader takes it (and never
    writes; putting the file back is the engine's job)."""
    from yulon.controller_wow_wotlk import modules

    a_record(tmp_path, generated=True)
    (tmp_path / ".env").write_text(f"DB_ROOT_PASSWORD={GENERATED}\n", encoding="utf-8")
    applier = modules.applier(tmp_path, world_running=lambda: False)
    assert applier.sql is not None
    assert applier.sql.root_password == GENERATED
    assert not (tmp_path / ".db_password").exists()


def test_an_old_folders_blank_password_file_reads_as_missing(tmp_path: Path) -> None:
    """A blank `.db_password` is a missing one for every reader, as for the engine (round 3)."""
    an_old_folder(tmp_path)
    (tmp_path / ".db_password").write_text("\n", encoding="utf-8")
    assert ENTRY.install.db_password(tmp_path) == DEFAULT_DB_ROOT_PASSWORD


def test_the_refusal_says_how_to_write_a_quote_inside_single_quotes(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=pa$word\n", encoding="utf-8")
    with pytest.raises(composegen.DotenvSecretError) as refusal:
        composegen.dotenv_secret(tmp_path, "DB_ROOT_PASSWORD")
    assert "\\'" in str(refusal.value)
