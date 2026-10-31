"""T837: a replaced `Data/patch-WB1.MPQ` makes CoA extract its map data again.

The extraction evidence identified a client by `Data/patch-WB2.MPQ` and the
plan, and `surgical.effective_plan()` differs only by layout. Update client
replacing the WB1 archive (or its `.DISABLED` twin) in place with the same size
and date changed neither, so every tool was skipped and stale terrain and map
936 stayed. The extract stage now salts the plan hash with the selected
archive's own bytes (`surgical.archive_identity()`), which is why a replacement
that keeps size and date is still caught, and writes `surgical.SIDECAR_NAME`
after a finished extraction so `coa_client.map_data_sentence()` can name the
archive without reading it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.test_coa_extract import (
    block,
    coa_client,
    coa_produce,
    context,
    engine,
    lay_evidence_maps,
)
from yulon import coa_client as coa_ui
from yulon.catalog.catalog import AzerothCoreData, CatalogEntry, ExtractPlan, load_catalog
from yulon.catalog.families import extract, surgical
from yulon.catalog.installer import InstallerError

MAP_DATA = (0, 1, 530, 571, 936, 937)
"""The maps `missing_map_data()` reads, 936 and 937 included."""


def data_block() -> AzerothCoreData:
    """The AC data block carrying the shipped client_extract."""
    return AzerothCoreData(client_extract=block())


def plan_for(client: Path) -> ExtractPlan:
    """The plan the stage runs for `client`, at the catalog's tools bin."""
    return surgical.effective_plan(data_block(), "/azerothcore/env/dist/bin", client)


def grow(path: Path) -> None:
    """A map archive past one hash chunk, so a difference in the LAST chunk is still tested.

    A hash that reads only the first `surgical.SHA_CHUNK` bytes would call the
    changed file below identical, which is the mutation this size exists for.
    """
    with path.open("wb") as handle:
        handle.write(b"MPQ\x1a")
        handle.truncate(surgical.SHA_CHUNK + 4096)


def replace_tail(path: Path, stat: os.stat_result) -> None:
    """New bytes past the first chunk, and the size and date the file had before."""
    with path.open("r+b") as handle:
        handle.seek(surgical.SHA_CHUNK + 1)
        handle.write(b"\x01")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def server(tmp_path: Path, client: Path) -> Path:
    """A server folder whose `data/` holds the maps the stage's gate demands."""
    folder = tmp_path / "srv"
    folder.mkdir()
    lay_evidence_maps(folder / "data", MAP_DATA)
    return folder


# -- the identity -------------------------------------------------------------------------


def test_the_identity_is_one_line_per_map_with_its_hash(tmp_path: Path) -> None:
    """`<map_id>:<layout>:<sha256>`, sorted by map id: the archive's own bytes, no cache."""
    client = coa_client(tmp_path, "enabled")
    archive = client / "Data" / "patch-WB1.MPQ"
    identity = surgical.archive_identity(data_block(), client)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    expected = ";".join(
        f"{spec.map_id}:enabled:{digest}"
        for spec in sorted(block().surgical_maps, key=lambda one: one.map_id)
    )
    assert identity == expected


def test_a_missing_archive_is_named_and_does_not_crash(tmp_path: Path) -> None:
    """Neither name in the client: the identity says `missing` where the hash would be."""
    identity = surgical.archive_identity(data_block(), coa_client(tmp_path, "none"))
    assert identity != ""
    for spec in block().surgical_maps:
        assert f"{spec.map_id}:missing:missing" in identity, identity


# -- the extract stage --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wb1", "archive"),
    [("enabled", "patch-WB1.MPQ"), ("disabled", "patch-WB1.MPQ.DISABLED")],
)
def test_a_replaced_wb1_with_the_same_size_and_date_extracts_everything_again(
    tmp_path: Path, wb1: str, archive: str
) -> None:
    """The ticket's case: a same-size, same-date replacement re-runs every tool."""
    client = coa_client(tmp_path, wb1)
    path = client / "Data" / archive
    grow(path)
    before = path.stat()
    folder = server(tmp_path, client)
    rec = coa_produce(Recorder())
    said = list(engine(rec)._extract_maps(context(folder, client)))
    plan = plan_for(client)
    first = len(rec.container_runs)
    assert first == len(plan.tools), said

    identity_before = surgical.archive_identity(data_block(), client)
    replace_tail(path, before)
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert surgical.archive_identity(data_block(), client) != identity_before

    again = list(engine(rec)._extract_maps(context(folder, client)))
    assert len(rec.container_runs) == first * 2, again
    assert [run.argv for run in rec.container_runs[first:]] == [tool.argv for tool in plan.tools]
    assert any("extracting everything again" in line for line in again), again


