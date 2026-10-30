"""One map out of an archive the main run never opens (T812 C4b).

`936 Vaults of the Inquisition` lives only in `Data/patch-WB1.MPQ.DISABLED`
(REPORT §4, ~/coa-work): a superseded 2.0 GB revision — laying it into the
main run changes 603 stock tiles and adds 2357 stale ones. The install's
`ExtractPlan` therefore ends with one more tool: `bash -c <script>` running the
same three extractors over a scratch view of the client where the disabled
archive is linked under the name the tools open, then copying only this map's
files into the shared `data/` and deleting the scratch.

The script is data here and `bash` is its interpreter for the same reason the
`mmaps` argv is data: the container that runs it is the tools image, which
ships `bash` (its `runtime` base is Ubuntu). Tested by running it with stub
`map_extractor`/`vmap4_*` fakes on PATH, so the assertions are about what the
script copies, never about an extractor binary.

T836: the CoA launcher's client (manifest 2026-10-29-01) ships that same
archive switched ON, as `Data/patch-WB1.MPQ` (sha256 identical to the
`.DISABLED` one), with no `.DISABLED` beside it. Such a client's main run
already reads it and makes the map, and the side run's `ln -sfn` laid a
dangling link over the real archive: 70 archives opened, no 936 file, and the
install stopped on a `cp: cannot stat`. So the side run is appended only for a
client that keeps the archive switched off (`layout()`), and a client holding
neither name is refused before anything is extracted (`archive_checks()`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from yulon import client_names
from yulon.catalog.catalog import (
    AcSurgicalMap,
    AzerothCoreData,
    CatalogEntry,
    ExtractPlan,
    ExtractTool,
)
from yulon.catalog.preflight import Check

TOOL_PREFIX = "map "
"""The appended tool's name: `map <id> from <archive>`, so the log says what it did."""

SURGICAL_DIR = ".yulon-surgical-"
"""Scratch under the container's `/out`: wiped before and after, never recorded."""

FAILED_EXIT = 3
"""The script's status when the archive is not there or fewer than `min_maps` maps came out."""

ARCHIVE_CHECK = "map archive"
"""The preflight row naming which archive a surgical map is read from (T836)."""

Layout = Literal["enabled", "disabled", "missing"]
"""Where the client keeps a surgical map's archive: under `link_as` (the client loads it, the
main run reads it), under `archive` only (switched off: the side run carves the map out), or
under neither."""


def _real_archive(path: Path | None) -> bool:
    """A file with bytes in it, links followed: a dangling link or an empty file is none."""
    try:
        return path is not None and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def layout(spec: AcSurgicalMap, client_dir: Path) -> Layout:
    """Which of the two names the client holds the archive under (T836).

    `link_as` is looked up in any case, because the extractors' scan of the
    lettered patches folds case (`toupper` in `LetteredPatchArchives`):
    `patch-wb1.mpq` is opened as surely as `patch-WB1.MPQ`. `archive` is
    looked up exactly as spelled, because the side run's script reads exactly
    that path. `link_as` wins when both are there (a client that ran Update
    client over the old layout): the main run reads it either way, and a side
    run would only extract the same bytes a second time.
    """
    if _real_archive(client_names.find(client_dir, spec.link_as)):
        return "enabled"
    if _real_archive(client_dir / spec.archive):
        return "disabled"
    return "missing"


def archive_checks(entry: CatalogEntry, client_dir: Path) -> tuple[Check, ...]:
    """One preflight row per surgical map: a refusal when its archive is under neither name.

    Empty for an entry without surgical maps, and for a folder with no
    `Data/`: the folder rules refuse that one in their own words. A pass row
    otherwise, so the install log says which layout the extraction read.
    """
    native = entry.install.native
    block = native.azerothcore if native is not None else None
    plan = block.client_extract if block is not None else None
    if plan is None or not plan.surgical_maps or not (client_dir / "Data").is_dir():
        return ()
    offers_update = entry.client.update is not None
    rows: list[Check] = []
    for spec in plan.surgical_maps:
        found = layout(spec, client_dir)
        if found == "enabled":
            rows.append(
                Check(
                    ARCHIVE_CHECK,
                    "pass",
                    f"map {spec.map_id} is in {spec.link_as}, which the client loads; the main "
                    "extraction reads it",
                )
            )
        elif found == "disabled":
            rows.append(
                Check(
                    ARCHIVE_CHECK,
                    "pass",
                    f"map {spec.map_id} is in {spec.archive}, which the client keeps switched "
                    "off; it is extracted on its own after the main extraction",
                )
            )
        else:
            remedy = (
                f"Use Update client to get the {entry.name} client files, then press Install "
                "again."
                if offers_update
                else f"Pick a complete {entry.name} client folder, then press Install again."
            )
            rows.append(
                Check(
                    ARCHIVE_CHECK,
                    "refuse",
                    f"this client has no {spec.link_as} (nor the older {spec.archive}), and "
                    f"map {spec.map_id} is only in that archive, so nothing was extracted",
                    remedy,
                )
            )
    return tuple(rows)


