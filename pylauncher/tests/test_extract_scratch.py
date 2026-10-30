"""T840: on Docker Desktop the extraction tools write inside their container, not on the bind.

`data/` is a bind into a Windows or macOS folder through Docker Desktop, where
small-file writes cost about ten times what they cost in the container's own
layer (measured: `vmap4_extractor` ~8 h on the bind, 48 min off it). The switch
is a keyword argument and not a catalog field, because the extraction evidence
hashes the plan: a new field would make every install on every platform
extract everything again.

The tests are in three groups: the spec `tool_run()` builds (Linux unchanged,
scratch only when asked); the real `SCRATCH_SCRIPT` run under `/bin/sh` against
fake tools, which is where the shell's own rules are pinned; and the family
call sites — AzerothCore on Windows/macOS gets the wrapper, on Linux it gets
today's argv, and CMaNGOS never gets it.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.support_player_text import command_faults, text_faults
from yulon import docker
from yulon.catalog import composegen, native
from yulon.catalog.catalog import (
    AzerothCoreData,
    CatalogEntry,
    ExtractPlan,
    ExtractTool,
    load_catalog,
)
from yulon.catalog.families import extract, surgical
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError
from yulon.resources import installers_dir

SCRATCH_SENTENCE = (
    "Docker Desktop writes slowly into a Windows or macOS folder, so each tool writes inside "
    "its container and the result is copied into data/ when it finishes."
)

AD = ExtractTool(
    name="dbc and maps",
    argv=("/opt/bin/ad", "-i", "/client", "-o", "/out"),
    produces={"dbc": 3, "maps": 2},
)
PLAN = ExtractPlan(image="server", tools=(AD,))


def coa_plan() -> ExtractPlan:
    """The shipped CoA extract plan: `map_extractor`, `vmap4_extractor`, `vmap4_assembler`."""
    entry = load_catalog().get("wow-coa")
    block = entry.install.native.azerothcore.client_extract
    assert block is not None
    return block.extract


def client(root: Path) -> Path:
    folder = root / "client"
    (folder / "Data").mkdir(parents=True)
    (folder / "Data" / "expansion.MPQ").write_bytes(b"MPQ" * 100)
    return folder


def spec_for(
    plan: ExtractPlan, tool: ExtractTool, tmp_path: Path, **kwargs: object
) -> docker.ContainerRun:
    return extract.tool_run(
        plan,
        tool,
        image_ref="yulon.local/tools:1",
        client_dir=tmp_path / "client",
        data_dir=tmp_path / "data",
        user_args=("--user", "1000:1000"),
        **kwargs,  # type: ignore[arg-type]
    )


# -- the spec `tool_run()` builds ---------------------------------------------


def test_on_linux_every_coa_tool_spec_is_exactly_todays(tmp_path: Path) -> None:
    """No wrapper, cwd `/out`, no env: the argv, mounts and cwd every Linux install has."""
    plan = coa_plan()
    assert plan.tools, "the shipped plan must have tools"
    for tool in plan.tools:
        spec = spec_for(plan, tool, tmp_path)
        assert spec.argv == tuple(tool.argv)
        assert spec.workdir == extract.OUT_MOUNT
        assert dict(spec.env) == {}
        assert spec.mounts == (
            docker.Mount(tmp_path / "client", extract.CLIENT_MOUNT, read_only=True),
            docker.Mount(tmp_path / "data", extract.OUT_MOUNT),
        )
        assert spec.ulimits == ()


def test_the_scratch_spec_wraps_the_tool_and_names_its_output_folders(tmp_path: Path) -> None:
    """`sh -c SCRATCH_SCRIPT sh <tool argv>`, cwd `/scratch`, and the same mounts as before."""
    plan = coa_plan()
    for tool in plan.tools:
        plain = spec_for(plan, tool, tmp_path)
        scratched = spec_for(plan, tool, tmp_path, scratch=True)
        assert scratched.argv == ("sh", "-c", extract.SCRATCH_SCRIPT, "sh", *tool.argv)
        assert scratched.workdir == extract.SCRATCH_MOUNT
        assert dict(scratched.env) == {"YULON_OUT_DIRS": " ".join(tool.produces)}
        assert (
            scratched.mounts == plain.mounts
        ), "the client stays read-only; nothing new is mounted"


def test_a_plan_that_both_stages_the_client_and_runs_off_the_bind_is_refused(
    tmp_path: Path,
) -> None:
    """The catalog must pick one; doing both would wrap the wrapper."""
    staged = ExtractPlan(image="server", tools=(AD,), stage_client=True)
    with pytest.raises(InstallerError, match="both stages the client"):
        spec_for(staged, AD, tmp_path, scratch=True)
    spec_for(staged, AD, tmp_path)  # staging alone is still fine


def test_an_output_folder_named_with_whitespace_is_refused_for_the_scratch_path_too(
    tmp_path: Path,
) -> None:
    """`for name in $YULON_OUT_DIRS` splits on whitespace: nothing copied, a lying count gate."""
    spaced = ExtractTool(name="odd", argv=("/opt/bin/x",), produces={"two words": 1})
    plan = ExtractPlan(image="server", tools=(spaced,))
    with pytest.raises(InstallerError, match="whitespace"):
        spec_for(plan, spaced, tmp_path, scratch=True)


# -- the real script, under /bin/sh ------------------------------------------

OUT_DIRS = "YULON_OUT_DIRS"


def scratched_script(out: Path, scratch: Path) -> str:
    """`SCRATCH_SCRIPT` with its two container paths pointed at this test's folders.

    `/out` and `/scratch` are the only paths the script spells, and they are the
    same two names on every host, so two string replacements are the whole
    rewrite.
    """
    return extract.SCRATCH_SCRIPT.replace("/scratch", str(scratch)).replace("/out", str(out))


def run_scratched(
    tmp_path: Path, out: Path, body: str, out_dirs: str, *args: str
) -> subprocess.CompletedProcess[str]:
    """Run the real script in a tmp dir, with `body` as the tool's own sh script."""
    tool = tmp_path / "tool.sh"
    tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    return subprocess.run(
        ("sh", "-c", scratched_script(out, tmp_path / "scratch"), "sh", "sh", str(tool), *args),
        env={**os.environ, OUT_DIRS: out_dirs},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_a_scratched_tool_writes_into_the_scratch_folder_and_its_output_is_copied_out(
    tmp_path: Path,
) -> None:
    """The point of the whole ticket: the bytes land on the bind once, at the end, flat."""
    out = tmp_path / "out"
    out.mkdir()
    result = run_scratched(
        tmp_path,
        out,
        'mkdir -p Buildings && for f in f1 f2 f3; do echo x > "Buildings/$f"; done\n',
        "Buildings",
    )
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in (out / "Buildings").iterdir()) == ["f1", "f2", "f3"]
    assert not (out / "Buildings" / "Buildings").exists(), "copied by content, never nested"


