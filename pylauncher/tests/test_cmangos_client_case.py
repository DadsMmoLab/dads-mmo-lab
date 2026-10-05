"""T260: CMaNGOS map extraction reads a client whose archives are named in another case.

The CMaNGOS tools (TBC, Vanilla, Tortoise) open the client's archives by fixed
names -- `Data/common.MPQ`, `Data/enUS/locale-enUS.MPQ` -- and the client is the
PLAYER'S folder, mounted read-only. On a disk that tells cases apart a client
holding `Data/common.mpq` gave them nothing to read. So the extraction reads it
through a view: a folder of links carrying the names the tools open, pointing at
the player's files, mounted where the client was. The player's files are never
renamed, moved or changed, and their read-only flags stay (T196/T198).

The names each tool opens are the pinned sources' (fetched by SHA, 2026-10-05):
`contrib/extractor/System.cpp` and `contrib/vmap_extractor/vmapextract/
vmapexport.cpp` at mangos-tbc 75f9ae68 and mangos-classic 8ec338a1, and
`tools/extractor/System.cpp` / `tools/vmap_extractor/vmapextract/vmapexport.cpp`
at tortoise-wow 187af788. Every one is a lower-case stem, the locale as `enUS`,
and `.MPQ`.

The run is the real `CmangosInstaller._extract()` over a real client folder on
the temp disk; only `docker run` is a double, and it opens the archives the way
a process inside the container would: through the mounts, following each link
inside the container's own paths, every name matched exactly.
"""

from __future__ import annotations

import os
import posixpath
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pytest

from tests.support_case import needs_case_sensitive_disk
from tests.support_native import Recorder
from tests.test_families_cmangos import context, engine, installable
from yulon import docker
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.families import extract
from yulon.catalog.installer import InstallerError

TBC = installable(load_catalog().get("wow-tbc"))
VANILLA = installable(load_catalog().get("wow-vanilla"))
TORTOISE = installable(load_catalog().get("wow-tortoise"))

TBC_CLIENT: Mapping[str, str] = {
    # what the tool opens (System.cpp:84-92, :986-996, :1031; vmapexport.cpp:384-417)
    # -> what the player's client holds
    "Data/common.MPQ": "Data/common.mpq",
    "Data/expansion.MPQ": "Data/expansion.mpq",
    "Data/patch.MPQ": "Data/Patch.MPQ",
    "Data/patch-2.MPQ": "Data/patch-2.mpq",
    "Data/enUS/locale-enUS.MPQ": "Data/enus/locale-enus.mpq",
    "Data/enUS/expansion-locale-enUS.MPQ": "Data/enus/expansion-locale-enus.mpq",
    "Data/enUS/patch-enUS.MPQ": "Data/enus/PATCH-ENUS.MPQ",
    "Data/enUS/patch-enUS-2.MPQ": "Data/enus/patch-enus-2.mpq",
}
VANILLA_CLIENT: Mapping[str, str] = {
    # System.cpp:100-107; vmapexport.cpp:353-374
    "Data/dbc.MPQ": "Data/dbc.mpq",
    "Data/model.MPQ": "Data/model.mpq",
    "Data/terrain.MPQ": "Data/Terrain.MPQ",
    "Data/texture.MPQ": "Data/texture.mpq",
    "Data/wmo.MPQ": "Data/wmo.mpq",
    "Data/base.MPQ": "Data/base.mpq",
    "Data/patch.MPQ": "Data/patch.mpq",
    "Data/patch-2.MPQ": "Data/patch-2.mpq",
}
TORTOISE_CLIENT: Mapping[str, str] = {
    # System.cpp:100-113, which tries `.mpq` in any case but never another stem case
    "Data/dbc.MPQ": "Data/DBC.MPQ",
    "Data/terrain.MPQ": "Data/Terrain.mpq",
    "Data/model.MPQ": "Data/model.mpq",
    "Data/patch.MPQ": "Data/patch.MPQ",
    "Data/patch-3.MPQ": "Data/Patch-3.mpq",
}
GAMES = [
    pytest.param(TBC, TBC_CLIENT, id="tbc"),
    pytest.param(VANILLA, VANILLA_CLIENT, id="vanilla"),
    pytest.param(TORTOISE, TORTOISE_CLIENT, id="tortoise"),
]


