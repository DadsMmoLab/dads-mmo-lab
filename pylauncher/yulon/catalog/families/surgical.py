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
"""

from __future__ import annotations

from yulon.catalog.catalog import AcSurgicalMap, AzerothCoreData, ExtractPlan, ExtractTool

TOOL_PREFIX = "map "
"""The appended tool's name: `map <id> from <archive>`, so the log says what it did."""

SURGICAL_DIR = ".yulon-surgical-"
"""Scratch under the container's `/out`: wiped before and after, never recorded."""

FAILED_EXIT = 3
"""The script's status when fewer than `min_maps` maps were copied."""


def script(spec: AcSurgicalMap, tools_bin: str) -> str:
    """The `bash -c` program that extracts one map out of its disabled archive.

    `tools_bin` is the container-absolute folder the three extractor binaries
    live in (`/azerothcore/env/dist/bin` in the tools image): the appended
    tool's argv carries no other absolute path into this script, so a change
    of install prefix touches the catalog, not this program. `set -eu` so a
    failed extractor stops the script before the count check can pass over
    half an extraction; `rm -rf` of the scratch even on that path, so a retry
    starts clean and `data/` keeps no scratch (the count check is the gate,
    and it reads only what was copied).

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
    tail = (
        f"n=$(ls /out/maps/{key}*.map 2>/dev/null | wc -l)\n"
        f'if [ "$n" -lt {spec.min_maps} ]; then echo "yulon: map {map_id}: $n files, '
        f'at least {spec.min_maps} expected"; exit {FAILED_EXIT}; fi\n'
        'rm -rf "$w"\n'
    )
    return (
        "set -eu\n"
        f'w="/out/{SURGICAL_DIR}{map_id}"\n'
        'rm -rf "$w"\n'
        'mkdir -p "$w/view" "$w/out"\n'
        'cp -rs /client/. "$w/view/"\n'
        f'ln -sfn "/client/{spec.archive}" "$w/view/{spec.link_as}"\n'
        'cd "$w"\n'
        f'"{tools_bin}/map_extractor" -i "$w/view" -o "$w/out" -e 1\n'
        f'"{tools_bin}/vmap4_extractor" -d "$w/view/Data"\n'
        f'"{tools_bin}/vmap4_assembler" Buildings vmaps\n'
        "mkdir -p /out/maps /out/vmaps\n" + "\n".join(copies) + "\n" + copy_extra + tail
    )


def tool_name(spec: AcSurgicalMap) -> str:
    """The appended tool's evidence name: `map <id> from <archive>`."""
    return f"{TOOL_PREFIX}{spec.map_id} from {spec.archive}"


def append(block: AzerothCoreData, tools_bin: str) -> ExtractPlan:
    """The catalog plan plus one `ExtractTool` per surgical map, same `produces` as `maps`."""
    plan = block.client_extract
    assert plan is not None  # the caller (the AC extract stage) only runs with one
    maps_need = plan.extract.tools[0].produces["maps"]
    tools = [
        *plan.extract.tools,
        *(
            ExtractTool(
                name=tool_name(spec),
                argv=("bash", "-c", script(spec, tools_bin), "yulon-surgical"),
                produces={"maps": maps_need},
            )
            for spec in plan.surgical_maps
        ),
    ]
    return plan.extract.model_copy(update={"tools": tuple(tools)})


def effective_plan(block: AzerothCoreData, tools_bin: str) -> ExtractPlan:
    """`append()` when the block names surgical maps, else the catalog plan unchanged."""
    plan = block.client_extract
    assert plan is not None
    if not plan.surgical_maps:
        return plan.extract
    return append(block, tools_bin)
