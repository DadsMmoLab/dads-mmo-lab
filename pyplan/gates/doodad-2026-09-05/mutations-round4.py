"""Every mutation for the doodad round-4 fix, re-run on the final tree.

Anchor occurrences asserted ==1, md5 checked on disk before and after, __pycache__
purged on both sides of each substitution, the original restored and its md5
re-verified. Scored against tests/test_extract.py + tests/test_families_cmangos.py
unless the case says otherwise.
"""
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/home/pk/yulon-runs/fix8-doodad/pylauncher")
PY = str(ROOT / ".venv/bin/python")
NL = "\n"
POLLUTED = "Your output directory seems to be polluted, please use an empty directory!"


def purge():
    for d in ROOT.rglob("__pycache__"):
        if ".venv" not in str(d):
            shutil.rmtree(d, ignore_errors=True)


def md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


GUARD = (
    "yulon/catalog/families/extract.py",
    "        blocked = blocking_output(tool, data_dir)" + NL
    + "        if blocked is not None:" + NL
    + "            raise InstallerError(blocked_message(tool, blocked))" + NL,
    "",
)
DOUBLE_REFUSES = (
    "tests/test_extract.py",
    "            if any((buildings / marker).exists() for marker in extract.DIRTY_MARKERS):" + NL
    + '                words = "' + POLLUTED + '"' + NL
    + "                sink(words)" + NL
    + "                return docker.AttachedRun(1, (words,))" + NL,
    "",
)
EMPTY_ON_RETRY = (
    "yulon/catalog/families/extract.py",
    "                empty_out_dirs(again.produces, data_dir)" + NL,
    "",
)

CASES = [
    ("M1 the run_plan guard removed", [GUARD]),
    (
        "M2 the remedy names the evidence file only",
        [(
            "yulon/catalog/families/cmangos.py",
            'doomed = " and ".join(str(path) for path in (evidence, *blocking))',
            'doomed = " and ".join(str(path) for path in (evidence,))',
        )],
    ),
    (
        "M3 blocking_output keyed on the folder alone, not the binary",
        [(
            "yulon/catalog/families/extract.py",
            '    if tool.argv[0].rsplit("/", 1)[-1] != DIRTY_OUTPUT_TOOL:' + NL
            + "        return None" + NL,
            "",
        )],
    ),
    (
        "M4 the Recorder double stops writing dir/dir_bin",
        [(
            "tests/support_native.py",
            "            if extractor and buildings is not None:" + NL
            + "                for marker in extract.DIRTY_MARKERS:" + NL,
            "            if False:" + NL
            + "                for marker in extract.DIRTY_MARKERS:" + NL,
        )],
    ),
    (
        "M5 the test_extract Runner stops naming its output dir/dir_bin",
        [(
            "tests/test_extract.py",
            "            marked = extract.DIRTY_MARKERS if extractor and folder == "
            "extract.BUILDINGS_DIR else ()",
            "            marked = ()",
        )],
    ),
    ("M6 the test_extract Runner stops refusing a polluted folder", [DOUBLE_REFUSES]),
    (
        "M10 the refusal lists both markers instead of the ones on disk",
        [(
            "yulon/catalog/families/extract.py",
            "    present = [marker for marker in DIRTY_MARKERS if (folder / marker).exists()]",
            "    present = list(DIRTY_MARKERS)",
        )],
    ),
    ("M1+M6 the guard AND the double's refusal both gone", [GUARD, DOUBLE_REFUSES]),
    ("M7 the retry pass stops emptying (the double still refuses)", [EMPTY_ON_RETRY]),
    ("M7+M6 the retry stops emptying AND the double stops refusing", [EMPTY_ON_RETRY,
                                                                      DOUBLE_REFUSES]),
]

TESTS = ["tests/test_extract.py", "tests/test_families_cmangos.py"]

for title, muts in CASES:
    print(NL + "=== " + title)
    saved = {}
    for rel, old, new in muts:
        path = ROOT / rel
        src = path.read_bytes().decode("utf-8")
        count = src.count(old)
        print("    " + rel + "  anchor occurrences = " + str(count))
        assert count == 1, "ANCHOR NOT UNIQUE (" + str(count) + ") in " + rel
        saved[rel] = (src, md5(path))
        path.write_bytes(src.replace(old, new).encode("utf-8"))
        assert path.read_bytes().decode("utf-8").count(old) == 0
        print("      md5 " + saved[rel][1] + " -> " + md5(path) + "  (on disk)")
    purge()
    out = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    failed = [line.split(" - ")[0] for line in out.stdout.splitlines() if line.startswith("FAILED")]
    tail = [line for line in out.stdout.splitlines() if " passed" in line or " failed" in line]
    print("    RED = " + str(len(failed)))
    for line in failed:
        print("      " + line)
    print("      " + tail[-1])
    for rel, old, new in muts:
        path = ROOT / rel
        src, digest = saved[rel]
        path.write_bytes(src.encode("utf-8"))
        assert md5(path) == digest, "restore failed for " + rel
    purge()
    print("    restored")

purge()
out = subprocess.run([PY, "-m", "pytest", *TESTS, "-q", "--no-header"], cwd=ROOT,
                     capture_output=True, text=True)
print(NL + "=== control after every restore: " + out.stdout.strip().splitlines()[-1])
