"""T812 C4b: the 936 surgical tool, the mmaps config, the mmaps progress line.

Map 936 lives only in `Data/patch-WB1.MPQ.DISABLED` (REPORT §4): the main run
never opens it. The extract plan therefore ends with one appended tool
(`surgical.effective_plan()`): `bash -c <script>` running the same three
extractors over a scratch view of the client where the disabled archive is
linked under the name the tools open, copying only this map's files into the
shared `data/`. The mmaps stage lays `data/mmaps-config.yaml` first (the
generator opens that name in its cwd) and says a progress line every 60 s.
Each test is named for the failing case, so a mutation that breaks the rule it
pins turns its test red.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support_bash import bash_available
from tests.support_native import Recorder
from tests.test_coa_extract import block, coa_client, context, engine, lay_evidence_maps
from yulon import docker
from yulon.catalog.catalog import AcSurgicalMap, ExtractPlan, ExtractTool, MmapPlan
from yulon.catalog.families import extract, surgical
from yulon.catalog.installer import InstallerError

needs_bash = pytest.mark.skipif(not bash_available(), reason="no bash that can run a script")


def spec936() -> AcSurgicalMap:
    """The shipped 936 entry of the catalog's surgical list."""
    maps = block().surgical_maps
    assert maps is not None
    return next(spec for spec in maps if spec.map_id == 936)


def ac_block():  # type: ignore[no-untyped-def]
    """The AC data block carrying the shipped client_extract."""
    from yulon.catalog.catalog import AzerothCoreData

    return AzerothCoreData(client_extract=block())


def plan936() -> ExtractPlan:
    """The catalog plan plus the one appended surgical tool, at the tools image's bin."""
    return surgical.effective_plan(ac_block(), "/azerothcore/env/dist/bin")


