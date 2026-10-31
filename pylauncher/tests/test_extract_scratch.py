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
    RetrySpec,
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
    """`sh -c SCRATCH_SCRIPT sh <tool argv>`, cwd `/tmp`, and the same mounts as before."""
    plan = coa_plan()
    for tool in plan.tools:
        plain = spec_for(plan, tool, tmp_path)
        scratched = spec_for(plan, tool, tmp_path, scratch=True)
        assert scratched.argv == ("sh", "-c", extract.SCRATCH_SCRIPT, "sh", *tool.argv)
        assert scratched.workdir == "/tmp"
        assert scratched.workdir == extract.SCRATCH_WORKDIR
        assert dict(scratched.env) == {"YULON_OUT_DIRS": " ".join(tool.produces)}
        assert (
            scratched.mounts == plain.mounts
        ), "the client stays read-only; nothing new is mounted"


def test_the_scratch_folder_is_under_tmp_where_the_images_user_can_make_it() -> None:
    """`-w /scratch` lays `/scratch` as root, and the tool runs as uid 1000 `acore`.

    Measured on Docker Desktop: `platform.container_user_args()` answers `[]`
    there, so `docker run` starts the image's own USER. A workdir Docker has to
    create is root-owned 0755 and the tool can neither link nor `mkdir` in it;
    `/tmp` is in every image, mode 1777, so the workdir is `/tmp` and the
    script makes its own folder inside it.
    """
    assert extract.SCRATCH_MOUNT == "/tmp/yulon-scratch"
    assert extract.SCRATCH_WORKDIR == "/tmp"
    assert extract.SCRATCH_MOUNT.startswith("/tmp/"), "the workdir's 1777 is what makes it writable"
    assert extract.SCRATCH_SCRIPT.startswith(
        f"mkdir -p {extract.SCRATCH_MOUNT} && cd {extract.SCRATCH_MOUNT} ||"
    ), "the folder is made by the user the tool runs as, not by Docker as root"


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

    `/out` and the scratch mount are the only paths the script spells, and they
    are the same names on every host, so two string replacements are the whole
    rewrite. The mount goes first: it lives under `/tmp`, and the `/out`
    replacement must not reach into it.
    """
    return extract.SCRATCH_SCRIPT.replace(extract.SCRATCH_MOUNT, str(scratch)).replace(
        extract.OUT_MOUNT, str(out)
    )


def run_scratched(
    tmp_path: Path,
    out: Path,
    body: str,
    out_dirs: str,
    *args: str,
    path_prefix: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real script in a tmp dir, with `body` as the tool's own sh script."""
    tool = tmp_path / "tool.sh"
    tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    env = {**os.environ, OUT_DIRS: out_dirs}
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(
        ("sh", "-c", scratched_script(out, tmp_path / "scratch"), "sh", "sh", str(tool), *args),
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def staging(out: Path) -> list[str]:
    """Every copy staging folder left in `/out`: none is the answer after any run."""
    return sorted(p.name for p in out.iterdir() if p.name.startswith(".yulon-copy-"))


NOT_ROOT = pytest.mark.skipif(
    getattr(os, "geteuid", lambda: -1)() == 0,
    reason="root can write into an 0555 folder, so a Docker-made data/ cannot be imitated",
)


@NOT_ROOT
def test_the_copy_back_works_when_the_container_cannot_write_the_top_of_data(
    tmp_path: Path,
) -> None:
    """A Docker-made `data/` is root's 0755 inside the container, so uid 1000 cannot
    create anything at its top -- which is what refused the live CoA install (T845).

    The host makes the staging folder, so the container only writes INSIDE a folder
    it owns, and the host moves the finished copy into place afterwards.
    """
    out = tmp_path / "out"
    out.mkdir()
    extract.make_copy_dirs(["Buildings"], out)
    tool = ExtractTool(name="vmap extract", argv=("/opt/bin/x",), produces={"Buildings": 1})
    try:
        out.chmod(0o555)
        result = run_scratched(tmp_path, out, 'echo x > "Buildings/x"\n', "Buildings")
    finally:
        out.chmod(0o755)
    assert result.returncode == 0, result.stdout + result.stderr
    extract.promote_copies(tool, docker.AttachedRun(0, ("ran",)), out)
    assert (out / "Buildings" / "x").is_file()
    assert staging(out) == [], "no .yulon-copy-* left after the host promoted"


def stage(out: Path, produces: Mapping[str, int]) -> None:
    """The host's half before a run: the staging folder a tool fills."""
    extract.make_copy_dirs(produces, out)


def promote(out: Path, produces: Mapping[str, int], *, returncode: int = 0) -> None:
    """The host's half after a run: promote the filled folder, or clear the staging one."""
    tool = ExtractTool(name="tool", argv=("/opt/bin/tool",), produces=dict(produces))
    extract.promote_copies(tool, docker.AttachedRun(returncode, ("ran",)), out)


def test_a_scratched_tool_writes_into_the_scratch_folder_and_its_output_is_copied_out(
    tmp_path: Path,
) -> None:
    """The point of the whole ticket: the bytes land on the bind once, at the end, flat."""
    out = tmp_path / "out"
    out.mkdir()
    stage(out, {"Buildings": 1})
    result = run_scratched(
        tmp_path,
        out,
        'for f in f1 f2 f3; do echo x > "Buildings/$f"; done\n',
        "Buildings",
    )
    assert result.returncode == 0, result.stderr
    staging_dir = out / ".yulon-copy-Buildings"
    assert (staging_dir / ".yulon-copied").is_file(), "the container filled the host's folder"
    assert sorted(path.name for path in staging_dir.iterdir() if path.name != ".yulon-copied") == [
        "f1",
        "f2",
        "f3",
    ]
    assert not (out / "Buildings").exists(), "the container never renames anything in /out"
    promote(out, {"Buildings": 1})
    assert sorted(path.name for path in (out / "Buildings").iterdir()) == ["f1", "f2", "f3"]
    assert not (out / "Buildings" / "Buildings").exists(), "copied by content, never nested"
    assert staging(out) == [], "the host moved the complete copy into place"


def test_a_scratched_tool_reads_an_earlier_tools_output_through_a_link(tmp_path: Path) -> None:
    """`vmap assemble` reads `Buildings` from cwd: it is a link to `/out/Buildings`.

    And it does NOT create `vmaps/` itself — the shipped assembler fails on a
    missing output folder (`make_out_dirs()` measured it, 2026-09-02) — so the
    script must have made it in scratch before `"$@"`.
    """
    out = tmp_path / "out"
    (out / "Buildings").mkdir(parents=True)
    (out / "Buildings" / "f1").write_bytes(b"x")
    stage(out, {"vmaps": 1})
    result = run_scratched(
        tmp_path,
        out,
        "test -f Buildings/f1 || exit 7\necho v > vmaps/v1\n",
        "vmaps",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    promote(out, {"vmaps": 1})
    assert (out / "vmaps" / "v1").is_file()
    assert [path.name for path in (out / "Buildings").iterdir()] == ["f1"], "untouched"
    assert staging(out) == []


def test_a_tool_that_fails_leaves_the_old_folder_alone(tmp_path: Path) -> None:
    """A failed tool's partial output never replaces `/out/<name>`: no copy at all.

    The old folder is never vouched for (the evidence did not accept it), so
    losing it would cost only a re-run -- but a crash must not put a half
    extraction in its place. The scratch is discarded with the container, and
    the host clears the staging folder it made.
    """
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "old").write_bytes(b"o")
    stage(out, {"vmaps": 1})
    result = run_scratched(tmp_path, out, "echo new > vmaps/new\nexit 3\n", "vmaps")
    assert result.returncode == 3
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["old"]
    promote(out, {"vmaps": 1}, returncode=3)
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["old"]
    assert staging(out) == [], "the staging folder the failed run did not fill is removed"


def test_a_tool_that_writes_straight_to_out_is_left_alone_by_the_copy_loop(tmp_path: Path) -> None:
    """`map_extractor -o /out` leaves its scratch folders empty, so nothing is copied over it.

    The staging folder it was given stays empty and gets no marker, so the host
    removes it rather than promoting an empty folder over real data.
    """
    out = tmp_path / "out"
    (out / "maps").mkdir(parents=True)
    (out / "maps" / "keep").write_bytes(b"k")
    stage(out, {"maps": 1})
    result = run_scratched(
        tmp_path,
        out,
        f'echo straight > "{out}/maps/straight"\n',
        "maps",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in (out / "maps").iterdir()) == ["keep", "straight"]
    scratch_maps = tmp_path / "scratch" / "maps"
    assert scratch_maps.is_dir(), "the produces folder was made in scratch before the tool ran"
    assert list(scratch_maps.iterdir()) == [], "and it stayed empty, so it was skipped"
    assert not (out / ".yulon-copy-maps" / ".yulon-copied").exists()
    promote(out, {"maps": 1})
    assert sorted(path.name for path in (out / "maps").iterdir()) == ["keep", "straight"]
    assert staging(out) == []


def test_an_old_folder_is_replaced_by_the_tools_output_not_merged(tmp_path: Path) -> None:
    """A killed run's half copy must not survive under a fresh, full re-run."""
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "stale").write_bytes(b"old")
    stage(out, {"vmaps": 1})
    result = run_scratched(tmp_path, out, "echo v > vmaps/new\n", "vmaps")
    assert result.returncode == 0, result.stdout + result.stderr
    promote(out, {"vmaps": 1})
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["new"]
    assert staging(out) == []


