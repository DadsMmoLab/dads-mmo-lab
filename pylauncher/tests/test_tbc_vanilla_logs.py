"""T169: TBC and Vanilla write their file logs into the server folder, not the container.

mangos-tbc @75f9ae68 and mangos-classic @8ec338a1 ship `LogsDir = ""` in both
`mangosd.conf.dist.in` and `realmd.conf.dist.in` (read 2026-09-28 with `gh api
.../contents/src/{mangosd,realmd}/*.conf.dist.in?ref=<pin>`), and `Log::Initialize()`
opens every `*LogFile` at `LogsDir + name`, so an empty one writes Server.log,
DBErrors.log, EventAIErrors.log, Char.log, SD2Errors.log and Realmd.log into the
working directory `/opt/mangos/bin` INSIDE the container: invisible from the host
and gone on every recreate. Neither conf names a `HonorDir` or `PDumpDir` at those
pins, so `LogsDir` is the one folder setting.

The owner's decision (2026-09-28), "New + existing (Repair)": a new install states
`LogsDir = "../logs"` in both confs, and T165's `composegen.server_folders()` binds
`./logs` for it; an existing install is offered "Repair server files…", which adds
the bind AND sets that one key in the conf it already has -- nothing else in the
file changes, and the conf is backed up first like the compose file. A value the
player set themselves is never overwritten: the bind follows it when it names a
folder of the server's own, and the repair says why it binds nothing when it does not.
"""

from __future__ import annotations

import os
import posixpath
import stat
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_repair_server_files import (  # noqa: F401 - `ps` is the Server tab fixture
    _answer,
    _Route,
    _view,
    engine,
    installed,
    make_old,
    ps,
)
from tests.test_reset_defaults import TEMPLATES, FakeImage, _fresh_install, _seams
from tests.test_server_folders import SERVICE_CONF, host_binds, render_base, service, table
from yulon import reset_defaults
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.install_wiring import repair_compose_for_app

CATALOG = load_catalog()
TBC = CATALOG.get("wow-tbc")
VANILLA = CATALOG.get("wow-vanilla")
TORTOISE = CATALOG.get("wow-tortoise")
OLD_GAMES = [TBC, VANILLA]

# The real lines around `LogsDir`, copied from mangos-tbc @75f9ae68
# `src/mangosd/mangosd.conf.dist.in` lines 80-96 and `src/realmd/realmd.conf.dist.in`
# lines 128-138 (mangos-classic @8ec338a1 differs only in the database names). LF,
# as upstream's are. What every TBC and Vanilla install made before T169 still has,
# since nothing re-patches a finished install's conf.
MANGOSD_DIST = (
    "###################################################################################################################\n"
    "\n"
    "RealmID = 1\n"
    'DataDir = "."\n'
    'LogsDir = ""\n'
    'LoginDatabaseInfo     = "127.0.0.1;3306;mangos;mangos;tbcrealmd"\n'
    'WorldDatabaseInfo     = "127.0.0.1;3306;mangos;mangos;tbcmangos"\n'
    'CharacterDatabaseInfo = "127.0.0.1;3306;mangos;mangos;tbccharacters"\n'
    'LogsDatabaseInfo      = "127.0.0.1;3306;mangos;mangos;tbclogs"\n'
    "LoginDatabaseConnections = 1\n"
    "MaxPingTime = 30\n"
    "WorldServerPort = 8085\n"
    'BindIP = "0.0.0.0"\n'
    'SD2ErrorLogFile = "SD2Errors.log"\n'
    "Spawns.ZoneArea = 0\n"
    "\n"
    'LogFile = "Server.log"\n'
)
REALMD_DIST = (
    "###################################################################################################################\n"
    "\n"
    'LoginDatabaseInfo = "127.0.0.1;3306;mangos;mangos;tbcrealmd"\n'
    'LogsDir = ""\n'
    "MaxPingTime = 30\n"
    "RealmServerPort = 3724\n"
    'BindIP = "0.0.0.0"\n'
    "ListenerThreads = 1\n"
    'PidFile = ""\n'
    'LogFile = "Realmd.log"\n'
)
DISTS = {"mangosd.conf": MANGOSD_DIST, "realmd.conf": REALMD_DIST}
SET = 'LogsDir = "../logs"'


