"""T62: a manifest's `server_dbc` step reaches the server, and a remove says what it left.

Until T62 nothing implemented `apply.DbcCopier`, so the WotLK Modules tab reported
every `server_dbc` step skipped — `mod-arac`'s race/class DBCs and the Season of
Discovery keg's spells never left the clone. The tests here drive the applier the
app itself builds (`ControllerServices.for_wotlk()`), not a copier constructed in
the test, because the defect was never in a copier: it was that nothing called one.

What stands in for Docker is as thin as it can be made. `subprocess.Popen` is
replaced by a function that checks the `docker compose run` argv and then runs
THAT ARGV'S OWN shell script with a real `sh`, pointing the container's data path
at a directory in `tmp_path`. So the bytes travel through the same pipe pump, the
same script and the same rename the real container runs — only the daemon is
missing. Nothing here can reach the real docker CLI: the double refuses any argv
that is not the one compose run it expects, and `platform.docker_prefix` is
pinned so the CLI is never even resolved.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from yulon import docker
from yulon.apply import ApplyError, ComposeDbc
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.git import CloneSpec
from yulon.manifest import Manifest
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, _format_report

WOTLK = load_catalog().get("wow-wotlk")
DATA = wotlk_modules.SERVER_DATA_DIR
SERVICE = "ac-client-data-init"
_REAL_POPEN = subprocess.Popen

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="the double runs a real sh")


def _manifest(rel: str) -> Manifest:
    return wotlk_modules.load_module(wotlk_modules.BUNDLED_MANIFESTS_DIR / "wow-wotlk" / rel)


ARAC = "modules/mod-arac.json"
SOD = "kegs/sod.json"

ARAC_DBCS = ("CharBaseInfo.dbc", "CharStartOutfit.dbc", "SkillRaceClassInfo.dbc")


class _CloneFromManifest:
    """A `Git` that fills the clone with a file for every path the manifest names.

    Each DBC gets content unique to its name, so a copy that wrote the wrong file
    to the wrong name — or the same bytes to all three — cannot pass.
    """

    def __init__(self, manifest: Manifest, dbc_names: tuple[str, ...]) -> None:
        self.manifest = manifest
        self.dbc_names = dbc_names

    def clone(self, spec: CloneSpec) -> None:
        dest = spec.dest
        for step in self.manifest.server_dbc:
            for name in self.dbc_names:
                _write(dest / step.src / name, _dbc_bytes(name))
            _write(dest / step.src / "README.txt", b"not a dbc, must not be copied")
        for client in self.manifest.client:
            if Path(client.src).suffix:
                _write(dest / client.src, b"MPQ\x1a" + client.src.encode())
            else:
                _write(dest / client.src / "patch-Z.MPQ", b"MPQ\x1a")
        for step in self.manifest.deploy:
            _write(dest / step.src, b"-- lua\n")
        for sql in self.manifest.sql:
            if sql.path is not None:
                _write(dest / sql.path, b"SELECT 1;\n")
        (dest / ".git").mkdir(parents=True, exist_ok=True)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _dbc_bytes(name: str) -> bytes:
    # Binary on purpose, NUL and CR/LF included: a text-mode pipe anywhere on the
    # route would mangle these and the equality below would say so.
    return b"WDBC\x00\r\n\xff" + name.encode() + b"\x00" * 7


class _FakeSql:
    def __init__(self) -> None:
        self.files: list[tuple[str, str]] = []

    def run_file(self, db: str, path: Path) -> None:
        self.files.append((db, path.name))

    def run_statement(self, db: str, statement: str) -> None:
        raise AssertionError("no manifest here runs an inline statement")


def _volume(tmp_path: Path) -> Path:
    """The data volume as the client-data download left it: `dbc/` with the stock files."""
    volume = tmp_path / "volume"
    (volume / "dbc").mkdir(parents=True)
    for name in ARAC_DBCS:
        (volume / "dbc" / name).write_bytes(b"stock " + name.encode())
    (volume / "dbc" / "Spell.dbc").write_bytes(b"stock Spell.dbc")
    return volume


def _compose_run_double(
    monkeypatch: pytest.MonkeyPatch, volume: Path, *, prefix: tuple[str, ...] = ("docker",)
) -> tuple[list[dict[str, Any]], list[tuple[str | None, str | None]]]:
    """Replace the docker CLI with the argv's own script run by a real `sh` over `volume`."""
    calls: list[dict[str, Any]] = []
    prefixes: list[tuple[str | None, str | None]] = []

    def docker_prefix(
        wsl_distro: str | None = None, *, inside: str | None = None
    ) -> tuple[str, ...]:
        prefixes.append((wsl_distro, inside))
        return prefix

    def popen(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        calls.append({"command": list(command), "cwd": kwargs.get("cwd")})
        head = [*prefix, "compose", "run", "--rm", "--no-deps", "-T", "--entrypoint", "sh"]
        assert command[: len(head)] == head, f"not the compose run this route sends: {command}"
        service, *argv = command[len(head) :]
        assert service == SERVICE, command
        assert argv[:1] == ["-c"] and argv[2] == "sh" and len(argv) == 4, command
        dest = argv[3]
        assert dest.startswith(DATA + "/dbc/"), dest
        mapped = str(volume) + dest[len(DATA) :]
        return _REAL_POPEN(
            ["sh", "-c", argv[1], "sh", mapped],
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
        )

    monkeypatch.setattr(docker.platform, "docker_prefix", docker_prefix)
    monkeypatch.setattr(docker.subprocess, "Popen", popen)
    return calls, prefixes


def _the_app_s_applier(
    monkeypatch: pytest.MonkeyPatch,
    server_dir: Path,
    client_dir: Path,
    manifest: Manifest,
    wsl_distro: str | None = None,
) -> tuple[Any, _FakeSql]:
    """The Modules tab's applier, from the factory the app runs, with git and SQL faked.

    Only the two seams that would clone from GitHub or write to a database are
    replaced. The DBC copier is whatever `for_wotlk()` put there — which is the
    thing under test.
    """
    monkeypatch.setattr(docker, "world_running", lambda *_a, **_k: False)
    monkeypatch.setattr(docker, "start_database", lambda *_a, **_k: False)
    # T69 (#182) turned a manifest's `requires` into a refusal raised before
    # anything is written, and the Season of Discovery keg requires `mod-ale`.
    # `missing_requirements()` asks the DISK, so a folder under the clone
    # directory is the whole of it -- the same stand-in `_have_requirements()`
    # in `test_apply.py` puts there. These tests are about the DBC copy and the
    # client receipts, not about that guard.
    for needed in manifest.requires:
        (server_dir / "modules" / needed / ".git").mkdir(parents=True, exist_ok=True)
    services = ControllerServices.for_wotlk(WOTLK, server_dir, client_dir, wsl_distro)
    applier = services.applier
    assert applier is not None
    sql = _FakeSql()
    applier.sql = sql
    applier.git = _CloneFromManifest(manifest, ARAC_DBCS)  # type: ignore[assignment]
    return applier, sql


@pytest.mark.parametrize("rel", [ARAC, SOD])
def test_the_app_s_own_applier_puts_the_dbcs_into_the_data_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rel: str
) -> None:
    """The arrival test: Install on the Modules tab's applier, and the files are in `dbc/`.

    Catches the T62 defect itself — `for_wotlk()` building the applier with no
    `dbc=` — which reports `server_dbc … : no DBC copier configured` and leaves
    the stock files in place, failing the byte comparison below.
    """
    manifest = _manifest(rel)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    (client_dir / "Data").mkdir(parents=True)
    volume = _volume(tmp_path)
    calls, _ = _compose_run_double(monkeypatch, volume)
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)

    report = applier.install(manifest)

    for name in ARAC_DBCS:
        assert (volume / "dbc" / name).read_bytes() == _dbc_bytes(name), name
    assert (volume / "dbc" / "Spell.dbc").read_bytes() == b"stock Spell.dbc", "touched a stranger"
    assert not (volume / "dbc" / "README.txt").exists(), "copied a file that is not a DBC"
    assert sorted(p.name for p in (volume / "dbc").iterdir()) == sorted(
        [*ARAC_DBCS, "Spell.dbc"]
    ), "a .yulon-part file was left behind"
    assert [c["cwd"] for c in calls] == [server_dir] * len(ARAC_DBCS)
    step = manifest.server_dbc[0].src
    assert f"server_dbc {step} → data/dbc/" in report.done
    assert not any("server_dbc" in s for s in report.skipped), report.skipped
    assert report.restart_recommended
    assert "Press Stop and then Start on the Server tab" in _format_report(report)