def test_a_scratched_tool_reads_an_earlier_tools_output_through_a_link(tmp_path: Path) -> None:
    """`vmap assemble` reads `Buildings` from cwd: it is a link to `/out/Buildings`."""
    out = tmp_path / "out"
    (out / "Buildings").mkdir(parents=True)
    (out / "Buildings" / "f1").write_bytes(b"x")
    result = run_scratched(
        tmp_path,
        out,
        "test -f Buildings/f1 || exit 7\nmkdir -p vmaps\necho v > vmaps/v1\n",
        "vmaps",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (out / "vmaps" / "v1").is_file()
    assert [path.name for path in (out / "Buildings").iterdir()] == ["f1"], "untouched"


def test_a_scratched_tool_that_fails_still_has_its_output_copied(tmp_path: Path) -> None:
    """A crash after an hour of work must not throw the work away; the status stays the tool's."""
    out = tmp_path / "out"
    out.mkdir()
    result = run_scratched(tmp_path, out, "mkdir -p vmaps\necho v > vmaps/v1\nexit 3\n", "vmaps")
    assert result.returncode == 3
    assert (out / "vmaps" / "v1").is_file()


def test_a_tool_that_writes_straight_to_out_is_left_alone_by_the_copy_loop(tmp_path: Path) -> None:
    """`map_extractor -o /out` leaves nothing in scratch, so nothing is copied over it."""
    out = tmp_path / "out"
    (out / "maps").mkdir(parents=True)
    (out / "maps" / "keep").write_bytes(b"k")
    result = run_scratched(
        tmp_path,
        out,
        f'echo straight > "{out}/maps/straight"\n',
        "maps",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in (out / "maps").iterdir()) == ["keep", "straight"]


def test_an_old_folder_is_replaced_by_the_tools_output_not_merged(tmp_path: Path) -> None:
    """A killed run's half copy must not survive under a fresh, full re-run."""
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "stale").write_bytes(b"old")
    result = run_scratched(tmp_path, out, "mkdir -p vmaps\necho v > vmaps/new\n", "vmaps")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["new"]