def test_an_unchanged_archive_keeps_every_tool_skipped(tmp_path: Path) -> None:
    """A resume with the archive as it was: the identity agrees, nothing re-runs."""
    client = coa_client(tmp_path, "enabled")
    grow(client / "Data" / "patch-WB1.MPQ")
    folder = server(tmp_path, client)
    rec = coa_produce(Recorder())
    eng = engine(rec)
    list(eng._extract_maps(context(folder, client)))
    first = len(rec.container_runs)
    said = list(eng._extract_maps(context(folder, client)))
    assert len(rec.container_runs) == first, said
    assert any("already extracted" in line for line in said), said


def test_the_stage_says_it_checks_the_archive_before_the_first_tool(tmp_path: Path) -> None:
    """The line is yielded before the hash and before any tool runs, naming the real file."""
    client = coa_client(tmp_path, "disabled")
    folder = server(tmp_path, client)
    rec = coa_produce(Recorder())
    said = list(engine(rec)._extract_maps(context(folder, client)))
    checking = [
        index
        for index, line in enumerate(said)
        if "Checking the map archive patch-WB1.MPQ.DISABLED" in line
    ]
    assert checking, said
    assert "so a changed one is extracted again" in said[checking[0]], said[checking[0]]
    running = next(index for index, line in enumerate(said) if ": running " in line)
    assert checking[0] < running, said


def test_no_sidecar_is_left_when_the_extraction_fails(tmp_path: Path) -> None:
    """A failed extraction records nothing about an archive it did not finish reading."""
    client = coa_client(tmp_path, "enabled")
    folder = server(tmp_path, client)
    rec = Recorder()
    rec.produce = {"dbc": 1, "maps": 1, "Buildings": 1, "vmaps": 1}
    with pytest.raises(InstallerError):
        list(engine(rec)._extract_maps(context(folder, client)))
    assert not (folder / "data" / surgical.SIDECAR_NAME).exists()


UNREADABLE = (
    "Yu'lon could not read patch-WB1.MPQ to check whether it changed "
    "([Errno 13] Permission denied). If the game or another program has it open, close it, "
    "then press it again. Nothing was extracted."
)
"""F1: the refusal an archive that cannot be read earns, word for word."""

CHANGED = (
    "patch-WB1.MPQ changed while the map data was being extracted from it, so that data may be "
    "from the old file. Press it again to extract from the new one."
)
"""F2: the refusal an archive replaced under the tools earns, word for word."""