def test_arac_s_client_patch_and_sql_go_where_they_go_beside_the_dbcs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other two ARAC steps in the same pass, so the three halves are one install."""
    manifest = _manifest(ARAC)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    (client_dir / "Data").mkdir(parents=True)
    _compose_run_double(monkeypatch, _volume(tmp_path))
    applier, sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)

    report = applier.install(manifest)

    assert (client_dir / "Data" / "Patch-A.MPQ").read_bytes() == b"MPQ\x1aPatch-A.MPQ"
    assert sql.files == [("world", "arac.sql")]
    assert report.skipped == ()


def test_a_wsl_install_copies_through_its_own_distro_from_its_linux_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The distro reaches the copier, and compose runs in the distro's spelling of the folder.

    Asked through the applier `for_wotlk()` builds for a distro install, so a
    factory that dropped `wsl_distro` on the way to the copier fails here: the
    local daemon would be asked about a project it has never heard of.
    """
    manifest = _manifest(ARAC)
    server_dir = Path("\\\\wsl.localhost\\dml-arch\\home\\pk\\wow-server-playerbots")
    clone_root = tmp_path / "clone"
    volume = _volume(tmp_path)
    wsl = ("wsl", "-d", "dml-arch", "--cd", "/home/pk/wow-server-playerbots", "--", "docker")
    calls, prefixes = _compose_run_double(monkeypatch, volume, prefix=wsl)
    monkeypatch.setattr(docker, "world_running", lambda *_a, **_k: False)
    services = ControllerServices.for_wotlk(WOTLK, server_dir, None, "dml-arch")
    assert services.applier is not None and services.applier.dbc is not None

    src = clone_root / manifest.server_dbc[0].src
    for name in ARAC_DBCS:
        _write(src / name, _dbc_bytes(name))
    services.applier.dbc.copy_dbc_dir(src)

    assert prefixes == [("dml-arch", "/home/pk/wow-server-playerbots")] * len(ARAC_DBCS)
    assert [c["cwd"] for c in calls] == [None] * len(ARAC_DBCS), "a Windows cwd crossed into WSL"
    for name in ARAC_DBCS:
        assert (volume / "dbc" / name).read_bytes() == _dbc_bytes(name)