def test_a_copy_that_cannot_be_made_is_exit_94_and_named(tmp_path: Path) -> None:
    """The folder is gone (disk full, permissions): say which one, and do not report success."""
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "old").write_bytes(b"o")
    result = run_scratched(
        tmp_path,
        out,
        'mkdir -p vmaps\necho n > vmaps/new\nchmod a-w "$1"\n',
        "vmaps",
        str(out),
    )
    try:
        assert result.returncode == 94, result.stdout + result.stderr
        assert extract.COPY_FAILED_MARKER in result.stdout
        assert "vmaps" in result.stdout
    finally:
        os.chmod(out, 0o755)


def test_a_tool_that_failed_first_keeps_its_own_status_over_a_failed_copy(tmp_path: Path) -> None:
    """The copy's 94 must never replace a status the tool itself produced."""
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    result = run_scratched(
        tmp_path,
        out,
        'mkdir -p vmaps\necho n > vmaps/new\nchmod a-w "$1"\nexit 3\n',
        "vmaps",
        str(out),
    )
    try:
        assert result.returncode == 3
        assert extract.COPY_FAILED_MARKER in result.stdout
    finally:
        os.chmod(out, 0o755)


def test_a_destination_already_holding_that_name_is_a_skip_and_not_a_failure(
    tmp_path: Path,
) -> None:
    """The case-twin on a case-insensitive bind: `--update=none` keeps the existing file.

    The CONTENT half of this is what `--update=none` (GNU coreutils 9.3+, the
    tools image is Ubuntu 24.04) buys, measured on 9.4: exit 0, existing file
    kept. It is not asserted here because a `cp` without the option also exits 0
    on Linux — it simply overwrites — and the failure it prevents exists only
    on a case-insensitive filesystem, which no test on this machine has. The
    twin case is proven live, not here; what this pins is that an existing
    destination is never a failed copy.
    """
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "twin").write_bytes(b"old")
    result = run_scratched(tmp_path, out, "mkdir -p vmaps\necho new > vmaps/twin\n", "vmaps")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (out / "vmaps" / "twin").is_file()


def test_the_script_is_its_own_home_for_its_paths_and_constants() -> None:
    """One home per name: a script that hard-coded a third spelling would drift silently."""
    assert extract.SCRATCH_MOUNT == "/scratch"
    assert f"mkdir -p {extract.SCRATCH_MOUNT} && cd {extract.SCRATCH_MOUNT} ||" in (
        extract.SCRATCH_SCRIPT
    )
    assert f"{extract.OUT_MOUNT}/*" in extract.SCRATCH_SCRIPT
    assert '"$@"; status=$?;' in extract.SCRATCH_SCRIPT
    assert extract.SCRATCH_SCRIPT.endswith("exit $status")
    assert extract.SCRATCH_FAILED_MARKER in extract.SCRATCH_SCRIPT
    assert f"exit {extract.SCRATCH_FAILED_RETURNCODE}" in extract.SCRATCH_SCRIPT
    assert extract.COPY_FAILED_MARKER in extract.SCRATCH_SCRIPT
    assert "status=94" in extract.SCRATCH_SCRIPT
    assert "--update=none" in extract.SCRATCH_SCRIPT


# -- run_plan: the line, and the two refusals ---------------------------------