def _fake_tools(binds: Path, marker: Path) -> None:
    """Stub `map_extractor`/`vmap4_*` on PATH: the script's tools, faked at its scale.

    The doubles write the 936 files the script copies plus 000 decoys the
    script must NOT copy: a fake that wrote only 936 files would pass the
    assertion without exercising the filter. Layout mirrors the real tools:
    `map_extractor -o <dir>` writes `maps/`, the vmap pair writes `vmaps/`
    under the cwd the script chose.
    """
    (binds / "map_extractor").write_text(
        "#!/usr/bin/env bash\n"
        'echo "fake map_extractor $*" >> "$FAKE_MARKER"\n'
        'out=""; prev=""\n'
        'for a in "$@"; do if [ "$prev" = "-o" ]; then out="$a"; fi; prev="$a"; done\n'
        'mkdir -p "$out/maps"\n'
        'if [ "${FAKE_THIN:-0}" = "1" ]; then\n'
        '  touch "$out/maps/9364341.map" "$out/maps/9364342.map" "$out/maps/0000000.map"\n'
        'else\n'
        '  touch "$out/maps/9364341.map" "$out/maps/9364342.map" "$out/maps/9364343.map"\n'
        '  touch "$out/maps/9364441.map" "$out/maps/9364442.map" "$out/maps/9364443.map"\n'
        '  touch "$out/maps/9364541.map" "$out/maps/9364542.map" "$out/maps/9364543.map"\n'
        '  touch "$out/maps/0000000.map"\n'
        'fi\n'
        'echo done\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (binds / "vmap4_extractor").write_text(
        "#!/usr/bin/env bash\n"
        'echo "fake vmap_extractor $*" >> "$FAKE_MARKER"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (binds / "vmap4_assembler").write_text(
        "#!/usr/bin/env bash\n"
        'echo "fake vmap_assembler $*" >> "$FAKE_MARKER"\n'
        'mkdir -p vmaps Buildings\n'
        'touch vmaps/936.vmtree vmaps/936_42_44.vmtile vmaps/936_43_44.vmtile\n'
        "touch vmaps/000.vmtree vmaps/000_00_00.vmtile\n"
        "touch Buildings/Ns_Inquisitorial_Dungeon.wmo.vmo\n"
        'echo "Ok, all done"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    for name in ("map_extractor", "vmap4_extractor", "vmap4_assembler"):
        (binds / name).chmod(0o755)
    marker.touch()


def _client_with_wb1(root: Path) -> Path:
    """A client tree with the disabled archive present (the one thing the farm needs)."""
    client = root / "client"
    (client / "Data").mkdir(parents=True)
    (client / "Data" / "patch-WB1.MPQ.DISABLED").write_bytes(b"MPQ\x1a")
    return client


def _run_script(
    tmp_path: Path, script: str, client: Path, out: Path, *, thin: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run the surgical script with fake tools: `/client` and `/out` are bind mounts' stand-ins.

    The script's absolute paths are the container's (`/client`, `/out`), so
    the test lays symlinks by those names inside a scratch dir and runs the
    script with that dir as cwd: every absolute reference is rewritten to the
    scratch (`/client` -> `$root/client`, `/out` -> `$root/out`), which the
    script never notices because it only ever spells the two roots. No
    `unshare`, no mount, no root: a symlink farm the test owns.
    """
    binds = tmp_path / "bin"
    binds.mkdir(exist_ok=True)
    marker = tmp_path / "marker.txt"
    _fake_tools(binds, marker)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / "client").symlink_to(client, target_is_directory=True)
    (root / "out").symlink_to(out, target_is_directory=True)
    # The tools live on PATH in the test but at an absolute path in the
    # script: rewrite the container path to the fake bindir first.
    rewritten = script.replace("/x/bin", str(tmp_path / "bin"))
    rewritten = rewritten.replace("/client", f"{root}/client").replace("/out", f"{root}/out")
    return subprocess.run(
        ["bash", "-c", rewritten, "yulon-surgical"],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": f"{binds}:/usr/bin:/bin",
            "FAKE_MARKER": str(marker),
            "FAKE_THIN": "1" if thin else "0",
        },
    )


# -- the script ------------------------------------------------------------


@needs_bash
def test_the_script_copies_only_the_named_map_and_vmo(tmp_path: Path) -> None:
    """The script's filter: 936 maps + vmtree + vmtile + the named vmo land in /out, 000 does not."""
    client = _client_with_wb1(tmp_path)
    out = tmp_path / "out"
    (out / "maps").mkdir(parents=True)
    (out / "vmaps").mkdir(parents=True)
    proc = _run_script(tmp_path, surgical.script(spec936(), "/x/bin"), client, out)
    assert proc.returncode == 0, proc.stderr
    assert sorted(path.name for path in (out / "maps").iterdir()) == [
        "9364341.map",
        "9364342.map",
        "9364343.map",
        "9364441.map",
        "9364442.map",
        "9364443.map",
        "9364541.map",
        "9364542.map",
        "9364543.map",
    ]
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == [
        "936.vmtree",
        "936_42_44.vmtile",
        "936_43_44.vmtile",
        "Ns_Inquisitorial_Dungeon.wmo.vmo",
    ]


def test_the_script_never_links_the_disabled_archive_into_the_main_client() -> None:
    """WB1 stays OUT of the main run: tools 0-2 keep their catalog argv, only the appended one is bash."""
    plan = plan936()
    catalog_argv = [tool.argv for tool in block().extract.tools]
    assert [tool.argv for tool in plan.tools[: len(catalog_argv)]] == catalog_argv
    (appended,) = plan.tools[len(catalog_argv) :]
    assert appended.argv[0] == "bash"
    script = appended.argv[2]
    assert 'ln -sfn "/client/Data/patch-WB1.MPQ.DISABLED"' in script
    assert 'cp -rs /client/. "$w/view/"' in script
    for tool in block().extract.tools:
        assert "WB1" not in " ".join(tool.argv) and "surgical" not in tool.name


@needs_bash
def test_too_few_936_maps_fails_the_tool(tmp_path: Path) -> None:
    """The count gate: fewer than `min_maps` maps copied exits 3 and says how many."""
    client = _client_with_wb1(tmp_path)
    out = tmp_path / "out"
    (out / "maps").mkdir(parents=True)
    (out / "vmaps").mkdir(parents=True)
    proc = _run_script(tmp_path, surgical.script(spec936(), "/x/bin"), client, out, thin=True)
    assert proc.returncode == surgical.FAILED_EXIT, (proc.returncode, proc.stdout, proc.stderr)
    assert "at least 9 expected" in proc.stdout + proc.stderr


# -- the mmaps stage ---------------------------------------------------------


def test_mmaps_without_the_config_refuses(tmp_path: Path) -> None:
    """No `mmaps-config.yaml` in the checkout: the stage refuses, naming it, and runs nothing."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    rec = Recorder()
    with pytest.raises(InstallerError, match="mmaps-config.yaml"):
        list(engine(rec)._mmaps(context(server_dir, client)))
    assert rec.container_runs == []


def _lay_config_and_evidence(server_dir: Path) -> None:
    source = server_dir / block().mmaps_config
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("tileProducer: RecastMeshBuilder\n", encoding="utf-8")
    lay_evidence_maps(server_dir / "data", (0, 1, 530, 571, 936, 937))
    plan = block().mmaps
    evidence = extract.expected_evidence(
        ExtractPlan(image="tools", tools=[ExtractTool(name="x", argv=("x",), produces={"maps": 1})]),
        server_dir,
        None,
    )
    extract.write_evidence(server_dir / "data", evidence)


def _drive_mmaps(
    rec: Recorder,
    server_dir: Path,
    client: Path,
    *,
    clock: object = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> list[str]:
    """Drive `_mmaps` with the config laid and evidence present, collecting its lines.

    The stage hardcodes the 60 s cadence; only the clock is seamed (over
    `extract.time.monotonic`), so the test's fake time, not a patched
    argument, drives the progress lines.
    """
    rec.produce.setdefault("mmaps", 600)
    _lay_config_and_evidence(server_dir)
    if clock is not None:
        assert monkeypatch is not None
        monkeypatch.setattr(extract.time, "monotonic", clock)
    return list(engine(rec)._mmaps(context(server_dir, client)))


def test_progress_lines_every_minute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A progress line every 60 s of generator time: the file count so far and the elapsed."""
    from tests.test_coa_extract import coa_produce

    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    rec = coa_produce(Recorder())
    now = [1000.0]

    original = rec.run_container

    def advancing(
        spec: docker.ContainerRun, *, sink: docker.OutputSink, cancel: threading.Event | None = None
    ) -> docker.AttachedRun:
        now[0] += 61.0
        result = original(spec, sink=sink, cancel=cancel)
        now[0] += 61.0
        return result

    rec.run_container = advancing  # type: ignore[method-assign]
    try:
        said = _drive_mmaps(rec, server_dir, client, clock=lambda: now[0], monkeypatch=monkeypatch)
    finally:
        rec.run_container = original  # type: ignore[method-assign]
    progress = [line for line in said if "Movement maps:" in line]
    assert len(progress) >= 1, said
    assert all("files so far" in line and "elapsed" in line for line in progress), said


def test_cmangos_mmaps_output_is_unchanged(tmp_path: Path) -> None:
    """Default cadence is off: `run_mmaps()` without `progress_every` sinks exactly the old lines."""
    from tests.test_extract import FULL, PLAN, Runner, driven

    runner = Runner(FULL)
    list(driven(PLAN, runner, tmp_path))
    data = tmp_path / "server" / "data"

    class SlowRunner:
        """A container double that writes one mmaps file per `sink()` call, 200 calls."""

        def __init__(self) -> None:
            self.calls = 0

        def __call__(
            self, spec: docker.ContainerRun, *, sink: docker.OutputSink, cancel: threading.Event | None
        ) -> docker.AttachedRun:
            out = next(mount.host for mount in spec.mounts if mount.guest == "/out")
            folder = out / "mmaps"
            folder.mkdir(parents=True, exist_ok=True)
            for _ in range(200):
                (folder / f"{self.calls:05d}.mmtile").write_bytes(b"x")
                self.calls += 1
                sink(f"tile {self.calls}")
            return docker.AttachedRun(0, ("Movemap build is complete!",))

    sunk: list[str] = []
    said_default = list(
        extract.run_mmaps(
            MmapPlan(argv=("/opt/bin/MoveMapGen", "--silent"), min_files=3, required=True),
            image_ref="yulon.local/x-server:1",
            data_dir=data,
            run_container=SlowRunner(),  # type: ignore[arg-type]
            user_args=("--user", "1000:1000"),
            sink=sunk.append,
            cancel=None,
        )
    )
    assert not any("Movement maps:" in line for line in sunk), sunk
    assert not any("Movement maps:" in line for line in said_default), said_default
    assert any("mmaps: done" in line for line in said_default)

def test_mmaps_stage_lays_the_config_and_says_progress(tmp_path: Path) -> None:
    """The stage copies the checkout config into `data/` and yields the progress line before the run."""
    from tests.test_coa_extract import coa_produce

    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = coa_client(tmp_path)
    rec = coa_produce(Recorder())
    said = _drive_mmaps(rec, server_dir, client)
    assert (server_dir / "data" / "mmaps-config.yaml").is_file()
    assert any("Making movement maps for the bots" in line for line in said), said
