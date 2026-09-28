"""T165: the folders a CMaNGOS server writes at run time land in the server folder.

Measured on a real Tortoise install (lead, 2026-09-28): `mangosd.conf` says
`LogsDir = "../logs"` and `realmd.conf` `LogsDir = "../logs/"`, both services run
with `working_dir: /opt/tortoise/bin`, and the image has no `/opt/tortoise/logs`
while compose bound only `./etc` and `./data`. So every file log was lost --
server.log, errors.log, gm.log, char.log, honor.log, Realmd.log and the rest --
and the world printed "Could not open bot log file ../logs/bot_events.csv" for
TortoiseBots' own log, which is written to the same folder. The weekly honor
report (`HonorDir = "../honor"`) and the automatic character dumps
(`PDumpDir = "../pdump"`) sit beside it in the same conf and were lost the same way.

The fix states those settings in the catalog's conf table and binds each folder
they resolve to onto a folder of the same name in the server folder, which the
install makes before compose can (Docker would make it root-owned). An install
made before this gets the binds through T106's "Repair server files…".
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_repair_server_files import engine, installed
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.install_wiring import repair_compose_for_app

CATALOG = load_catalog()
TORTOISE = CATALOG.get("wow-tortoise")

UPSTREAM_TORTOISE_DIRS = {
    "mangosd.conf": {"LogsDir": "../logs", "HonorDir": "../honor", "PDumpDir": "../pdump"},
    "realmd.conf": {"LogsDir": "../logs/"},
}
"""What tortoise-wow's own `.conf.dist.in` files say, which every existing install's
confs still say: `src/mangosd/mangosd.conf.dist.in` lines 16/20/24 and
`src/realmd/realmd.conf.dist.in` line 14 at the pinned commit 187af788, read
2026-09-28 with `gh api .../contents/...?ref=187af788...`, and the lead's live read
of the installed confs on yulon-fedora the same day. `mangosd` reads `LogsDir` for
every `*LogFile` (Log.cpp) and the DB error log (Database.cpp), `HonorDir` for the
HCR report (HonorMgr.cpp `CreateCalculationReport`), `PDumpDir` for the automatic
character dumps (World.cpp `AutoPDumpWorker`)."""

SERVICE_CONF = {"realmd": "realmd.conf", "mangosd": "mangosd.conf"}
"""Which conf each CMaNGOS server binary reads: the template's `command:` runs
`./realmd` and `./mangosd`, which read these two from the bound `etc/`."""


def services(text: str) -> dict[str, Any]:
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    found = loaded["services"]
    assert isinstance(found, dict)
    return found


def service(text: str, entry: CatalogEntry, role: str) -> dict[str, Any]:
    prefix = entry.containers.db.removesuffix("db")
    found = services(text)[f"{prefix}{role}"]
    assert isinstance(found, dict)
    return found


def host_binds(svc: dict[str, Any]) -> dict[str, str]:
    """`{in-container target: host source}` for the service's `./` binds, label dropped."""
    out: dict[str, str] = {}
    for item in svc.get("volumes", []):
        source, target, *_options = str(item).split(":")
        if source.startswith("./"):
            out[target] = source
    return out


