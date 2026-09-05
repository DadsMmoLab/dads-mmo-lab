#!/usr/bin/env python3
"""Round-9 doodad mutations, run in ~/yulon-runs/fix9-doodad/pylauncher.

Every mutation: anchor occurrence asserted == 1, md5 before/after recorded,
__pycache__ purged on BOTH sides, file restored and the md5 re-checked.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXTRACT = ROOT / "yulon/catalog/families/extract.py"
TEST_EXTRACT = ROOT / "tests/test_extract.py"
SUPPORT = ROOT / "tests/support_native.py"
PY = str(ROOT / ".venv/bin/python")

TWO_FILES = ["tests/test_extract.py", "tests/test_families_cmangos.py"]
GATE = ["-m", "not integration"]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def purge() -> None:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def run(args: list[str]) -> str:
    purge()
    import os

    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    purge()
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    tail = lines[-1] if lines else "(no output)"
    reds = sorted(
        {ln.split(" ")[1].split(" - ")[0] for ln in proc.stdout.splitlines() if ln.startswith("FAILED")}
    )
    return tail + ("\n    RED: " + "\n         ".join(reds) if reds else "")


def sub(path: Path, old: str, new: str) -> str:
    text = path.read_text(encoding="utf-8", newline="")
    if "\r\n" in text:  # the working copy is CRLF; the anchors below are written LF
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    assert text.count(old) == 1, f"anchor occurs {text.count(old)}x in {path.name}: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="")
    return md5(path)


HOIST_OLD = """    for tool in plan.tools:
        if tool_satisfied(tool, data_dir, current, expected):
            continue
        blocked = blocking_output(tool, data_dir)
        if blocked is not None:
            raise InstallerError(blocked_message(tool, blocked))
"""
LOOP_OLD = "        make_out_dirs(tool.produces, data_dir)\n"
LOOP_NEW = """        blocked = blocking_output(tool, data_dir)
        if blocked is not None:
            raise InstallerError(blocked_message(tool, blocked))
        make_out_dirs(tool.produces, data_dir)
"""

MUTATIONS: list[tuple[str, str, list[tuple[Path, str, str]], list[str]]] = [
    (
        "M1",
        "blocking_output keyed on DIR_INDEX alone -- the marker nothing writes",
        [
            (
                EXTRACT,
                "    if any((folder / marker).exists() for marker in DIRTY_MARKERS):",
                "    if (folder / DIR_INDEX).exists():",
            )
        ],
        GATE,
    ),
    (
        "M2",
        "the guard put back inside run_plan's per-tool loop (un-hoisted)",
        [(EXTRACT, HOIST_OLD, ""), (EXTRACT, LOOP_OLD, LOOP_NEW)],
        GATE,
    ),
    (
        "M3",
        '"Nothing was run and nothing was changed. " deleted from the refusal',
        [
            (
                EXTRACT,
                'f"it exits without writing anything). Nothing was run and nothing was changed. "',
                'f"it exits without writing anything). "',
            )
        ],
        GATE,
    ),
    (
        "M4",
        "blocked_message lists both markers instead of the ones on disk",
        [
            (
                EXTRACT,
                "    present = [marker for marker in DIRTY_MARKERS if (folder / marker).exists()]",
                "    present = list(DIRTY_MARKERS)",
            )
        ],
        GATE,
    ),
    (
        "M5",
        "the test_extract Runner writes `dir` again (the round-8 fabrication)",
        [
            (
                TEST_EXTRACT,
                "    FINISHED: tuple[str, ...] = (extract.DIR_BIN, extract.GAMEOBJECT_MODELS)",
                "    FINISHED: tuple[str, ...] = (extract.DIR_INDEX, extract.DIR_BIN)",
            )
        ],
        GATE,
    ),
    (
        "M6",
        "the test_extract Runner stops writing temp_gameobject_models",
        [
            (
                TEST_EXTRACT,
                "    FINISHED: tuple[str, ...] = (extract.DIR_BIN, extract.GAMEOBJECT_MODELS)",
                "    FINISHED: tuple[str, ...] = (extract.DIR_BIN,)",
            )
        ],
        GATE,
    ),
    (
        "M7",
        "support_native's Recorder writes `dir` again",
        [
            (
                SUPPORT,
                "for name in (extract.DIR_BIN, extract.GAMEOBJECT_MODELS):",
                "for name in (extract.DIR_INDEX, extract.DIR_BIN):",
            )
        ],
        GATE,
    ),
    (
        "M8",
        "support_native's Recorder stops writing temp_gameobject_models",
        [
            (
                SUPPORT,
                "for name in (extract.DIR_BIN, extract.GAMEOBJECT_MODELS):",
                "for name in (extract.DIR_BIN,):",
            )
        ],
        GATE,
    ),
    (
        "M9",
        "M1 + both doubles writing `dir` again: the round-8 tree's blind spot",
        [
            (
                EXTRACT,
                "    if any((folder / marker).exists() for marker in DIRTY_MARKERS):",
                "    if (folder / DIR_INDEX).exists():",
            ),
            (
                TEST_EXTRACT,
                "    FINISHED: tuple[str, ...] = (extract.DIR_BIN, extract.GAMEOBJECT_MODELS)",
                "    FINISHED: tuple[str, ...] = (extract.DIR_INDEX, extract.DIR_BIN)",
            ),
            (
                SUPPORT,
                "for name in (extract.DIR_BIN, extract.GAMEOBJECT_MODELS):",
                "for name in (extract.DIR_INDEX, extract.DIR_BIN):",
            ),
        ],
        GATE,
    ),
]


def main() -> int:
    only = sys.argv[1:]
    base = {p: (md5(p), p.read_bytes()) for p in (EXTRACT, TEST_EXTRACT, SUPPORT)}
    print("BASELINE md5:", {p.name: base[p][0] for p in base})
    print("BASELINE gate suite:", run(GATE))
    print("BASELINE two files:", run(TWO_FILES))
    for tag, what, edits, scope in MUTATIONS:
        if only and tag not in only:
            continue
        after = {}
        for path, old, new in edits:
            after[path.name] = sub(path, old, new)
        print(f"\n=== {tag}: {what}")
        print("    md5 after:", after)
        print("   ", run(scope))
        for path, _, _ in edits:
            path.write_bytes(base[path][1])
        purge()
        for path, _, _ in edits:
            assert md5(path) == base[path][0], f"{path} not restored"
        print("    restored, md5 re-checked OK")
    print("\nFINAL gate suite:", run(GATE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
