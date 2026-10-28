"""T812 C4a: the AzerothCore extraction stage (`extract-maps` + `mmaps`).

`wow-coa` extracts its maps from the player's client (its own `client_extract`
block) instead of downloading server data; WotLK and Unbound keep the old
download. Each test is named for the failing case, so a mutation that removes
the rule it pins turns its test red.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

import pytest

from tests.support_native import Recorder
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import AzerothCoreData, CatalogEntry, load_catalog
from yulon.catalog.families import extract, surgical
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError
from yulon.resources import installers_dir

COA_ID = "wow-coa"
WOTLK_ID = "wow-wotlk"
UNBOUND_ID = "wow-unbound"
DB_PASSWORD = "coa-0123456789abcdef"


def coa() -> CatalogEntry:
    """The shipped CoA entry, pinned to Linux the way the family tests pin theirs."""
    entry = load_catalog().get(COA_ID)
    return entry.model_copy(
        update={"install": entry.install.model_copy(update={"platforms": ("linux",)})}
    )


def block() -> object:
    native_block = coa().install.native
    assert native_block is not None and native_block.azerothcore is not None
    assert native_block.azerothcore.client_extract is not None
    return native_block.azerothcore.client_extract


def context(
    server_dir: Path,
    client_dir: Path | None = None,
    *,
    cancel: threading.Event | None = None,
) -> native.StageContext:
    entry = coa()
    return native.StageContext(
        server_dir=server_dir,
        client_dir=client_dir,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="azerothcore",
            completed=(),
        ),
        cancel=cancel,
        secrets=native.Secrets(db_password=DB_PASSWORD),
    )


def engine(
    rec: Recorder, *, entry: CatalogEntry | None = None, **overrides: object
) -> AzerothCoreInstaller:
    chosen = entry if entry is not None else coa()
    return AzerothCoreInstaller(
        chosen,
        installers_root=installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(**{"platform_id": lambda: "linux", **overrides}),
    )


def coa_client(tmp_path: Path) -> Path:
    """A client tree passing the CoA ClientSpec: Data/, the WB2 marker, enough MPQs, a locale."""
    client = tmp_path / "client"
    (client / "Data" / "enUS").mkdir(parents=True)
    (client / "Data" / "patch-WB2.MPQ").write_bytes(b"MPQ\x1a")
    for name in ("common", "expansion", "lichking", "patch", "patch-2", "patch-3"):
        (client / "Data" / f"{name}.MPQ").write_bytes(b"MPQ\x1a")
    (client / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"MPQ\x1a")
    return client


def lay_evidence_maps(data_dir: Path, map_ids: Iterable[int]) -> None:
    """The grid files `missing_map_data()` reads: one `.map` tile and the `.vmtree` per map."""
    (data_dir / "maps").mkdir(parents=True, exist_ok=True)
    (data_dir / "vmaps").mkdir(parents=True, exist_ok=True)
    for map_id in map_ids:
        key = f"{map_id:03}"
        (data_dir / "maps" / f"{key}0000.map").write_bytes(b"map")
        (data_dir / "vmaps" / f"{key}.vmtree").write_bytes(b"vmap")


def coa_produce(rec: Recorder) -> Recorder:
    """The double's output at the CoA plan's scale: counts its `produces` gates accept."""
    rec.produce = {"maps": 9100, "dbc": 370, "Cameras": 1, "Buildings": 1100, "vmaps": 18400}
    return rec


# -- stage tuples ------------------------------------------------------------


def test_wotlk_and_unbound_stage_tuples_are_unchanged() -> None:
    """No `extract-maps` outside a `client_extract` entry; WotLK keeps its tuple."""
    assert AzerothCoreInstaller.STAGE_NAMES == (
        "clone-core",
        "clone-modules",
        "db-password",
        "generate-compose",
        "build",
        "client-data",
        "start-db",
        "import",
        "up",
        "ready",
    )
    wotlk = load_catalog().get(WOTLK_ID)
    assert [stage.name for stage in AzerothCoreInstaller(wotlk).stages()] == list(
        AzerothCoreInstaller.STAGE_NAMES
    )
    unbound = load_catalog().get(UNBOUND_ID)
    unbound_names = [stage.name for stage in AzerothCoreInstaller(unbound).stages()]
    assert "extract-maps" not in unbound_names and "mmaps" not in unbound_names


def test_coa_runs_extract_maps_after_build_and_before_start_db() -> None:
    """The CoA tuple: `extract-maps`/`mmaps` between `client-data` and `start-db`."""
    names = [stage.name for stage in engine(Recorder()).stages()]
    assert names.index("extract-maps") == names.index("build") + 2, names
    assert names[names.index("extract-maps") - 1] == "client-data", names
    assert names[names.index("extract-maps") + 1] == "mmaps", names
    assert names[names.index("mmaps") + 1] == "start-db", names
    recorded = {stage.name: stage.recorded for stage in engine(Recorder()).stages()}
    assert recorded["extract-maps"] is True, "skipped on the evidence, like any extract"
    assert recorded["mmaps"] is False, "a resume must always end with the movement maps made"


# -- the extract run -----------------------------------------------------------


def test_a_resume_with_evidence_skips_the_extract(tmp_path: Path) -> None:
    """Per-tool records vouch for a finished run: nothing re-runs and nothing is refused."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    rec = coa_produce(Recorder())
    lay_evidence_maps(server_dir / "data", (0, 1, 530, 571, 936, 937))
    eng = engine(rec)
    list(eng._extract_maps(context(server_dir, client)))
    first = len(rec.container_runs)
    block_data = block()
    assert block_data is not None
    effective = surgical.effective_plan(
        AzerothCoreData(client_extract=block_data), "/azerothcore/env/dist/bin"
    )
    assert first == len(effective.tools), "a skip is only observable once something ran"
    list(eng._extract_maps(context(server_dir, client)))
    assert len(rec.container_runs) == first, "every tool had a record and its counts; none re-ran"