def failing_cp(folder: Path, *, only: str | None = None) -> Path:
    """A `cp` on PATH that lays a partial staging copy and fails.

    `only` limits the failure to the staging folder whose name contains it, so
    a run with several output folders can be seen to carry on past the one
    that failed.
    """
    folder.mkdir()
    guard = "" if only is None else f'case "$4" in *{only}*) ;; *) exec /bin/cp "$@";; esac\n'
    (folder / "cp").write_text(
        "#!/bin/sh\n" + guard + 'mkdir -p "$4" && echo partial > "$4/partial"\nexit 1\n',
        encoding="utf-8",
    )
    (folder / "cp").chmod(0o755)
    return folder


def test_a_copy_that_cannot_be_made_is_exit_94_named_and_leaves_the_old_folder(
    tmp_path: Path,
) -> None:
    """Disk full or permissions: say which folder, keep the old one, and do not claim success.

    Also the whole path a user sees: the script's own marker, `_conclude()`'s
    copy sentence, and the host clearing the partial staging copy afterwards.
    """
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "old").write_bytes(b"o")
    stage(out, {"vmaps": 1})
    result = run_scratched(
        tmp_path,
        out,
        "echo n > vmaps/new\n",
        "vmaps",
        path_prefix=failing_cp(tmp_path / "bin"),
    )
    assert result.returncode == 94, result.stdout + result.stderr
    assert f"{extract.COPY_FAILED_MARKER} vmaps" in result.stdout
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["old"]
    assert (
        out / ".yulon-copy-vmaps" / "partial"
    ).is_file(), "the partial copy is what the host clears"

    def replay(
        spec: docker.ContainerRun,
        *,
        sink: docker.OutputSink,
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        return docker.AttachedRun(result.returncode, tuple(result.stdout.splitlines()))

    with pytest.raises(InstallerError, match="could not be copied into data/vmaps"):
        run_said(tmp_path, replay)
    promote(out, {"vmaps": 1}, returncode=94)
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["old"]
    assert staging(out) == [], "the partial staging copy was removed, best effort"


def test_one_failed_copy_does_not_stop_the_folders_after_it(tmp_path: Path) -> None:
    """Do not stop half way through the list silently: the next folder is still copied."""
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / "vmaps" / "old").write_bytes(b"o")
    stage(out, {"maps": 1, "vmaps": 1})
    result = run_scratched(
        tmp_path,
        out,
        "echo m > maps/m1\necho v > vmaps/v1\n",
        "maps vmaps",
        path_prefix=failing_cp(tmp_path / "bin", only=".yulon-copy-vmaps"),
    )
    assert result.returncode == 94, result.stdout + result.stderr
    assert f"{extract.COPY_FAILED_MARKER} vmaps" in result.stdout
    assert (out / ".yulon-copy-maps" / "m1").is_file(), "the folder after the failure was filled"
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["old"]
    promote(out, {"maps": 1, "vmaps": 1}, returncode=94)
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["old"]
    assert not (out / "maps").exists(), "a failed run promotes nothing, even a complete folder"
    assert staging(out) == []


