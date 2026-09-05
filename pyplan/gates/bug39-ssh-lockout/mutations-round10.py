#!/usr/bin/env python3
"""Round 10's mutation driver for bug 39, run on the COMMITTED blob.

Every run is against the bytes `git show <SHA>:pylauncher/yulon/networking.py`
produces -- a `git clone --shared` checkout on Linux, LF, never a scp'd Windows
working copy.  The guard printed for each restore is the blob hash
(`git hash-object`) plus the sha256 of the bytes on disk.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / "yulon-runs" / "fix10-bug39"
PYL = ROOT / "pylauncher"
MOD = PYL / "yulon" / "networking.py"
PYTEST = [str(PYL / ".venv" / "bin" / "python"), "-m", "pytest", "-q", "tests/test_networking.py"]

MUTATIONS = [
    (
        "M1 buy the separating read in where_the_reading_came_from()",
        "    if initial is False:\n",
        '    if initial is False and os.stat("/proc/1/ns/pid").st_ino != _INITIAL_PID_NAMESPACE_INO:\n',
    ),
    (
        "M2 in_initial_pid_namespace() asks /proc/1 instead of /proc/self",
        '        return os.stat("/proc/self/ns/pid").st_ino == _INITIAL_PID_NAMESPACE_INO\n',
        '        return os.stat("/proc/1/ns/pid").st_ino == _INITIAL_PID_NAMESPACE_INO\n',
    ),
]


def purge() -> None:
    for cache in PYL.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def blob_id(path: Path) -> str:
    return subprocess.run(
        ["git", "hash-object", str(path)], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def guard(path: Path) -> str:
    raw = path.read_bytes()
    endings = "CRLF" if bytes([13, 10]) in raw else "LF"
    return (
        f"{len(raw)} bytes, {endings}, sha256 {hashlib.sha256(raw).hexdigest()}, "
        f"git hash-object {blob_id(path)}"
    )


def run() -> str:
    purge()
    out = subprocess.run(PYTEST, cwd=PYL, capture_output=True, text=True)
    purge()
    tail = [ln for ln in out.stdout.splitlines() if ln.strip()]
    return "\n".join(tail[-14:])


def main() -> int:
    original = MOD.read_bytes()
    committed = subprocess.run(
        ["git", "show", "HEAD:pylauncher/yulon/networking.py"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"HEAD {sha}")
    print(f"on-disk == committed blob: {original == committed}")
    print(f"GUARD (the blob every mutation below was run on): {guard(MOD)}")
    print()
    print("=== BASELINE ===")
    print(run())
    print()
    for name, anchor, replacement in MUTATIONS:
        count = original.decode().count(anchor)
        print(f"=== {name} ===")
        print(f"anchor occurrences in the committed bytes: {count}")
        assert count == 1, f"anchor is not unique: {count}"
        mutated = original.decode().replace(anchor, replacement).encode()
        MOD.write_bytes(mutated)
        on_disk = MOD.read_text()
        assert replacement in on_disk, "substitution not on disk"
        assert anchor not in on_disk, "original still on disk"
        print(f"mutated blob: {guard(MOD)}")
        print(run())
        MOD.write_bytes(original)
        assert MOD.read_bytes() == original, "restore did not reproduce the original bytes"
        print(f"restored: {guard(MOD)}")
        print()
    print("=== RESTORE CHECK ===")
    print(run())
    print(f"final: {guard(MOD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