def logs_target(entry: CatalogEntry) -> str:
    """`<CORE_DIR>/logs`: where `../logs` lands from the template's `working_dir`."""
    svc = service(render_base(entry, Path("/nowhere")), entry, "mangosd")
    return posixpath.normpath(posixpath.join(str(svc["working_dir"]), "../logs"))


def write_confs(server_dir: Path, texts: dict[str, str] | None = None) -> dict[str, Path]:
    """Put the install's `etc/` confs in place, owner-only as the conf stage leaves them."""
    etc = server_dir / composegen.SERVER_CONF_DIR
    etc.mkdir(exist_ok=True)
    paths: dict[str, Path] = {}
    for name, text in (texts or DISTS).items():
        path = etc / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
        paths[name] = path
    return paths


def before_t169(server_dir: Path) -> str:
    """The base file a TBC/Vanilla install made before T169 has: no `./logs` bind, no folder.

    The fresh render less exactly its two `- ./logs:` lines (realmd's and mangosd's).
    Returns the fresh render.
    """
    path = server_dir / composegen.BASE_FILE
    fresh = path.read_text(encoding="utf-8")
    kept = [line for line in fresh.split("\n") if not line.strip().startswith("- ./logs:")]
    assert len(fresh.split("\n")) - len(kept) == 2, "T169 no longer adds two binds"
    path.write_text("\n".join(kept), encoding="utf-8", newline="\n")
    (server_dir / "logs").rmdir()
    return fresh


def an_old_install(tmp_path: Path, entry: CatalogEntry = TBC) -> tuple[Path, str, dict[str, Path]]:
    server_dir = installed(tmp_path, entry)
    fresh = before_t169(server_dir)
    return server_dir, fresh, write_confs(server_dir)


def conf_backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".*" + native.REPAIR_BACKUP_SUFFIX))


def one_line_changed(before: str, after: str, line: str) -> None:
    """`after` is `before` with exactly one line replaced by `line`, endings and all."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(old) == len(new), (before, after)
    differ = [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
    assert len(differ) == 1, differ
    ending = old[differ[0]][len(old[differ[0]].rstrip("\r\n")) :]
    assert new[differ[0]] == line + ending, new[differ[0]]


# -- the rule the server reads its conf by ----------------------------------------


def test_the_reader_answers_what_cmangos_config_reload_does() -> None:
    """`src/shared/Config/Config.cpp` `Reload()` at both pins (identical, read 2026-09-28).

    Left-trim the line; skip it if empty or starting with `#` or `[`; key = the
    trimmed, LOWER-CASED text before the first `=`; value trimmed, then its `"`s
    trimmed; every later line overwrites the earlier (`newEntries[entry] = value`).
    """
    text = 'LogsDir = "a"\n  logsdir =  "b" \n# LogsDir = "c"\n[LogsDir = "d"\n'
    assert composegen.conf_setting(text, "LogsDir") == "b"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('LogsDir = "a"\nLogsDir = "b"\n', "b"),
        ('# LogsDir = "a"\n', None),
        ('[LogsDir = "a"\n', None),
        ('   LogsDir = "a"\n', "a"),
        ('LOGSDIR = "a"\n', "a"),
        ('LogsDir = ""\n', ""),
        ("LogsDir = ../x\r\n", "../x"),
        ('LogsDirX = "a"\n', None),
    ],
    ids=[
        "the-last-line-wins",
        "a-comment-says-nothing",
        "a-section-line-says-nothing",
        "an-indented-line-is-live",
        "the-key-is-any-case",
        "an-empty-value-is-empty-not-absent",
        "unquoted-and-crlf",
        "a-longer-key-is-another-key",
    ],
)
def test_each_rule_of_the_reader(text: str, expected: str | None) -> None:
    assert composegen.conf_setting(text, "LogsDir") == expected


# -- a new install ----------------------------------------------------------------


@pytest.mark.parametrize("entry", OLD_GAMES, ids=lambda e: e.id)
def test_the_catalog_states_logs_dir_in_both_confs(entry: CatalogEntry) -> None:
    """Stated for both binaries: each opens its own logs at its own conf's `LogsDir`."""
    for conf in SERVICE_CONF.values():
        assert table(entry)[conf].get("LogsDir") == '"../logs"', (entry.id, conf)