def test_an_archive_the_stage_cannot_read_refuses_before_any_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1: `unreadable` was reusable evidence; an OSError refuses the press instead."""
    client = coa_client(tmp_path, "enabled")
    folder = server(tmp_path, client)
    rec = coa_produce(Recorder())
    real = surgical._sha256_file
    failing = [True]

    def reader(path: Path) -> str:
        if failing[0]:
            raise OSError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(surgical, "_sha256_file", reader)
    with pytest.raises(InstallerError) as caught:
        list(engine(rec)._extract_maps(context(folder, client)))
    assert str(caught.value) == UNREADABLE
    assert rec.container_runs == []
    assert not (folder / "data" / extract.EVIDENCE_FILE).exists()
    assert not (folder / "data" / surgical.SIDECAR_NAME).exists()

    failing[0] = False
    said = list(engine(rec)._extract_maps(context(folder, client)))
    assert said[-1] == "Extraction finished.", said
    assert len(rec.container_runs) == len(plan_for(client).tools)
    assert (folder / "data" / surgical.SIDECAR_NAME).exists()


def test_an_archive_replaced_during_the_tools_refuses_and_reruns_everything(
    tmp_path: Path,
) -> None:
    """F2: the sidecar and the finished line are claims about the bytes the tools read."""
    client = coa_client(tmp_path, "enabled")
    path = client / "Data" / "patch-WB1.MPQ"
    folder = server(tmp_path, client)
    rec = coa_produce(Recorder())
    run_tool = rec.run_container
    changed: list[bool] = []

    def replace_during_the_tools(spec: object, *, sink: object, cancel: object = None) -> object:
        run = run_tool(spec, sink=sink, cancel=cancel)
        if not changed:
            changed.append(True)
            path.write_bytes(b"MPQ\x1a" * 4)
        return run

    with pytest.raises(InstallerError) as caught:
        list(
            engine(rec, run_container=replace_during_the_tools)._extract_maps(
                context(folder, client)
            )
        )
    assert str(caught.value) == CHANGED
    assert not (folder / "data" / surgical.SIDECAR_NAME).exists()
    written = extract.read_evidence(folder / "data")
    assert written is not None and written.tools, "the tools' records were written"

    first = len(rec.container_runs)
    said = list(engine(rec)._extract_maps(context(folder, client)))
    assert any("extracting everything again" in line for line in said), said
    assert len(rec.container_runs) == first + len(plan_for(client).tools), said
    assert (folder / "data" / surgical.SIDECAR_NAME).exists()


def test_the_sidecar_holds_the_facts_taken_before_the_hash(tmp_path: Path) -> None:
    """An unchanged archive: the sidecar is the stat the hash was taken from."""
    client = coa_client(tmp_path, "enabled")
    path = client / "Data" / "patch-WB1.MPQ"
    grow(path)
    before = path.stat()
    folder = server(tmp_path, client)
    list(engine(coa_produce(Recorder()))._extract_maps(context(folder, client)))
    payload = json.loads((folder / "data" / surgical.SIDECAR_NAME).read_text())
    assert payload == {
        "name": "Data/patch-WB1.MPQ",
        "size": before.st_size,
        "mtime_ns": before.st_mtime_ns,
    }


def test_the_archive_facts_are_measured_before_its_bytes_are_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A date that moves while the hash reads is caught only by a pre-hash stamp."""
    client = coa_client(tmp_path, "enabled")
    path = client / "Data" / "patch-WB1.MPQ"
    grow(path)
    folder = server(tmp_path, client)
    real = surgical._sha256_file

    def hash_then_touch(archive: Path) -> str:
        digest = real(archive)
        os.utime(archive, ns=(1, 1))
        return digest

    monkeypatch.setattr(surgical, "_sha256_file", hash_then_touch)
    with pytest.raises(InstallerError) as caught:
        list(engine(coa_produce(Recorder()))._extract_maps(context(folder, client)))
    assert str(caught.value) == CHANGED
    assert not (folder / "data" / surgical.SIDECAR_NAME).exists()


@pytest.mark.parametrize("platform_id", ["linux", "windows"])
def test_the_identity_is_the_salt_on_every_platform(tmp_path: Path, platform_id: str) -> None:
    """The salt is passed on Linux and on Windows (the T840 scratch path is not a gate)."""
    client = coa_client(tmp_path, "enabled")
    folder = server(tmp_path, client)
    rec = coa_produce(Recorder())
    list(engine(rec, platform_id=lambda: platform_id)._extract_maps(context(folder, client)))
    plan = plan_for(client)
    assert len(rec.container_runs) == len(plan.tools), platform_id
    evidence = extract.read_evidence(folder / "data")
    assert evidence is not None
    identity = surgical.archive_identity(data_block(), client)
    assert evidence.plan_hash == extract.plan_hash(plan, identity), platform_id
    assert evidence.plan_hash != extract.plan_hash(plan), "the salt is not the plan alone"


# -- the movement maps --------------------------------------------------------------------


def test_the_movement_maps_are_made_again_after_a_re_extraction(tmp_path: Path) -> None:
    """A new extraction drops this stage's record, so the old movement maps are rebuilt."""
    client = coa_client(tmp_path, "enabled")
    path = client / "Data" / "patch-WB1.MPQ"
    grow(path)
    before = path.stat()
    folder = server(tmp_path, client)
    source = folder / block().mmaps_config
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("tileProducer: RecastMeshBuilder\n", encoding="utf-8")
    rec = coa_produce(Recorder())
    rec.produce["mmaps"] = 600
    eng = engine(rec)
    list(eng._extract_maps(context(folder, client)))
    list(eng._mmaps(context(folder, client)))
    made = len(rec.container_runs)
    skipped = list(eng._mmaps(context(folder, client)))
    assert len(rec.container_runs) == made, skipped
    assert any("mmaps: already generated" in line for line in skipped), skipped

    replace_tail(path, before)
    list(eng._extract_maps(context(folder, client)))
    extracted = len(rec.container_runs)
    assert extracted > made, "the re-extraction ran its own tools first"
    said = list(eng._mmaps(context(folder, client)))
    assert len(rec.container_runs) == extracted + 1, said
    assert rec.container_runs[-1].argv == tuple(block().mmaps.argv)
    assert any("mmaps: running" in line for line in said), said
    assert not any("already generated" in line for line in said), said