def test_a_staging_folder_a_killed_run_left_is_removed_and_the_run_succeeds(
    tmp_path: Path,
) -> None:
    """A leftover `/out/.yulon-copy-<name>` is removed by the host before the next run.

    It must not be copied from or into: a merge of two extractions would then be
    promoted as one.
    """
    out = tmp_path / "out"
    (out / "vmaps").mkdir(parents=True)
    (out / ".yulon-copy-vmaps").mkdir(parents=True)
    (out / ".yulon-copy-vmaps" / "junk").write_bytes(b"j")
    stage(out, {"vmaps": 1})
    assert staging(out) == [".yulon-copy-vmaps"]
    assert list((out / ".yulon-copy-vmaps").iterdir()) == [], "the leftover was cleared"
    result = run_scratched(tmp_path, out, "echo v > vmaps/v1\n", "vmaps")
    assert result.returncode == 0, result.stdout + result.stderr
    promote(out, {"vmaps": 1})
    assert sorted(path.name for path in (out / "vmaps").iterdir()) == ["v1"]
    assert staging(out) == []


def test_the_script_is_its_own_home_for_its_paths_and_constants() -> None:
    """One home per name: a script that hard-coded a third spelling would drift silently."""
    assert extract.SCRATCH_MOUNT == "/tmp/yulon-scratch"
    assert extract.SCRATCH_WORKDIR == "/tmp"
    assert f"mkdir -p {extract.SCRATCH_MOUNT} && cd {extract.SCRATCH_MOUNT} ||" in (
        extract.SCRATCH_SCRIPT
    )
    assert f"{extract.OUT_MOUNT}/*" in extract.SCRATCH_SCRIPT
    assert 'for name in $YULON_OUT_DIRS; do mkdir -p "$name"; done;' in extract.SCRATCH_SCRIPT
    assert '"$@"; status=$?;' in extract.SCRATCH_SCRIPT
    assert '[ "$status" -eq 0 ] || exit "$status";' in extract.SCRATCH_SCRIPT
    assert extract.SCRATCH_SCRIPT.endswith("exit $status")
    assert 'cp -r --update=none "$name/." "/out/.yulon-copy-$name/"' in extract.SCRATCH_SCRIPT
    assert 'touch "/out/.yulon-copy-$name/.yulon-copied"' in extract.SCRATCH_SCRIPT
    assert extract.SCRATCH_FAILED_MARKER in extract.SCRATCH_SCRIPT
    assert f"exit {extract.SCRATCH_FAILED_RETURNCODE}" in extract.SCRATCH_SCRIPT
    assert extract.COPY_FAILED_MARKER in extract.SCRATCH_SCRIPT
    assert "status=94" in extract.SCRATCH_SCRIPT
    assert "--update=none" in extract.SCRATCH_SCRIPT
    assert "rm -rf" not in extract.SCRATCH_SCRIPT, "the container cannot touch the top of /out"
    assert " mv " not in extract.SCRATCH_SCRIPT, "the host does the swap, not the container"


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