@pytest.mark.parametrize("role", sorted(SERVICE_CONF))
@pytest.mark.parametrize("entry", OLD_GAMES, ids=lambda e: e.id)
def test_a_new_install_binds_the_folder_its_logs_go_to(
    tmp_path: Path, entry: CatalogEntry, role: str
) -> None:
    """`working_dir` + the table's `LogsDir`, read off the rendered file, is bound to `./logs`."""
    svc = service(render_base(entry, tmp_path), entry, role)
    value = table(entry)[SERVICE_CONF[role]]["LogsDir"].strip('"')
    target = posixpath.normpath(posixpath.join(str(svc["working_dir"]), value))
    assert host_binds(svc).get(target) == "./logs", (target, host_binds(svc))


@pytest.mark.parametrize("entry", OLD_GAMES, ids=lambda e: e.id)
def test_a_new_install_makes_the_logs_folder(tmp_path: Path, entry: CatalogEntry) -> None:
    server_dir = installed(tmp_path, entry)
    assert (server_dir / "logs").is_dir()
    assert f"- ./logs:{logs_target(entry)}\n" in (server_dir / composegen.BASE_FILE).read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("entry", OLD_GAMES, ids=lambda e: e.id)
def test_the_conf_stage_writes_logs_dir_into_the_real_dist_lines(entry: CatalogEntry) -> None:
    """The install's own patcher over upstream's lines: `LogsDir` set in place, nothing else."""
    from yulon.catalog.families import conf

    native_block = entry.install.native
    assert native_block is not None and native_block.cmangos is not None
    for name, dist in DISTS.items():
        only = native_block.cmangos.conf.files[name].model_copy(
            update={"keys": {"LogsDir": table(entry)[name]["LogsDir"]}}
        )
        one_line_changed(dist, conf.patch(dist, only, {}), SET)


# -- an install made before T169: the Repair ---------------------------------------


@pytest.mark.parametrize("entry", OLD_GAMES, ids=lambda e: e.id)
def test_an_old_install_is_offered_the_bind_and_the_setting(
    tmp_path: Path, entry: CatalogEntry
) -> None:
    server_dir, _fresh, _paths = an_old_install(tmp_path, entry)
    check = engine(entry).base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale", check
    assert (check.added, check.removed) == (2, 0), check
    assert check.settings == (
        'LogsDir = "../logs" in etc/mangosd.conf',
        'LogsDir = "../logs" in etc/realmd.conf',
    ), check


@pytest.mark.parametrize("entry", OLD_GAMES, ids=lambda e: e.id)
def test_the_repair_sets_the_one_key_binds_the_folder_and_backs_the_confs_up(
    tmp_path: Path, entry: CatalogEntry
) -> None:
    """Through the Server tab's own wiring: the press the player makes."""
    server_dir, fresh, paths = an_old_install(tmp_path, entry)
    route = repair_compose_for_app(entry, server_dir)
    assert route is not None

    done = route.repair()

    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == fresh
    assert (server_dir / "logs").is_dir()
    for name, path in paths.items():
        with path.open(encoding="utf-8", newline="") as fh:
            one_line_changed(DISTS[name], fh.read(), SET)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, f"{name} lost its owner-only mode"
        [backup] = conf_backups(path)
        assert backup.read_bytes() == DISTS[name].encode("utf-8"), f"{name}'s backup is not it"
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600, "a backup of a password file"
    assert sorted(done.confs) == sorted(b for p in paths.values() for b in conf_backups(p))
    assert route.check().state == "current"
    assert not list(server_dir.rglob("*.yulon-new")), "a temp file was left behind"


