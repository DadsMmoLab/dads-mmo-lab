#!/usr/bin/env python3
"""What a REFUSED press actually ran and changed, with the guard where it used to be.

Run in ~/yulon-runs/fix9-doodad/pylauncher AFTER mutations9.py has restored the
tree. It applies the un-hoist (mutations9.py's M2) itself, measures, and restores.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXTRACT = ROOT / "yulon/catalog/families/extract.py"

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

BODY = '''
import sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
from tests.test_extract import PLAN, FULL, Runner, run, _data_snapshot
from yulon.catalog.families import extract
from yulon.catalog.installer import InstallerError

tmp = Path(tempfile.mkdtemp())
run(PLAN, Runner(FULL), tmp)
data = tmp / "server" / "data"
(data / extract.EVIDENCE_FILE).unlink()
before = _data_snapshot(data)
blocked = Runner(FULL)
try:
    run(PLAN, blocked, tmp)
    print("NO REFUSAL")
except InstallerError as exc:
    print("REFUSED:", str(exc)[:140])
after = _data_snapshot(data)
changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
print("CONTAINERS RUN IN THE REFUSED PRESS:", blocked.names())
print("FILES UNDER data/ BEFORE:", len(before), " AFTER:", len(after))
print("CREATED-OR-REWRITTEN:", len(changed))
print("FIRST TEN:", changed[:10])
'''


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def purge() -> None:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def sub(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8", newline="")
    if "\r\n" in text:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    assert text.count(old) == 1, f"anchor occurs {text.count(old)}x"
    path.write_text(text.replace(old, new), encoding="utf-8", newline="")


def measure(label: str) -> None:
    purge()
    script = Path(tempfile.mkstemp(suffix=".py")[1])
    script.write_text(BODY, encoding="utf-8")
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(script)], cwd=ROOT, capture_output=True, text=True
    )
    print(f"----- {label}")
    print(proc.stdout or proc.stderr[-2000:])
    script.unlink()
    purge()


def main() -> int:
    original = EXTRACT.read_bytes()
    base = md5(EXTRACT)
    print("extract.py md5:", base)
    measure("AS SHIPPED (guard hoisted over the whole plan)")
    sub(EXTRACT, HOIST_OLD, "")
    sub(EXTRACT, LOOP_OLD, LOOP_NEW)
    print("extract.py md5 un-hoisted:", md5(EXTRACT))
    measure("UN-HOISTED (the guard asked per tool inside the loop)")
    EXTRACT.write_bytes(original)
    purge()
    assert md5(EXTRACT) == base, "not restored"
    print("restored, md5 re-checked:", md5(EXTRACT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