def script(spec: AcSurgicalMap, tools_bin: str) -> str:
    """The `bash -c` program that extracts one map out of its disabled archive.

    `tools_bin` is the container-absolute folder the three extractor binaries
    live in (`/azerothcore/env/dist/bin` in the tools image): the appended
    tool's argv carries no other absolute path into this script, so a change
    of install prefix touches the catalog, not this program. `set -eu` so a
    failed extractor stops the script before the count check can pass over
    half an extraction.

    T836, measured with the real tools on a client in the launcher's layout
    (the archive under `link_as` only): the farm's `ln -sfn` left a dangling
    link, `map_extractor` opened 70 archives instead of 71 and wrote no 936
    file, the vmap pair ran for minutes, and the script died on `cp: cannot
    stat ...936*.map` with exit 1, leaving 1.9 GB of scratch in `data/`. So
    the archive is checked first (`[ -s ]` follows links: a dangling one is
    "missing"), the map count is read in the scratch right after
    `map_extractor` and before any copy or vmap run, both say their own
    `yulon:` sentence and exit `FAILED_EXIT`, and an EXIT trap removes the
    scratch on every path out, so a retry starts clean and `data/` keeps no
    scratch.

    `$w/out` is made here, before the extractor runs: the real `map_extractor`
    creates `out/maps` but not `out` itself and stops with "Could not create
    directory ... check your permissions" (three Linux Mint installs, v0.9.19;
    the stub in the test used to `mkdir -p` the parent and hid it).

    Layout inside `$w`: `view/` is the farm (`cp -rs /client/.` — links only,
    nothing copied; the disabled archive arrives as `ln -sfn`), `map_extractor`
    writes `out/`, and `vmap4_extractor`/`vmap4_assembler` run with cwd `$w` so
    their relative outputs (`Buildings/`, `vmaps/`) land beside it, not in
    `view/` and never in the client.
    """
    map_id = spec.map_id
    key = f"{map_id:03}"
    extras = " ".join(f'"{name}"' for name in spec.extra_vmaps)
    copy_extra = (
        f'for f in {extras}; do cp "$w/vmaps/$f" /out/vmaps/ 2>/dev/null || '
        'cp "$w/Buildings/$f" /out/vmaps/; done\n'
        if spec.extra_vmaps
        else ""
    )
    copies = [
        f'cp "$w/out/maps/"{key}*.map /out/maps/',
        f'cp "$w/vmaps/"{key}.vmtree /out/vmaps/ 2>/dev/null || '
        f'cp "$w/Buildings/"{key}.vmtree /out/vmaps/',
        f'cp "$w/vmaps/"{key}_*.vmtile /out/vmaps/ 2>/dev/null || '
        f'cp "$w/Buildings/"{key}_*.vmtile /out/vmaps/ 2>/dev/null || true',
    ]
    archive_check = (
        f'if [ ! -s "/client/{spec.archive}" ]; then echo "yulon: map {map_id}: '
        f'/client/{spec.archive} is missing or empty, so the map cannot be extracted"; '
        f"exit {FAILED_EXIT}; fi\n"
    )
    count_check = (
        f'n=$(ls "$w/out/maps/"{key}*.map 2>/dev/null | wc -l)\n'
        f'if [ "$n" -lt {spec.min_maps} ]; then echo "yulon: map {map_id}: $n files, '
        f'at least {spec.min_maps} expected"; exit {FAILED_EXIT}; fi\n'
    )
    return (
        "set -eu\n"
        + archive_check
        + f'w="/out/{SURGICAL_DIR}{map_id}"\n'
        + "trap 'rm -rf \"$w\"' EXIT\n"
        + 'rm -rf "$w"\n'
        + 'mkdir -p "$w/view" "$w/out"\n'
        + 'cp -rs /client/. "$w/view/"\n'
        + f'ln -sfn "/client/{spec.archive}" "$w/view/{spec.link_as}"\n'
        + 'cd "$w"\n'
        + f'"{tools_bin}/map_extractor" -i "$w/view" -o "$w/out" -e 1\n'
        + count_check
        + f'"{tools_bin}/vmap4_extractor" -d "$w/view/Data"\n'
        + f'"{tools_bin}/vmap4_assembler" Buildings vmaps\n'
        + "mkdir -p /out/maps /out/vmaps\n"
        + "\n".join(copies)
        + "\n"
        + copy_extra
    )


def tool_name(spec: AcSurgicalMap) -> str:
    """The appended tool's evidence name: `map <id> from <archive>`."""
    return f"{TOOL_PREFIX}{spec.map_id} from {spec.archive}"


def append(
    block: AzerothCoreData, tools_bin: str, specs: tuple[AcSurgicalMap, ...] | None = None
) -> ExtractPlan:
    """The catalog plan plus one `ExtractTool` per surgical map, same `produces` as `maps`.

    `specs` narrows the appended tools to those maps (T836: the ones whose
    archive the client keeps switched off); None appends every one.
    """
    plan = block.client_extract
    assert plan is not None  # the caller (the AC extract stage) only runs with one
    maps_need = plan.extract.tools[0].produces["maps"]
    chosen = plan.surgical_maps if specs is None else specs
    tools = [
        *plan.extract.tools,
        *(
            ExtractTool(
                name=tool_name(spec),
                argv=("bash", "-c", script(spec, tools_bin), "yulon-surgical"),
                produces={"maps": maps_need},
            )
            for spec in chosen
        ),
    ]
    return plan.extract.model_copy(update={"tools": tuple(tools)})


def effective_plan(block: AzerothCoreData, tools_bin: str, client_dir: Path) -> ExtractPlan:
    """The catalog plan plus a side run for each surgical map the client keeps switched off.

    T836: a map whose archive the client loads under `link_as` (the CoA
    launcher's layout) gets no side run, because the main run already reads
    that archive and makes the map; the `required_maps` gate after the run
    still demands it. A map under neither name keeps its side run, whose own
    archive check then says so plainly (the extract stage refuses that client
    before this, through `archive_checks()`). The plan, and with it the
    evidence's plan hash, therefore follows the client's layout: a client
    that switches the archive on after an install extracts everything again
    on the next extract stage instead of keeping terrain made without it.
    """
    plan = block.client_extract
    assert plan is not None
    off = tuple(spec for spec in plan.surgical_maps if layout(spec, client_dir) != "enabled")
    if not off:
        return plan.extract
    return append(block, tools_bin, off)