def players_client(tmp_path: Path, names: Mapping[str, str]) -> Path:
    """A client folder holding `names`' values, each with its own bytes; one is read-only."""
    client = tmp_path / "client"
    for theirs in names.values():
        path = client / theirs
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"MPQ\x1a {theirs}".encode())
    (client / "Wow.exe").write_bytes(b"MZ")
    (client / "WTF").mkdir()
    (client / "WTF" / "Config.wtf").write_text('SET realmList "logon.example"\n')
    os.chmod(client / next(iter(names.values())), 0o444)
    return client


def snapshot(folder: Path) -> dict[str, tuple[bytes | None, int, int]]:
    """Every entry under `folder`, links not followed: bytes, mtime and mode."""
    found: dict[str, tuple[bytes | None, int, int]] = {}
    for path in sorted(folder.rglob("*")):
        info = path.lstat()
        body = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        found[path.relative_to(folder).as_posix()] = (body, info.st_mtime_ns, info.st_mode)
    return found


def in_container(spec: docker.ContainerRun, guest: str) -> Path | None:
    """Where `guest` lands on the host for a process inside this container; None: not there.

    Through the mounts, longest guest path first; every component matched by its
    exact name, which is what `fopen()` asks a case-sensitive disk; and a link
    followed by its target read as a path INSIDE the container, which is how the
    kernel resolves it there. A relative target would resolve against the view's
    own folder, so one is refused rather than guessed at.
    """
    mounts = sorted(spec.mounts, key=lambda mount: len(mount.guest), reverse=True)
    for _hop in range(40):
        mount = next(
            (m for m in mounts if guest == m.guest or guest.startswith(m.guest + "/")), None
        )
        if mount is None:
            return None
        rest = PurePosixPath(guest[len(mount.guest) :].lstrip("/")).parts
        here = mount.host
        for index, part in enumerate(rest):
            if part not in os.listdir(here):
                return None
            here = here / part
            if here.is_symlink():
                target = os.readlink(here)
                assert target.startswith("/"), f"{here} -> {target} is not a container path"
                guest = posixpath.join(target, *rest[index + 1 :])
                break
        else:
            return here
    raise AssertionError(f"{guest}: more than 40 links")