def filling(data_dir: Path, produced: Mapping[str, int]) -> extract.RunContainer:
    """A `docker run` that succeeds and leaves `produced`'s counts under `data_dir`."""

    def run_container(
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        sink(f"{spec.argv[0]} ran")
        for name, count in produced.items():
            folder = data_dir / name
            folder.mkdir(parents=True, exist_ok=True)
            for index in range(count):
                (folder / f"{index:05d}.bin").write_bytes(b"x")
        return docker.AttachedRun(0, ("ran",))

    return run_container


def run_said(
    tmp_path: Path,
    runner: extract.RunContainer,
    *,
    plan: ExtractPlan = PLAN,
    scratch: bool = True,
) -> list[str]:
    data = tmp_path / "data"
    return list(
        extract.run_plan(
            plan,
            image_ref="yulon.local/tools:1",
            client_dir=client(tmp_path),
            data_dir=data,
            run_container=runner,
            user_args=(),
            sink=lambda line: None,
            cancel=None,
            scratch_off_bind=scratch,
        )
    )


def test_the_scratch_line_is_said_once_before_the_first_tool_and_is_plain_words(
    tmp_path: Path,
) -> None:
    said = run_said(tmp_path, filling(tmp_path / "data", {"dbc": 3, "maps": 2}))
    assert said.count(SCRATCH_SENTENCE) == 1, said
    assert said.index(SCRATCH_SENTENCE) < next(
        index for index, line in enumerate(said) if ": running " in line
    )
    assert text_faults(SCRATCH_SENTENCE) == []
    assert command_faults(SCRATCH_SENTENCE) == []


def test_off_the_bind_nothing_new_is_said(tmp_path: Path) -> None:
    said = run_said(tmp_path, filling(tmp_path / "data", {"dbc": 3, "maps": 2}), scratch=False)
    assert SCRATCH_SENTENCE not in said


def test_a_scratch_folder_that_could_not_be_made_is_the_tool_never_running(
    tmp_path: Path,
) -> None:
    """Both halves of the signal, the way the staged farm's own failure is read."""

    def runner(
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        return docker.AttachedRun(
            extract.SCRATCH_FAILED_RETURNCODE, (extract.SCRATCH_FAILED_MARKER,)
        )

    with pytest.raises(InstallerError) as caught:
        run_said(tmp_path, runner)
    message = str(caught.value)
    assert "never ran" in message
    assert extract.SCRATCH_FAILED_MARKER in message
    assert f"exit {extract.SCRATCH_FAILED_RETURNCODE}" not in message


def test_a_copy_that_failed_names_the_tool_the_folder_and_the_likely_cause(
    tmp_path: Path,
) -> None:
    """94 without its marker is an ordinary status; with it, it is the copy that failed."""

    def runner(
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        return docker.AttachedRun(
            extract.COPY_FAILED_RETURNCODE, (f"{extract.COPY_FAILED_MARKER} maps",)
        )

    with pytest.raises(InstallerError) as caught:
        run_said(tmp_path, runner)
    message = str(caught.value)
    assert "could not be copied into data/maps (disk full?)" in message
    assert extract.COPY_FAILED_MARKER in message


def test_a_tool_that_really_exits_94_is_still_the_tool_failing(tmp_path: Path) -> None:
    def runner(
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        return docker.AttachedRun(extract.COPY_FAILED_RETURNCODE, ("aborting",))

    with pytest.raises(InstallerError, match="exit 94") as caught:
        run_said(tmp_path, runner)
    assert "copied" not in str(caught.value)


# -- the AzerothCore call site ------------------------------------------------

COA_ID = "wow-coa"
DB_PASSWORD = "coa-0123456789abcdef"


def coa_entry(platform: str) -> CatalogEntry:
    entry = load_catalog().get(COA_ID)
    return entry.model_copy(
        update={"install": entry.install.model_copy(update={"platforms": (platform,)})}
    )


def coa_client(tmp_path: Path) -> Path:
    """A client tree passing the CoA ClientSpec: Data/, the WB2 marker, the MPQs, a locale."""
    folder = tmp_path / "client"
    (folder / "Data" / "enUS").mkdir(parents=True)
    (folder / "Data" / "patch-WB2.MPQ").write_bytes(b"MPQ\x1a")
    (folder / "Data" / "patch-WB1.MPQ.DISABLED").write_bytes(b"MPQ\x1a")
    for name in ("common", "expansion", "lichking", "patch", "patch-2", "patch-3"):
        (folder / "Data" / f"{name}.MPQ").write_bytes(b"MPQ\x1a")
    (folder / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"MPQ\x1a")
    return folder


def ac_engine(rec: Recorder, entry: CatalogEntry) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry,
        installers_root=installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(platform_id=lambda: entry.install.platforms[0]),
    )


def lay_map_data(data_dir: Path, map_ids: Iterable[int]) -> None:
    """The grid files `missing_map_data()` reads: one `.map` tile and the `.vmtree` per map."""
    (data_dir / "maps").mkdir(parents=True, exist_ok=True)
    (data_dir / "vmaps").mkdir(parents=True, exist_ok=True)
    for map_id in map_ids:
        key = f"{map_id:03}"
        (data_dir / "maps" / f"{key}0000.map").write_bytes(b"map")
        (data_dir / "vmaps" / f"{key}.vmtree").write_bytes(b"vmap")


def ac_context(server_dir: Path, client_dir: Path, entry: CatalogEntry) -> native.StageContext:
    return native.StageContext(
        server_dir=server_dir,
        client_dir=client_dir,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="azerothcore",
            completed=(),
        ),
        cancel=None,
        secrets=native.Secrets(db_password=DB_PASSWORD),
    )


def coa_produce(rec: Recorder) -> Recorder:
    """The double's output at the CoA plan's scale: counts its `produces` gates accept."""
    rec.produce = {"maps": 9100, "dbc": 370, "Cameras": 1, "Buildings": 1100, "vmaps": 18400}
    return rec


def coa_tools(entry: CatalogEntry, client_dir: Path) -> tuple[ExtractTool, ...]:
    block = entry.install.native.azerothcore.client_extract
    assert block is not None
    tools_bin = block.extract.tools[0].argv[0].rpartition("/")[0] or "/"
    data = AzerothCoreData(client_extract=block)
    return surgical.effective_plan(data, tools_bin, client_dir).tools


def test_on_docker_desktop_every_coa_tool_runs_in_the_containers_own_layer(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client_dir = coa_client(tmp_path)
    entry = coa_entry("windows")
    lay_map_data(server_dir / "data", (0, 1, 530, 571, 936, 937))
    rec = coa_produce(Recorder())
    said = list(ac_engine(rec, entry)._extract_maps(ac_context(server_dir, client_dir, entry)))
    tools = coa_tools(entry, client_dir)
    assert len(rec.container_runs) == len(tools)
    for run, tool in zip(rec.container_runs, tools, strict=True):
        assert run.argv == ("sh", "-c", extract.SCRATCH_SCRIPT, "sh", *tool.argv)
        assert run.workdir == extract.SCRATCH_MOUNT
        assert dict(run.env) == {"YULON_OUT_DIRS": " ".join(tool.produces)}
    assert SCRATCH_SENTENCE in said


def test_on_macos_every_coa_tool_runs_in_the_containers_own_layer(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client_dir = coa_client(tmp_path)
    entry = coa_entry("macos")
    lay_map_data(server_dir / "data", (0, 1, 530, 571, 936, 937))
    rec = coa_produce(Recorder())
    list(ac_engine(rec, entry)._extract_maps(ac_context(server_dir, client_dir, entry)))
    assert rec.container_runs
    assert all(run.workdir == extract.SCRATCH_MOUNT for run in rec.container_runs)


def test_on_linux_the_coa_tools_are_the_argv_they_always_were(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client_dir = coa_client(tmp_path)
    entry = coa_entry("linux")
    lay_map_data(server_dir / "data", (0, 1, 530, 571, 936, 937))
    rec = coa_produce(Recorder())
    said = list(ac_engine(rec, entry)._extract_maps(ac_context(server_dir, client_dir, entry)))
    tools = coa_tools(entry, client_dir)
    assert [run.argv for run in rec.container_runs] == [tuple(tool.argv) for tool in tools]
    assert all(
        run.workdir == extract.OUT_MOUNT and dict(run.env) == {} for run in rec.container_runs
    )
    assert SCRATCH_SENTENCE not in said


def test_the_cmangos_path_still_runs_its_tools_against_the_bind(tmp_path: Path) -> None:
    """CMaNGOS never passes the switch: its tools keep `argv`, cwd `/out`, no env."""
    from tests.test_families_cmangos import context as cmangos_context
    from tests.test_families_cmangos import engine as cmangos_engine

    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    rec = Recorder()
    list(cmangos_engine(rec)._extract(cmangos_context(server_dir, client_dir)))
    assert rec.container_runs
    assert all(
        run.argv[0] != "sh" and run.workdir == extract.OUT_MOUNT and dict(run.env) == {}
        for run in rec.container_runs
    )
