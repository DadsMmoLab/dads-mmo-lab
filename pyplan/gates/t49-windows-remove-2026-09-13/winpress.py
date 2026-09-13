"""T49 Windows press: a REAL git pack, the real attribute, both paths."""
import os, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\Users\pk\dads-mmo-lab\pylauncher")))
from yulon import rmtree as yulon_rmtree

def make_clone(where: Path) -> Path:
    """A real checkout with a real pack, made by real git."""
    where.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=where, check=True)
    (where / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=where, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "x"], cwd=where, check=True)
    # force loose objects into a pack; git writes packs read-only
    subprocess.run(["git", "gc", "-q", "--aggressive"], cwd=where, check=True)
    return where

def readonly_report(root: Path) -> list[str]:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and not (p.stat().st_mode & stat.S_IWRITE):
            out.append(str(p.relative_to(root)))
    return out

base = Path(tempfile.mkdtemp(prefix="t49-"))
print(f"scratch: {base}")

a = make_clone(base / "with-shutil")
ro = readonly_report(a)
print(f"read-only files git left: {len(ro)}")
for f in ro[:4]:
    print(f"   {f}")
if not ro:
    print("!! git left nothing read-only here -- the press proves nothing")

print("\n--- shutil.rmtree (what shipped before T49) ---")
try:
    shutil.rmtree(a)
    print("RESULT: it deleted the tree (no read-only stop on this box)")
except OSError as exc:
    print(f"RESULT: FAILED -> {type(exc).__name__}: {exc}")
    print(f"        still on disk: {a.exists()}")
    left = sum(1 for _ in a.rglob('*')) if a.exists() else 0
    print(f"        entries left behind: {left}  <-- half-deleted")

b = make_clone(base / "with-remove-tree")
print("\n--- yulon.rmtree.remove_tree (T49) ---")
try:
    yulon_rmtree.remove_tree(b)
    print(f"RESULT: OK, gone = {not b.exists()}")
except OSError as exc:
    print(f"RESULT: FAILED -> {type(exc).__name__}: {exc}")

# tidy
for leftover in (a, b, base):
    if leftover.exists():
        try:
            yulon_rmtree.remove_tree(leftover)
        except OSError:
            pass
print(f"\nscratch cleaned: {not base.exists()}")