def test_the_extract_stage_refuses_when_936_is_missing_after_the_run(tmp_path: Path) -> None:
    """The stage-end gate (not the counts): full counts but no 936 grid files still refuses."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    lay_evidence_maps(server_dir / "data", (0, 1, 530, 571, 937))
    rec = coa_produce(Recorder())
    with pytest.raises(InstallerError, match="936"):
        list(engine(rec)._extract_maps(context(server_dir, client)))


def test_missing_936_refuses_the_start(tmp_path: Path) -> None:
    """No 936 (or 937) map data on disk: the server must not start."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    lay_evidence_maps(server_dir / "data", (0, 1, 530, 571, 937))
    refused = engine(Recorder()).start_refusal(server_dir)
    assert refused is not None
    assert "936" in refused, f"the refusal names the first missing map: {refused}"
    assert "Nothing was started" in refused
    lay_evidence_maps(server_dir / "data", (936,))
    assert engine(Recorder()).start_refusal(server_dir) is None


def test_the_extract_refuses_a_shortfall_before_any_start(tmp_path: Path) -> None:
    """A tool that wrote too little refuses inside the stage, not at the world server."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    rec = Recorder()
    rec.produce = {"dbc": 1, "maps": 1, "Buildings": 1, "vmaps": 1}
    with pytest.raises(InstallerError, match="Extraction|shortfall|maps"):
        list(engine(rec)._extract_maps(context(server_dir, client)))


def test_the_extract_stage_names_the_tools_image(tmp_path: Path) -> None:
    """The tools run in the `tools` image the entry's images built, read-only client at /client."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    rec = coa_produce(Recorder())
    lay_evidence_maps(server_dir / "data", (0, 1, 530, 571, 936, 937))
    list(engine(rec)._extract_maps(context(server_dir, client)))
    block_data = block()
    assert block_data is not None
    plan = surgical.effective_plan(
        AzerothCoreData(client_extract=block_data), "/azerothcore/env/dist/bin"
    )
    assert [run.argv for run in rec.container_runs] == [tool.argv for tool in plan.tools]
    for run in rec.container_runs:
        mounts = {m.guest: m for m in run.mounts}
        assert mounts["/client"].host in (client, server_dir / extract.CASE_VIEW_DIR)
        assert mounts["/client"].read_only is True
        assert mounts["/out"].host == server_dir / "data"
        assert run.workdir == "/out"
    expected = engine(rec)._image_ref(context(server_dir, client), block_data.extract.image)
    assert all(run.image == expected for run in rec.container_runs)
    assert expected.startswith("yulon.local/ac-coa-tools:")


def test_the_extract_needs_the_client_folder(tmp_path: Path) -> None:
    """No client dir, no extraction — and nothing ran."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    with pytest.raises(InstallerError, match="client"):
        list(engine(rec)._extract_maps(context(server_dir, None)))
    assert rec.container_runs == []


# -- preflight / the view --------------------------------------------------------


def test_the_client_rules_of_an_azerothcore_entry_are_checked() -> None:
    """`client_spec_of` answers the `client_extract` block; preflight refuses without WB2."""
    entry = coa()
    assert entry.install.native is not None
    spec = preflight.client_spec_of(entry.install.native)
    assert spec is not None
    assert spec.required_file == "Data/patch-WB2.MPQ"
    assert preflight.client_spec_for(entry) is spec
    wotlk = load_catalog().get(WOTLK_ID)
    assert wotlk.install.native is not None
    assert preflight.client_spec_of(wotlk.install.native) is None
    assert preflight.client_spec_for(wotlk) is None


def test_preflight_refuses_a_client_without_patch_wb2(tmp_path: Path) -> None:
    """A stock 3.3.5a client has no WB2 archive: preflight refuses it by name."""
    from yulon.catalog.families import clientdir

    entry = coa()
    spec = preflight.client_spec_for(entry)
    assert spec is not None
    bare = tmp_path / "bare"
    (bare / "Data" / "enUS").mkdir(parents=True)
    (bare / "Data" / "patch.MPQ").write_bytes(b"MPQ\x1a")
    (bare / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"MPQ\x1a")
    checks = clientdir.validate(bare, spec, free_bytes=lambda path: None)
    refused = [check for check in checks if check.verdict == "refuse"]
    assert refused, "a client without the WB2 marker must be refused"
    assert any("patch-WB2.MPQ" in check.detail for check in refused)


def test_an_unbound_client_is_not_judged_by_wb2(tmp_path: Path) -> None:
    """Unbound's own rules still decide Unbound's folder: no WB2 question anywhere."""

    entry = load_catalog().get(UNBOUND_ID)
    spec = preflight.client_spec_for(entry)
    assert spec is None, "Unbound has no client_extract and no built client block"