# -- the sentence the UI shows ------------------------------------------------------------


def coa_entry() -> CatalogEntry:
    """The shipped CoA entry."""
    return next(entry for entry in load_catalog().games if entry.id == "wow-coa")


def recorded(tmp_path: Path, client: Path) -> tuple[Path, Path]:
    """A server whose `data/` holds the evidence and the sidecar of a finished extraction."""
    entry = coa_entry()
    folder = tmp_path / "server"
    evidence = extract.expected_evidence(block().extract, client, block().client.required_file)
    extract.write_evidence(folder / "data", evidence)
    _, stamp = surgical.measure_identity(data_block(), client)
    surgical.write_sidecar(client, folder / "data", stamp)
    return entry, folder


def test_a_changed_wb1_with_the_same_name_is_named_by_the_sentence(tmp_path: Path) -> None:
    """Size and date differ from the sidecar: the sentence names the archive, not WB2."""
    client = coa_client(tmp_path, "enabled")
    entry, folder = recorded(tmp_path, client)
    assert coa_ui.map_data_sentence(entry, folder, client) is None
    sidecar = json.loads((folder / "data" / surgical.SIDECAR_NAME).read_text())
    archive = client / "Data" / "patch-WB1.MPQ"
    archive.write_bytes(b"MPQ\x1b")
    os.utime(archive, ns=(sidecar["mtime_ns"] + 10**9, sidecar["mtime_ns"] + 10**9))
    said = coa_ui.map_data_sentence(entry, folder, client)
    assert said is not None
    assert "patch-WB1.MPQ" in said and "extract it again" in said, said


def test_the_sentence_names_the_archive_and_never_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI asks this on every draw: no hash, and the archive itself is not opened."""
    client = coa_client(tmp_path, "enabled")
    entry, folder = recorded(tmp_path, client)
    sidecar = json.loads((folder / "data" / surgical.SIDECAR_NAME).read_text())
    archive = client / "Data" / "patch-WB1.MPQ"

    def no_hash(path: Path) -> str:
        raise AssertionError(f"map_data_sentence hashed {path}; the UI must not read 2 GB")

    opened: list[Path] = []
    real_open = Path.open

    def watch(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(self)
        return real_open(self, *args, **kwargs)

    archive.write_bytes(b"MPQ\x1b")
    os.utime(archive, ns=(sidecar["mtime_ns"] + 10**9, sidecar["mtime_ns"] + 10**9))
    monkeypatch.setattr(surgical, "_sha256_file", no_hash)
    monkeypatch.setattr(Path, "open", watch)
    said = coa_ui.map_data_sentence(entry, folder, client)
    assert said is not None and "patch-WB1.MPQ" in said, said
    assert archive not in opened, opened


def test_a_size_change_alone_is_enough_for_the_sentence(tmp_path: Path) -> None:
    """A bigger archive with the date restored: size is compared too."""
    client = coa_client(tmp_path, "enabled")
    entry, folder = recorded(tmp_path, client)
    sidecar = json.loads((folder / "data" / surgical.SIDECAR_NAME).read_text())
    archive = client / "Data" / "patch-WB1.MPQ"
    archive.write_bytes(b"MPQ\x1a" * 2)
    os.utime(archive, ns=(sidecar["mtime_ns"], sidecar["mtime_ns"]))
    said = coa_ui.map_data_sentence(entry, folder, client)
    assert said is not None and "patch-WB1.MPQ" in said, said


def test_no_sidecar_means_no_sentence(tmp_path: Path) -> None:
    """An install extracted before this release has no sidecar: say nothing."""
    client = coa_client(tmp_path, "enabled")
    entry, folder = recorded(tmp_path, client)
    (folder / "data" / surgical.SIDECAR_NAME).unlink()
    assert coa_ui.map_data_sentence(entry, folder, client) is None