def test_both_loops_stage_before_and_promote_after_every_run_only_off_the_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staging folder is the host's on BOTH paths, and promotion is before `_conclude()`.

    A retry recipe re-runs a tool the main loop already ran, from the second
    loop — the one the `make_out_dirs()` fix missed once — so both loops are
    walked here, in order around each `run_container`. Off the bind neither
    helper may run: Linux keeps today's run byte for byte, and no
    `.yulon-copy-*` may ever appear under its data folder.
    """
    plan = ExtractPlan(
        image="server",
        tools=(AD,),
        retry=RetrySpec(when_log_matches="boom", when_returncode_in=(139,), tools=(AD.name,)),
    )
    events: list[tuple[str, str, int]] = []
    client_dir = client(tmp_path)
    real_make = extract.make_copy_dirs
    real_promote = extract.promote_copies

    def drive(data: Path, scratch: bool) -> list[tuple[str, str, int]]:
        events.clear()

        def run_container(
            spec: docker.ContainerRun,
            *,
            sink: docker.OutputSink,
            cancel: threading.Event | None = None,
        ) -> docker.AttachedRun:
            runs = len([event for event in events if event[0] == "run"])
            events.append(("run", "", runs))
            if runs == 0:
                return docker.AttachedRun(139, ("boom",))
            for name, count in AD.produces.items():
                folder = (data / f".yulon-copy-{name}") if scratch else (data / name)
                folder.mkdir(parents=True, exist_ok=True)
                for index in range(count):
                    (folder / f"{index:05d}.bin").write_bytes(b"x")
                if scratch:
                    (folder / ".yulon-copied").touch()
            return docker.AttachedRun(0, ("ran",))

        def make_copy_dirs(produces: Iterable[str], data_dir: Path) -> None:
            events.append(("make", " ".join(produces), 0))
            real_make(produces, data_dir)

        def promote_copies(tool: ExtractTool, run: docker.AttachedRun, data_dir: Path) -> None:
            events.append(("promote", tool.name, run.returncode))
            real_promote(tool, run, data_dir)

        monkeypatch.setattr(extract, "make_copy_dirs", make_copy_dirs)
        monkeypatch.setattr(extract, "promote_copies", promote_copies)
        list(
            extract.run_plan(
                plan,
                image_ref="yulon.local/tools:1",
                client_dir=client_dir,
                data_dir=data,
                run_container=run_container,
                user_args=(),
                sink=lambda line: None,
                cancel=None,
                scratch_off_bind=scratch,
            )
        )
        return list(events)

    assert drive(tmp_path / "data", True) == [
        ("make", "dbc maps", 0),
        ("run", "", 0),
        ("promote", AD.name, 139),
        ("make", "dbc maps", 0),
        ("run", "", 1),
        ("promote", AD.name, 0),
    ]
    assert drive(tmp_path / "plain", False) == [("run", "", 0), ("run", "", 1)]
    assert list((tmp_path / "plain").rglob(".yulon-copy-*")) == []


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
        assert run.workdir == "/tmp"
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
    assert all(run.workdir == extract.SCRATCH_WORKDIR for run in rec.container_runs)


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