def test_a_crlf_conf_keeps_its_endings(tmp_path: Path) -> None:
    """A conf a Windows editor saved: the one line changes, every ending stays CRLF."""
    server_dir = installed(tmp_path)
    before_t169(server_dir)
    crlf = {name: text.replace("\n", "\r\n") for name, text in DISTS.items()}
    paths = write_confs(server_dir, crlf)
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    for name, path in paths.items():
        after = path.read_bytes().decode("utf-8")
        one_line_changed(crlf[name], after, SET)
        assert after.count("\r\n") == after.count("\n"), f"{name} now mixes endings"


def test_a_setting_the_player_chose_is_kept_and_its_folder_bound(tmp_path: Path) -> None:
    """`LogsDir = "../mylogs"` in mangosd.conf: left as it is, and `./mylogs` bound for it."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    theirs = MANGOSD_DIST.replace('LogsDir = ""', 'LogsDir = "../mylogs"')
    write_confs(server_dir, {"mangosd.conf": theirs})
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.settings == ('LogsDir = "../logs" in etc/realmd.conf',), check

    engine().repair_base_compose(InstallOptions(server_dir=server_dir))

    assert paths["mangosd.conf"].read_text(encoding="utf-8") == theirs
    assert conf_backups(paths["mangosd.conf"]) == [], "a conf it did not change was backed up"
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    mangosd = host_binds(service(base, TBC, "mangosd"))
    assert mangosd.get("/opt/mangos/mylogs") == "./mylogs", mangosd
    assert "/opt/mangos/logs" not in mangosd, "a folder its setting no longer names was bound"
    assert (server_dir / "mylogs").is_dir()


@pytest.mark.parametrize("value", ['"/var/log/mangos"', '"logs"', '"../data"'])
def test_a_setting_that_cannot_be_bound_is_kept_and_the_repair_says_why(
    tmp_path: Path, value: str
) -> None:
    """Outside the prefix, inside `bin/`, onto extraction's folder: each kept, none bound."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    theirs = MANGOSD_DIST.replace('LogsDir = ""', f"LogsDir = {value}")
    write_confs(server_dir, {"mangosd.conf": theirs})
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale", check  # realmd's is still offered
    assert check.settings == ('LogsDir = "../logs" in etc/realmd.conf',), check
    [why] = check.kept
    assert "mangosd.conf" in why and value.strip('"') in why and "LogsDir" in why, why

    engine().repair_base_compose(InstallOptions(server_dir=server_dir))

    assert paths["mangosd.conf"].read_text(encoding="utf-8") == theirs
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    binds = list(host_binds(service(base, TBC, "mangosd")))
    assert binds == ["/opt/mangos/etc", "/opt/mangos/data"], binds


def test_the_same_folder_spelled_differently_is_not_rewritten(tmp_path: Path) -> None:
    """`"../logs/"` (Tortoise's realmd spelling) is the folder the table names: no edit."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    theirs = REALMD_DIST.replace('LogsDir = ""', 'LogsDir = "/opt/mangos/logs/"')
    write_confs(server_dir, {"realmd.conf": theirs})
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.settings == ('LogsDir = "../logs" in etc/mangosd.conf',), check


def test_a_key_the_conf_does_not_have_is_added(tmp_path: Path) -> None:
    """No `LogsDir` line at all is upstream's default, `""`: the key is appended."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    without = REALMD_DIST.replace('LogsDir = ""\n', "")
    write_confs(server_dir, {"realmd.conf": without})
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert paths["realmd.conf"].read_text(encoding="utf-8") == without + SET + "\n"