def test_a_distro_folder_with_no_linux_spelling_is_refused_before_docker_is_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Compose would otherwise run in the distro's home directory — some other project."""
    calls, prefixes = _compose_run_double(monkeypatch, _volume(tmp_path))
    src = tmp_path / "dbc"
    _write(src / "CharBaseInfo.dbc", b"x")
    copier = ComposeDbc(tmp_path / "server", SERVICE, DATA, wsl_distro="dml-arch")

    with pytest.raises(ApplyError, match="is not a path inside the dml-arch distro"):
        copier.copy_dbc_dir(src)
    assert calls == [] and prefixes == []


def test_a_volume_with_no_dbc_folder_is_a_failure_not_a_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No `mkdir -p`: a data volume without `dbc/` holds no server data to patch.

    The failure is the real script's own, surfaced whole — the double runs it.
    """
    empty = tmp_path / "empty-volume"
    empty.mkdir()
    _compose_run_double(monkeypatch, empty)
    src = tmp_path / "dbc"
    _write(src / "CharBaseInfo.dbc", b"x")
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    with pytest.raises(ApplyError, match="could not copy CharBaseInfo.dbc into the server's data"):
        ComposeDbc(server_dir, SERVICE, DATA).copy_dbc_dir(src)
    assert not (empty / "dbc").exists(), "the copy invented a dbc/ folder"


@pytest.mark.parametrize(
    ("make", "refusal"),
    [
        (lambda src: None, "server DBC folder missing in clone"),
        (lambda src: _write(src / "notes.txt", b"x"), "no .dbc files in"),
    ],
)
def test_nothing_to_copy_is_refused_rather_than_reported_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make: Callable[[Path], None],
    refusal: str,
) -> None:
    """`_dbc()` writes its done line after the copier returns, so returning is a claim."""
    calls, _ = _compose_run_double(monkeypatch, _volume(tmp_path))
    src = tmp_path / "clone" / "patch-contents" / "DBFilesContent"
    make(src)
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    with pytest.raises(ApplyError, match=refusal):
        ComposeDbc(server_dir, SERVICE, DATA).copy_dbc_dir(src)
    assert calls == []


# ------------------------------------------------------------------ remove


def test_removing_arac_names_what_it_did_not_take_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`rm -r modules/mod-arac` alone would read as a clean uninstall; it is not one.

    The manifest's own note says so ("Removing the clone does NOT revert the
    SQL/DBC/MPQ"), and the report the user reads has to agree with it.

    Since T67 the MPQ is the one of the three that IS taken back, so it is named
    in `done` here rather than in `left_behind`; T67's own tests cover the arms
    where it is not. The DBCs and the SQL are still nobody's to undo.
    """
    manifest = _manifest(ARAC)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    (client_dir / "Data").mkdir(parents=True)
    volume = _volume(tmp_path)
    _compose_run_double(monkeypatch, volume)
    applier, _sql = _the_app_s_applier(monkeypatch, server_dir, client_dir, manifest)
    applier.install(manifest)

    report = applier.remove(manifest)

    assert report.left_behind == (
        "the server DBC files from patch-contents/DBFilesContent (in the server's data volume)",
        "what data/sql/db-world/arac.sql wrote into the world database",
    )
    text = _format_report(report)
    assert "Removing mod-arac did not undo everything it installed" in text
    assert "the server DBC files from patch-contents/DBFilesContent" in text
    # And the report is telling the truth about the machine, not only about itself.
    assert (volume / "dbc" / "CharBaseInfo.dbc").read_bytes() == _dbc_bytes("CharBaseInfo.dbc")
    # T67: the one step of the three that IS undone, in `done` and on disk.
    assert f"took back Patch-A.MPQ from {client_dir / 'Data'}" in report.done
    assert not (client_dir / "Data" / "Patch-A.MPQ").exists()


def test_a_remove_that_leaves_nothing_behind_says_nothing_about_it(tmp_path: Path) -> None:
    """The line is about the steps a remove cannot reach; a manifest with none gets none."""
    manifest = Manifest.model_validate(
        {
            "id": "mod-plain",
            "name": "Plain",
            "type": "module",
            "game": "wow-wotlk",
            "source": {"repo": "azerothcore/mod-plain"},
            "sql": [{"db": "world", "path": "data/sql/db-world/*.sql", "applied_by": "db-import"}],
        }
    )
    report = controller_view_module.Applier(tmp_path).remove(manifest)
    assert report.left_behind == ()
    assert "did not undo" not in _format_report(report)