def unquoted(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def render_base(entry: CatalogEntry, tmp_path: Path, *, label: str = "") -> str:
    return composegen.render(
        entry,
        tmp_path / "srv",
        templates_root=engine(entry).installers_root,
        db_password="x" * 16,
        bind_label=label,
        platform_id=lambda: "linux",
    ).base


def table(entry: CatalogEntry) -> dict[str, dict[str, str]]:
    native_block = entry.install.native
    assert native_block is not None and native_block.cmangos is not None
    return {name: dict(patch.keys) for name, patch in native_block.cmangos.conf.files.items()}


# -- where the server writes, and what is bound there -------------------------


@pytest.mark.parametrize("role", sorted(SERVICE_CONF))
def test_every_folder_tortoises_confs_write_to_is_bound_into_the_server_folder(
    tmp_path: Path, role: str
) -> None:
    """`working_dir` + each `*Dir` setting, read off the rendered file and the conf, is bound.

    Both ends are read, neither is remembered: the working directory from the
    compose file the install writes, and the setting from the value the install
    leaves in the conf -- the catalog table's when it states one, upstream's
    otherwise. The host side is the folder of the same name in the server folder.
    """
    base = render_base(TORTOISE, tmp_path)
    svc = service(base, TORTOISE, role)
    workdir = str(svc["working_dir"])
    binds = host_binds(svc)
    conf = SERVICE_CONF[role]
    stated = table(TORTOISE).get(conf, {})
    for key, upstream in UPSTREAM_TORTOISE_DIRS[conf].items():
        value = unquoted(stated.get(key, f'"{upstream}"'))
        target = posixpath.normpath(posixpath.join(workdir, value))
        assert target in binds, (
            f"{role} writes {key} = {value!r} into {target}, and nothing binds it out: "
            f"its binds are {binds}"
        )
        assert binds[target] == f"./{posixpath.basename(target)}", binds


def test_the_catalog_states_the_folders_where_upstream_puts_them_so_old_confs_agree() -> None:
    """The table pins each `*Dir` the binds depend on, to upstream's own spelling.

    Stated, so an upstream that moved a default cannot unmoor a bind the install
    wrote; upstream's spelling, so the conf of an install made before T165 -- which
    holds upstream's value, since nothing re-patches a finished install's conf --
    lands in the same folder as a fresh one.
    """
    stated = table(TORTOISE)
    for conf, keys in UPSTREAM_TORTOISE_DIRS.items():
        for key, upstream in keys.items():
            assert unquoted(stated[conf].get(key, "")) == upstream, (conf, key)


def test_tortoisebots_own_log_lands_in_a_bound_folder(tmp_path: Path) -> None:
    """The world printed "Could not open bot log file ../logs/bot_events.csv" (lead, 2026-09-28).

    TortoiseBots opens that path relative to mangosd's working directory, so it
    lands in whatever is bound at `<working_dir>/../logs`.
    """
    svc = service(render_base(TORTOISE, tmp_path), TORTOISE, "mangosd")
    printed = "../logs/bot_events.csv"
    folder = posixpath.dirname(posixpath.normpath(posixpath.join(str(svc["working_dir"]), printed)))
    assert folder in host_binds(svc), (folder, host_binds(svc))


def test_the_new_binds_carry_the_installs_selinux_label(tmp_path: Path) -> None:
    """`:z` on every host bind or on none: T106 refuses a file whose binds disagree (`mixed`)."""
    base = render_base(TORTOISE, tmp_path, label=":z")
    lines = [line.strip() for line in base.splitlines() if line.strip().startswith("- ./")]
    assert any(line.startswith("- ./logs:") for line in lines), lines
    assert composegen.bind_label_of(base) == ":z"
    assert composegen.bind_label_of(render_base(TORTOISE, tmp_path)) == ""


# -- the install makes the folders --------------------------------------------


def test_a_fresh_install_makes_the_folders_before_compose_can(tmp_path: Path) -> None:
    """Made by the install, as the user: a folder Docker makes for a bind is root's on Linux.

    A root-owned `logs/` would hold files the player cannot delete, and an
    uninstall's folder removal would stop on it.
    """
    server_dir = installed(tmp_path, TORTOISE)
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    for name in ("logs", "honor", "pdump"):
        assert f"- ./{name}:" in base, name
        assert (server_dir / name).is_dir(), f"the install wrote a bind for ./{name} and no folder"


def test_a_folder_that_cannot_be_made_stops_the_stage_before_the_compose_is_written(
    tmp_path: Path,
) -> None:
    """A FILE named `logs` is the one input here: the stage refuses and writes no compose file."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    (server_dir / "logs").write_text("not a folder\n", encoding="utf-8")
    with pytest.raises(InstallerError, match="logs"):
        installed_into(server_dir, TORTOISE)
    assert not (server_dir / composegen.BASE_FILE).exists()


def installed_into(server_dir: Path, entry: CatalogEntry) -> None:
    plan = entry.install.password
    assert plan.file is not None
    (server_dir / plan.file).write_text(f"{plan.prefix}{'0' * 16}\n", encoding="utf-8")
    eng = engine(entry)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
        ),
        cancel=None,
        secrets=eng.resolve_secrets(server_dir),
    )
    list(eng.stage_generate_compose(ctx))


# -- an install made before T165: the Repair offer ------------------------------


def before_t165(server_dir: Path) -> str:
    """Put the base file an install made before T165 has in place, and remove its folders.

    The fresh render less exactly the bind lines this ticket adds -- one on realmd,
    three on mangosd -- which is the file every Tortoise install made before it holds.
    """
    path = server_dir / composegen.BASE_FILE
    fresh = path.read_text(encoding="utf-8")
    new = [f"- ./{name}:" for name in ("logs", "honor", "pdump")]
    kept = [line for line in fresh.split("\n") if not line.strip().startswith(tuple(new))]
    assert len(fresh.split("\n")) - len(kept) == 4, "the binds T165 adds are not the four expected"
    path.write_text("\n".join(kept), encoding="utf-8", newline="\n")
    for name in ("logs", "honor", "pdump"):
        (server_dir / name).rmdir()
    return fresh


def test_an_install_from_before_t165_is_offered_the_repair(tmp_path: Path) -> None:
    server_dir = installed(tmp_path, TORTOISE)
    before_t165(server_dir)
    check = engine(TORTOISE).base_compose_check(InstallOptions(server_dir=server_dir))
    assert check.state == "stale", check
    assert (check.added, check.removed) == (4, 0), check


def test_the_repair_binds_the_folders_and_makes_them(tmp_path: Path) -> None:
    server_dir = installed(tmp_path, TORTOISE)
    fresh = before_t165(server_dir)
    # Through the Server tab's own wiring, so the press is the one the player makes.
    # Its engine asks the real host, but the label is read off the file on disk,
    # and the fixture's file carries none.
    route = repair_compose_for_app(TORTOISE, server_dir)
    assert route is not None
    assert route.check().state == "stale"
    done = route.repair()
    assert done.backup is not None
    assert route.check().state == "current"
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == fresh
    for name in ("logs", "honor", "pdump"):
        assert (server_dir / name).is_dir(), f"the repair bound ./{name} and made no folder"


def test_a_repair_keeps_what_is_already_in_the_folders(tmp_path: Path) -> None:
    """A `logs/` somebody made by hand keeps its files: the repair makes, it never empties."""
    server_dir = installed(tmp_path, TORTOISE)
    before_t165(server_dir)
    (server_dir / "logs").mkdir()
    kept = server_dir / "logs" / "server.log"
    kept.write_text("an old line\n", encoding="utf-8")
    engine(TORTOISE).repair_base_compose(InstallOptions(server_dir=server_dir))
    assert kept.read_text(encoding="utf-8") == "an old line\n"


def test_a_refused_repair_makes_no_folder(tmp_path: Path) -> None:
    """A moved install is refused whole (T106): no compose written, and no folder made either."""
    server_dir = installed(tmp_path, TORTOISE)
    before_t165(server_dir)
    moved = tmp_path / "moved"
    server_dir.rename(moved)
    with pytest.raises(InstallerError, match="new project"):
        engine(TORTOISE).repair_base_compose(InstallOptions(server_dir=moved))
    assert not any((moved / name).exists() for name in ("logs", "honor", "pdump"))


def test_a_repair_whose_folder_cannot_be_made_writes_nothing(tmp_path: Path) -> None:
    """A FILE named `logs`: refused with the folder's name, the old file left and no backup made."""
    server_dir = installed(tmp_path, TORTOISE)
    before_t165(server_dir)
    path = server_dir / composegen.BASE_FILE
    old = path.read_bytes()
    (server_dir / "logs").write_text("not a folder\n", encoding="utf-8")
    with pytest.raises(InstallerError, match="logs"):
        engine(TORTOISE).repair_base_compose(InstallOptions(server_dir=server_dir))
    assert path.read_bytes() == old
    assert not list(server_dir.glob(composegen.BASE_FILE + ".*.repair.bak"))


# -- what the rule refuses ------------------------------------------------------


def with_mangosd_key(key: str, value: str) -> CatalogEntry:
    """Tortoise with ONE `mangosd.conf` table value changed."""
    native_block = TORTOISE.install.native
    assert native_block is not None and native_block.cmangos is not None
    conf = native_block.cmangos.conf
    patch = conf.files["mangosd.conf"]
    files = {
        **conf.files,
        "mangosd.conf": patch.model_copy(update={"keys": {**patch.keys, key: value}}),
    }
    cmangos = native_block.cmangos.model_copy(
        update={"conf": conf.model_copy(update={"files": files})}
    )
    install = TORTOISE.install.model_copy(
        update={"native": native_block.model_copy(update={"cmangos": cmangos})}
    )
    return TORTOISE.model_copy(update={"install": install})


def test_an_absolute_setting_is_bound_where_it_points() -> None:
    entry = with_mangosd_key("HonorDir", '"/opt/tortoise/honor"')
    assert composegen.ServerFolder("honor", "/opt/tortoise/honor") in composegen.server_folders(
        entry, "mangosd.conf"
    )


@pytest.mark.parametrize(
    "value",
    ['"../../logs"', '"../logs/old"', '"../data"', '"../bin"', '"../lo gs"'],
    ids=[
        "outside-the-core-prefix",
        "a-folder-inside-a-folder",
        "the-folder-extraction-fills",
        "the-folder-the-binaries-run-from",
        "a-name-yaml-would-read-differently",
    ],
)
def test_a_setting_that_is_not_a_folder_of_the_servers_own_is_refused(value: str) -> None:
    """Each value breaks one rule, and each is refused by name rather than bound."""
    with pytest.raises(composegen.ComposeGenError, match="LogsDir"):
        composegen.server_folders(with_mangosd_key("LogsDir", value))