def test_a_later_spelling_the_patch_cannot_reach_is_said_not_written(tmp_path: Path) -> None:
    """An indented `logsdir = ""` after the line: the server would read it, so no edit is made.

    The patcher rewrites column-0 `LogsDir` lines; CMaNGOS's reader takes the last
    line in any case and indentation, which here is one the patch leaves `""`.
    """
    server_dir, _fresh, paths = an_old_install(tmp_path)
    theirs = MANGOSD_DIST + '  logsdir = ""\n'
    write_confs(server_dir, {"mangosd.conf": theirs})
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.settings == ('LogsDir = "../logs" in etc/realmd.conf',), check
    assert any("mangosd.conf" in why for why in check.kept), check
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert paths["mangosd.conf"].read_text(encoding="utf-8") == theirs


def test_a_conf_that_is_not_there_is_not_made(tmp_path: Path) -> None:
    """The bind is added; a conf the install folder lacks is not the repair's to create."""
    server_dir = installed(tmp_path)
    fresh = before_t169(server_dir)
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale" and check.settings == (), check
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == fresh
    assert not (server_dir / composegen.SERVER_CONF_DIR).exists()


def test_a_conf_that_cannot_be_read_is_an_error_and_nothing_is_written(tmp_path: Path) -> None:
    server_dir, _fresh, paths = an_old_install(tmp_path)
    paths["realmd.conf"].write_bytes(b'LogsDir = "\xff"\n')
    base = server_dir / composegen.BASE_FILE
    old = base.read_bytes()
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "error" and "realmd.conf" in check.why, check
    with pytest.raises(InstallerError, match="realmd.conf"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert base.read_bytes() == old
    assert paths["mangosd.conf"].read_text(encoding="utf-8") == MANGOSD_DIST
    assert conf_backups(paths["mangosd.conf"]) == []


def test_an_install_already_bound_keeps_its_empty_setting(tmp_path: Path) -> None:
    """The conf rides with the bind it needs: a file that has the bind is not re-set.

    A post-T169 install whose player put `LogsDir = ""` back, with its compose stale
    for another reason (T98's block cut out): the repair writes the compose file
    and leaves the conf as the player has it.
    """
    server_dir = installed(tmp_path)
    paths = write_confs(server_dir)
    make_old(server_dir)
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale" and check.settings == (), check
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert paths["mangosd.conf"].read_text(encoding="utf-8") == MANGOSD_DIST


def test_the_confs_are_set_before_the_compose_file_so_a_failure_is_offered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conf that cannot be written stops the press before the compose file is replaced.

    Written the other way round, a compose file already binding `./logs` would
    read `current` and the conf it needs would never be offered again.
    """
    server_dir, _fresh, paths = an_old_install(tmp_path)
    base = server_dir / composegen.BASE_FILE
    old = base.read_bytes()
    real = native.os.replace

    def refuse_realmd(src: object, dst: object) -> None:
        if Path(str(dst)).name == "realmd.conf":
            raise OSError(28, "No space left on device")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(native.os, "replace", refuse_realmd)
    with pytest.raises(InstallerError, match="No space left"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert base.read_bytes() == old, "the compose file was replaced before its confs were set"
    assert paths["realmd.conf"].read_text(encoding="utf-8") == REALMD_DIST
    monkeypatch.setattr(native.os, "replace", real)
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale", check
    assert check.settings == ('LogsDir = "../logs" in etc/realmd.conf',), check


def test_a_conf_changed_after_the_check_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody saves mangosd.conf between the press's read and its write: the press aborts."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    base = server_dir / composegen.BASE_FILE
    old = base.read_bytes()
    theirs = MANGOSD_DIST + "Rate.XP.Kill = 2\n"
    real = native.os.fsync

    def edit_meanwhile(fd: int) -> None:
        real(fd)
        paths["mangosd.conf"].write_text(theirs, encoding="utf-8", newline="")

    monkeypatch.setattr(native.os, "fsync", edit_meanwhile)
    with pytest.raises(InstallerError, match="changed"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert paths["mangosd.conf"].read_text(encoding="utf-8") == theirs, "their edit was lost"
    assert base.read_bytes() == old


# -- Tortoise is left as T165 made it ---------------------------------------------


def test_a_tortoise_install_with_upstreams_values_is_offered_nothing(tmp_path: Path) -> None:
    """Its confs already say `../logs` (upstream's value, T165): no edit, no new bind."""
    server_dir = installed(tmp_path, TORTOISE)
    write_confs(
        server_dir,
        {
            "mangosd.conf": 'LogsDir = "../logs"\nHonorDir = "../honor"\nPDumpDir = "../pdump"\n',
            "realmd.conf": 'LogsDir = "../logs/"\n',
        },
    )
    check = engine(TORTOISE).base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "current", check


# -- the Server tab's question ----------------------------------------------------


class _WithSettings(_Route):
    def check(self) -> native.ComposeCheck:
        found = super().check()
        return native.ComposeCheck(
            found.state,
            added=2,
            settings=('LogsDir = "../logs" in etc/mangosd.conf',),
            kept=('etc/realmd.conf sets LogsDir = "x", which is kept as it is.',),
        )


def test_the_question_names_the_conf_setting_and_what_is_kept(
    qapp: object,
    ps: object,  # noqa: F811 - the fixture imported above, as pytest resolves it
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = _WithSettings("stale")
    view = _view(ps, tmp_path, route)  # type: ignore[arg-type]
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    [question] = asked
    assert 'LogsDir = "../logs" in etc/mangosd.conf' in question, question
    assert "which is kept as it is" in question, question
    assert "not your .conf settings" not in question, "it says the confs are not touched"
    assert route.repairs == 0


def test_without_a_setting_the_question_still_says_the_confs_are_not_touched(
    qapp: object,
    ps: object,  # noqa: F811 - the fixture imported above, as pytest resolves it
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(ps, tmp_path, _Route("stale"))  # type: ignore[arg-type]
    asked = _answer(monkeypatch, yes=False)
    view.compose_banner_button.click()
    assert "not your .conf settings" in asked[0], asked[0]
    assert "LogsDir" not in asked[0]


# -- round 2: each service's own binds (cold review) ------------------------------


def test_a_bind_on_mangosd_only_still_gets_realmd_its_setting_and_its_bind(tmp_path: Path) -> None:
    """`./logs` on mangosd says nothing about realmd, which writes where ITS conf says."""
    server_dir = installed(tmp_path)
    path = server_dir / composegen.BASE_FILE
    fresh = path.read_text(encoding="utf-8")
    lines = fresh.split("\n")
    realmd = next(i for i, line in enumerate(lines) if line.strip().startswith("- ./logs:"))
    path.write_text("\n".join(lines[:realmd] + lines[realmd + 1 :]), encoding="utf-8")
    base = path.read_text(encoding="utf-8")
    assert "/opt/mangos/logs" not in host_binds(service(base, TBC, "realmd"))
    assert "/opt/mangos/logs" in host_binds(service(base, TBC, "mangosd"))
    paths = write_confs(server_dir)

    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.settings == ('LogsDir = "../logs" in etc/realmd.conf',), check
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))

    one_line_changed(REALMD_DIST, paths["realmd.conf"].read_text(encoding="utf-8"), SET)
    assert paths["mangosd.conf"].read_text(encoding="utf-8") == MANGOSD_DIST
    assert path.read_text(encoding="utf-8") == fresh


# -- round 2: what a stopped repair says (Codex) ----------------------------------


def test_a_compose_backup_that_fails_writes_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Taken and checked before any conf is touched, so "Nothing was written" is true."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    real = native._backup_beside

    def refuse_compose(path: Path, when: object) -> Path:
        if path.name == composegen.BASE_FILE:
            raise OSError(28, "No space left on device")
        return real(path, when)  # type: ignore[arg-type]

    monkeypatch.setattr(native, "_backup_beside", refuse_compose)
    with pytest.raises(InstallerError, match="Nothing was written"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    for name, conf_path in paths.items():
        assert conf_path.read_text(encoding="utf-8") == DISTS[name], f"{name} was changed"
        assert conf_backups(conf_path) == [], f"{name} was backed up"


def test_a_compose_write_that_fails_after_the_confs_names_them_and_the_next_press_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, fresh, paths = an_old_install(tmp_path)
    base = server_dir / composegen.BASE_FILE
    old = base.read_bytes()
    real = native.os.replace

    def refuse_compose(src: object, dst: object) -> None:
        if Path(str(dst)).name == composegen.BASE_FILE:
            raise OSError(28, "No space left on device")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(native.os, "replace", refuse_compose)
    with pytest.raises(InstallerError) as raised:
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    said = str(raised.value)
    assert base.read_bytes() == old
    for name, conf_path in paths.items():
        one_line_changed(DISTS[name], conf_path.read_text(encoding="utf-8"), SET)
        [backup] = conf_backups(conf_path)
        assert name in said and backup.name in said, said
    assert "Repair server files… again before restarting" in said, said
    assert "Nothing was written" not in said, said

    monkeypatch.setattr(native.os, "replace", real)
    check = engine().base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale" and check.settings == (), check
    engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert base.read_text(encoding="utf-8") == fresh
    assert engine().base_compose_check(InstallOptions(server_dir=server_dir)).state == "current"


# -- round 2: Reset to default does not set what nothing binds (Codex) -------------


def reset_templates() -> dict[str, str]:
    """TBC's image templates, with upstream's real `LogsDir = ""` lines in the two confs."""
    return {
        **TEMPLATES["wow-tbc"],
        "mangosd.conf.dist": MANGOSD_DIST,
        "realmd.conf.dist": REALMD_DIST,
    }


def an_install_to_reset(tmp_path: Path, *, bound: bool) -> tuple[Path, dict[str, bytes]]:
    """A TBC install whose confs say `LogsDir = ""` as before T169, then tuned by hand.

    `bound`: its compose file already links `./logs` (a repaired or post-T169
    install); otherwise it is the base file from before T169. Returns the server
    folder and each conf's text before the hand tuning.
    """
    server_dir = installed(tmp_path)
    if not bound:
        before_t169(server_dir)
    written = _fresh_install("wow-tbc", server_dir, FakeImage(reset_templates()))
    untuned: dict[str, bytes] = {}
    for file, data in written.items():
        text = data.decode("utf-8").replace(SET, 'LogsDir = ""')
        untuned[file] = text.encode("utf-8")
        (server_dir / file).write_bytes(untuned[file] + b"Tuned.By.Hand = 7\n")
    return server_dir, untuned


def test_a_reset_keeps_logs_dir_where_nothing_binds_the_folder_and_names_repair(
    tmp_path: Path,
) -> None:
    server_dir, untuned = an_install_to_reset(tmp_path, bound=False)
    report = reset_defaults.reset(
        TBC,
        server_dir,
        reset_defaults.core_files(TBC),
        seams=_seams(FakeImage(reset_templates())),
    )
    assert [r.outcome for r in report.results] == ["reset"] * 4, report.lines()
    for file, text in untuned.items():
        # Every other key back as installed, the hand tuning gone, LogsDir kept.
        assert (server_dir / file).read_bytes() == text, file
    for name in DISTS:
        conf_text = (server_dir / "etc" / name).read_text(encoding="utf-8")
        assert composegen.conf_setting(conf_text, "LogsDir") == "", name
    last = report.lines()[-1]
    assert "Repair server files…" in last and "Server tab" in last, last
    assert "LogsDir in mangosd.conf and LogsDir in realmd.conf" in last, last


def test_a_reset_of_an_install_that_binds_the_folder_writes_the_tables_logs_dir(
    tmp_path: Path,
) -> None:
    server_dir, _untuned = an_install_to_reset(tmp_path, bound=True)
    report = reset_defaults.reset(
        TBC,
        server_dir,
        reset_defaults.core_files(TBC),
        seams=_seams(FakeImage(reset_templates())),
    )
    for name in DISTS:
        conf_text = (server_dir / "etc" / name).read_text(encoding="utf-8")
        assert composegen.conf_setting(conf_text, "LogsDir") == "../logs", name
    assert not any("Repair server files" in line for line in report.lines()), report.lines()


def test_a_reset_keeps_a_folder_the_player_chose_and_says_so(tmp_path: Path) -> None:
    """Unbound and set to their own folder: kept, and the report says why."""
    server_dir, untuned = an_install_to_reset(tmp_path, bound=False)
    mangosd = server_dir / "etc" / "mangosd.conf"
    mangosd.write_bytes(mangosd.read_bytes().replace(b'LogsDir = ""', b'LogsDir = "../mylogs"'))
    report = reset_defaults.reset(
        TBC, server_dir, ["etc/mangosd.conf"], seams=_seams(FakeImage(reset_templates()))
    )
    text = mangosd.read_text(encoding="utf-8")
    assert composegen.conf_setting(text, "LogsDir") == "../mylogs"
    assert "Tuned.By.Hand" not in text
    assert "LogsDir in mangosd.conf left as it is" in report.lines()[-1], report.lines()


def test_a_conf_made_again_by_a_reset_takes_upstreams_empty_logs_dir_until_bound(
    tmp_path: Path,
) -> None:
    """realmd.conf deleted, compose from before T169: remade as installed but for `LogsDir`."""
    server_dir, untuned = an_install_to_reset(tmp_path, bound=False)
    (server_dir / "etc" / "realmd.conf").unlink()
    report = reset_defaults.reset(
        TBC, server_dir, ["etc/realmd.conf"], seams=_seams(FakeImage(reset_templates()))
    )
    assert [r.outcome for r in report.results] == ["recreated"], report.lines()
    assert (server_dir / "etc" / "realmd.conf").read_bytes() == untuned["etc/realmd.conf"]


# -- round 3: a backup that fails part-way (Codex) --------------------------------


def test_a_compose_backup_that_fails_part_way_leaves_no_backup_and_no_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy dies after some bytes landed: the half backup goes, no folder was made."""
    server_dir, _fresh, paths = an_old_install(tmp_path)
    real = native.shutil.copyfileobj

    def half(source: object, out: object, *args: object) -> None:
        out.write(source.read(10))  # type: ignore[attr-defined]
        out.flush()  # type: ignore[attr-defined]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(native.shutil, "copyfileobj", half)
    with pytest.raises(InstallerError, match="Nothing was written"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    monkeypatch.setattr(native.shutil, "copyfileobj", real)
    assert not list(server_dir.glob(composegen.BASE_FILE + ".*" + native.REPAIR_BACKUP_SUFFIX))
    assert not (server_dir / "logs").exists(), "a folder was made before the backup"
    for name, conf_path in paths.items():
        assert conf_path.read_text(encoding="utf-8") == DISTS[name]


def test_a_backup_never_touches_a_file_already_at_its_name(tmp_path: Path) -> None:
    """A name that is taken is skipped, never opened, and never removed on a failure."""
    path = tmp_path / "docker-compose.yml"
    path.write_text("mine\n", encoding="utf-8")
    when = datetime(2026, 9, 28, 12, 0, 0, 0)
    taken = path.with_name(f"{path.name}.20260928-120000-000000{native.REPAIR_BACKUP_SUFFIX}")
    taken.write_text("an older backup\n", encoding="utf-8")
    made = native._backup_beside(path, when)
    assert made != taken and made.read_text(encoding="utf-8") == "mine\n"
    assert taken.read_text(encoding="utf-8") == "an older backup\n"
    if os.name != "nt":
        assert stat.S_IMODE(made.stat().st_mode) == stat.S_IMODE(path.stat().st_mode)


def test_a_compose_backup_that_fails_makes_no_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backup is taken before any folder is made, so "Nothing was written" stays true."""
    server_dir, _fresh, _paths = an_old_install(tmp_path)

    def refuse(path: Path, when: object) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(native, "_backup_beside", refuse)
    with pytest.raises(InstallerError, match="Nothing was written"):
        engine().repair_base_compose(InstallOptions(server_dir=server_dir))
    assert not (server_dir / "logs").exists(), "a folder was made before the backup"
