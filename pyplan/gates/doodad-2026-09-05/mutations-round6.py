#!/usr/bin/env python3
"""Round-10 doodad mutations, run in ~/yulon-runs/fix10-doodad/pylauncher.

The sixth pass is text closure: two refusal sentences narrowed from a claim
about the PRESS to a claim about the stage that makes it, and one half of
`blocking_output()`'s OR that no test drove. Every mutation: anchor occurrence
asserted == 1, md5 before/after recorded, __pycache__ purged on BOTH sides,
file restored and the md5 re-checked.

Copy this file to `pylauncher/` and run it there -- `ROOT` is its own folder.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXTRACT = ROOT / "yulon/catalog/families/extract.py"
CMANGOS = ROOT / "yulon/catalog/families/cmangos.py"
TEST_EXTRACT = ROOT / "tests/test_extract.py"
TEST_CMANGOS = ROOT / "tests/test_families_cmangos.py"
CATALOG = ROOT / "yulon/catalog/catalog.json"
PY = str(ROOT / ".venv/bin/python")

TOUCHED = (EXTRACT, CMANGOS, TEST_EXTRACT, TEST_CMANGOS, CATALOG)
TWO_FILES = ["tests/test_extract.py", "tests/test_families_cmangos.py"]
GATE = ["-m", "not integration"]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def purge() -> None:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def run(args: list[str]) -> str:
    purge()
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
        {
            ln.split(" ")[1].split(" - ")[0]
            for ln in proc.stdout.splitlines()
            if ln.startswith("FAILED")
        }
    )
    return tail + ("\n    RED: " + "\n         ".join(reds) if reds else "")


def sub(path: Path, old: str, new: str) -> str:
    text = path.read_text(encoding="utf-8", newline="")
    if "\r\n" in text:  # the working copy is CRLF; the anchors below are written LF
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    assert text.count(old) == 1, f"anchor occurs {text.count(old)}x in {path.name}: {old!r}"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="")
    return md5(path)


NARROW = (
    '        f"it exits without writing anything). The extraction ran nothing and changed nothing "'
    '\n        f"under data/. "\n'
)
WIDE = '        f"it exits without writing anything). Nothing was run and nothing was changed. "\n'
GONE = '        f"it exits without writing anything). "\n'

STAGE_SAID = (
    '                "client\'s, if that is where the link goes. This stage ran nothing and '
    'removed "\n                f"nothing. Remove the link so {DATA_DIR} can be this install\'s '
    'own folder, or "\n'
)
STAGE_WIDE = (
    '                "client\'s, if that is where the link goes. Nothing was run and nothing was "'
    '\n                f"removed. Remove the link so {DATA_DIR} can be this install\'s own folder, '
    'or "\n'
)
STAGE_GONE = (
    '                "client\'s, if that is where the link goes. "\n'
    '                f"Remove the link so {DATA_DIR} can be this install\'s own folder, or "\n'
)

OR_ANCHOR = "    if any((folder / marker).exists() for marker in DIRTY_MARKERS):"
DIR_BIN_ONLY = "    if (folder / DIR_BIN).exists():"
NEW_ASSERT = (
    '    assert extract.blocking_output(VMAP, data) == buildings, '
    '"a lone `dir` did not stop the tool"\n'
)

# `"ulimit_stack_unlimited": true,` occurs once in catalog.json (wow-vanilla);
# the two `false` spellings are wow-tbc and wow-tortoise, so the marker is what
# makes this anchor one occurrence rather than three identical `produces` blocks.
VANILLA_AD_OLD = """              "ulimit_stack_unlimited": true,
              "tools": [
                {
                  "name": "dbc and maps",
                  "argv": [
                    "/opt/mangos/bin/tools/ad",
                    "-i",
                    "/client",
                    "-o",
                    "/out"
                  ],
                  "produces": {
                    "dbc": 100,
                    "maps": 100
                  }
"""
VANILLA_AD_NEW = VANILLA_AD_OLD.replace(
    '                    "maps": 100\n',
    '                    "maps": 100,\n                    "Buildings": 100\n',
)

MUTATIONS: list[tuple[str, str, list[tuple[Path, str, str]], list[str]]] = [
    (
        "MU1",
        "the narrowed sentence deleted from blocked_message",
        [(EXTRACT, NARROW, GONE)],
        GATE,
    ),
    (
        "MU2",
        "the sentence widened back to the press claim it was until this pass",
        [(EXTRACT, NARROW, WIDE)],
        GATE,
    ),
    (
        "MU3",
        "blocking_output narrowed to DIR_BIN alone -- the `dir` half of the tool's OR dropped",
        [(EXTRACT, OR_ANCHOR, DIR_BIN_ONLY)],
        GATE,
    ),
    (
        "MU4",
        "MU3 with this pass's new assertion removed: what round 9 was scoring",
        [(TEST_EXTRACT, NEW_ASSERT, ""), (EXTRACT, OR_ANCHOR, DIR_BIN_ONLY)],
        GATE,
    ),
    (
        "MU5",
        "_data_dir's stage sentence deleted",
        [(CMANGOS, STAGE_SAID, STAGE_GONE)],
        GATE,
    ),
    (
        "MU6",
        "_data_dir's sentence widened back to the press claim",
        [(CMANGOS, STAGE_SAID, STAGE_WIDE)],
        GATE,
    ),
    (
        "MU7",
        "wow-vanilla's `dbc and maps` made to produce Buildings too -- two tools, one folder",
        [(CATALOG, VANILLA_AD_OLD, VANILLA_AD_NEW)],
        GATE,
    ),
]


def blob(path: Path) -> str:
    """`git hash-object` of the file as it sits here -- the id it has in the commit.

    md5 of a working copy says nothing across machines: this tree's line
    endings are the checkout's, and a file copied here by `scp` or `rsync` is
    not the one the branch holds. The blob id is, so it is what the capture
    names and what a reader can check with
    `git rev-parse <commit>:pylauncher/...`.
    """
    out = subprocess.run(
        ["git", "hash-object", str(path)], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def main() -> int:
    only = sys.argv[1:]
    base = {p: (md5(p), p.read_bytes()) for p in TOUCHED}
    print("BLOB (git hash-object, unmutated):", {p.name: blob(p) for p in TOUCHED})
    print("BASELINE md5:", {p.name: base[p][0] for p in TOUCHED})
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