@dataclass
class Opens:
    """`docker run` that opens the archives each tool opens, then runs as the Recorder runs."""

    rec: Recorder
    names: Mapping[str, str]
    read: dict[tuple[str, str], bytes | None] = field(default_factory=dict)
    specs: list[docker.ContainerRun] = field(default_factory=list)

    def __call__(
        self,
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        self.specs.append(spec)
        program = spec.argv[0].rsplit("/", 1)[-1]
        for retail in self.names:
            found = in_container(spec, f"{extract.CLIENT_MOUNT}/{retail}")
            self.read[(program, retail)] = None if found is None else found.read_bytes()
        return self.rec.run_container(spec, sink=sink, cancel=cancel)


@needs_case_sensitive_disk
@pytest.mark.parametrize(("entry", "names"), GAMES)
def test_every_tool_opens_the_players_archives_by_the_names_it_asks_for(
    tmp_path: Path, entry: CatalogEntry, names: Mapping[str, str]
) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = players_client(tmp_path, names)
    before = snapshot(client)
    rec = Recorder()
    opens = Opens(rec, names)
    said = list(engine(rec, entry=entry, run_container=opens)._extract(context(server_dir, client)))

    tools = [spec.argv[0].rsplit("/", 1)[-1] for spec in opens.specs]
    assert len(tools) == 3
    for program in tools:
        for retail, theirs in names.items():
            assert (
                opens.read[(program, retail)] == (client / theirs).read_bytes()
            ), f"{program} opening {retail}"
    assert snapshot(client) == before, "the player's client: not a name, byte, date or mode"
    assert not (server_dir / extract.CASE_VIEW_DIR).exists(), "the view goes with the run"
    evidence = extract.read_evidence(server_dir / "data")
    assert evidence is not None and evidence.client_path == str(client.resolve())
    assert any("named in another case" in line for line in said)


@needs_case_sensitive_disk
def test_the_view_and_the_players_client_are_both_mounted_read_only(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = players_client(tmp_path, TBC_CLIENT)
    rec = Recorder()
    opens = Opens(rec, TBC_CLIENT)
    list(engine(rec, run_container=opens)._extract(context(server_dir, client)))
    for spec in opens.specs:
        mounts = {mount.guest: mount for mount in spec.mounts}
        assert set(mounts) == {extract.CLIENT_MOUNT, extract.CLIENT_FILES_MOUNT, extract.OUT_MOUNT}
        assert mounts[extract.CLIENT_MOUNT].host == server_dir / extract.CASE_VIEW_DIR
        assert mounts[extract.CLIENT_FILES_MOUNT].host == client
        assert (
            mounts[extract.CLIENT_MOUNT].read_only and mounts[extract.CLIENT_FILES_MOUNT].read_only
        )


def test_a_client_named_as_the_tools_open_it_is_mounted_as_it_is(tmp_path: Path) -> None:
    """No view when every name already reaches its file: the mounts of every earlier install."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = players_client(tmp_path, {retail: retail for retail in TBC_CLIENT})
    rec = Recorder()
    opens = Opens(rec, {retail: retail for retail in TBC_CLIENT})
    said = list(engine(rec, run_container=opens)._extract(context(server_dir, client)))
    for spec in opens.specs:
        assert {mount.guest: mount.host for mount in spec.mounts} == {
            extract.CLIENT_MOUNT: client,
            extract.OUT_MOUNT: server_dir / "data",
        }
    assert not any("named in another case" in line for line in said)


@needs_case_sensitive_disk
def test_a_failed_extraction_takes_the_view_with_it(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = players_client(tmp_path, TBC_CLIENT)
    rec = Recorder()
    rec.run_result = docker.AttachedRun(1, ("cannot open Data",))
    with pytest.raises(InstallerError):
        list(engine(rec)._extract(context(server_dir, client)))
    assert not (server_dir / extract.CASE_VIEW_DIR).exists()


@needs_case_sensitive_disk
def test_a_view_left_by_a_crash_is_replaced_not_reused(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    stale = server_dir / extract.CASE_VIEW_DIR / "Data"
    stale.mkdir(parents=True)
    (stale / "common.MPQ").symlink_to("/client-files/somewhere-else.mpq")
    client = players_client(tmp_path, TBC_CLIENT)
    rec = Recorder()
    opens = Opens(rec, TBC_CLIENT)
    list(engine(rec, run_container=opens)._extract(context(server_dir, client)))
    assert opens.read[("ad", "Data/common.MPQ")] == (client / "Data/common.mpq").read_bytes()


# -- the view itself ------------------------------------------------------------------


@needs_case_sensitive_disk
def test_the_spelling_the_tools_ask_for_wins_over_another_case_of_it(tmp_path: Path) -> None:
    """Two names that differ only in case are two files here; the exact one is the one meant."""
    client = tmp_path / "client"
    (client / "Data").mkdir(parents=True)
    (client / "DATA").mkdir()
    (client / "Data" / "patch-2.MPQ").write_bytes(b"exact")
    (client / "Data" / "Patch-2.mpq").write_bytes(b"other, and first in name order")
    (client / "Data" / "common.mpq").write_bytes(b"common")
    view = tmp_path / "view"
    assert extract.lay_case_view(client, view)
    assert os.readlink(view / "Data" / "patch-2.MPQ") == "/client-files/Data/patch-2.MPQ"
    assert os.readlink(view / "Data" / "Patch-2.mpq") == "/client-files/Data/Patch-2.mpq"
    assert os.readlink(view / "Data" / "common.MPQ") == "/client-files/Data/common.mpq"
    assert os.readlink(view / "DATA") == "/client-files/DATA", "the other Data is only a link"


@needs_case_sensitive_disk
def test_a_lowercase_data_folder_and_locale_folder_get_the_tools_spelling(tmp_path: Path) -> None:
    client = tmp_path / "client"
    (client / "data" / "enus").mkdir(parents=True)
    (client / "data" / "enus" / "locale-enus.mpq").write_bytes(b"locale")
    (client / "data" / "enus" / "realmlist.wtf").write_text("set realmlist x\n")
    (client / "Interface").mkdir()
    view = tmp_path / "view"
    assert extract.lay_case_view(client, view)
    assert os.readlink(view / "Data" / "enUS" / "locale-enUS.MPQ") == (
        "/client-files/data/enus/locale-enus.mpq"
    )
    assert os.readlink(view / "Data" / "enUS" / "realmlist.wtf") == (
        "/client-files/data/enus/realmlist.wtf"
    ), "a file that is no archive keeps its own name"
    assert os.readlink(view / "Interface") == "/client-files/Interface"


def test_no_view_is_laid_on_a_disk_that_ignores_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows and macOS: `Data/common.MPQ` opens `Data/common.mpq`, so the tools need no view.

    The disk is played by `os.path.lexists` answering whatever the case, which
    is the one question the view asks of it beyond listing a folder.
    """
    client = tmp_path / "client"
    (client / "Data").mkdir(parents=True)
    (client / "Data" / "common.mpq").write_bytes(b"common")
    real = os.path.lexists

    def ignoring_case(path: str | os.PathLike[str]) -> bool:
        folder, name = os.path.split(os.fspath(path))
        return real(path) or any(n.casefold() == name.casefold() for n in os.listdir(folder))

    monkeypatch.setattr(extract.os.path, "lexists", ignoring_case)
    view = tmp_path / "view"
    assert not extract.lay_case_view(client, view)
    assert not view.exists()


def test_no_view_is_laid_when_every_name_already_reaches_its_file(tmp_path: Path) -> None:
    client = tmp_path / "client"
    (client / "Data" / "enUS").mkdir(parents=True)
    (client / "Data" / "common.MPQ").write_bytes(b"common")
    (client / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"locale")
    view = tmp_path / "view"
    assert not extract.lay_case_view(client, view)
    assert not view.exists()


@needs_case_sensitive_disk
def test_a_view_that_cannot_be_made_refuses_naming_the_rename_that_would_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = players_client(tmp_path, TBC_CLIENT)

    def refuse(self: Path, target: str, target_is_directory: bool = False) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "symlink_to", refuse)
    rec = Recorder()
    with pytest.raises(InstallerError) as refused:
        list(engine(rec)._extract(context(server_dir, client)))
    message = str(refused.value)
    assert "Data/common.mpq" in message and "Data/common.MPQ" in message
    assert "Nothing was extracted" in message
    assert rec.container_runs == []


def test_a_client_folder_that_is_not_there_is_not_blamed_on_case(tmp_path: Path) -> None:
    """No view for a folder that cannot be listed; the run reports it as it always has."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    said = list(engine(rec)._extract(context(server_dir, tmp_path / "gone")))
    assert not any("another case" in line for line in said)
    assert not (server_dir / extract.CASE_VIEW_DIR).exists()


@needs_case_sensitive_disk
def test_a_lowercase_data_folder_alone_is_named_in_the_line(tmp_path: Path) -> None:
    client = tmp_path / "client"
    (client / "data").mkdir(parents=True)
    (client / "data" / "common.MPQ").write_bytes(b"common")
    assert extract.renamed_in_view(client) == [("data", "Data")]


@needs_case_sensitive_disk
def test_an_extraction_closed_at_the_line_about_the_view_takes_the_view_with_it(
    tmp_path: Path,
) -> None:
    """Codex review: the view is the stage's to remove from the moment it is laid."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = players_client(tmp_path, TBC_CLIENT)
    stage = engine(Recorder())._extract(context(server_dir, client))
    for line in stage:
        if "named in another case" in line:
            break
    assert (server_dir / extract.CASE_VIEW_DIR).is_dir()
    stage.close()
    assert not (server_dir / extract.CASE_VIEW_DIR).exists()
