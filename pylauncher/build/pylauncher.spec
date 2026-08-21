# PyInstaller spec for the Yu'lon launcher (pyplan/README.md §4, roadmap 5.2).
#
# One-dir build per platform; the release workflow wraps dist/yulon into an
# AppImage (Linux), zips it (Windows) or builds a .dmg (macOS). Everything the
# app reads at runtime is listed as data so `yulon.resources` finds it under
# sys._MEIPASS with the SAME relative names as in the source tree:
#   manifests/            -> <bundle>/manifests
#   ../archive/guides/**  -> <bundle>/archive/guides/**   (install scripts only)
# The app bundles a self-contained Python + PySide6 + pydantic; end users never
# install Python (README §3b).

import glob
import os

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))          # build/
ROOT = os.path.abspath(os.path.join(HERE, ".."))        # pylauncher/
REPO = os.path.abspath(os.path.join(ROOT, ".."))        # repository root

block_cipher = None

# The install scripts the Phase 3a installer drives (catalog.json install.script
# paths) plus the helpers they source/install. Kept to the scripts themselves —
# archive/guides also holds guides, MPQs and DBCs we must not ship (README §3a).
script_globs = [
    "archive/guides/wow-wotlk/install-wow-wotlk*.sh",
    "archive/guides/wow-wotlk/dml-start.sh",
    "archive/guides/wow-wotlk/wow-manage.sh",
    "archive/guides/wow-tbc/install-wow-tbc.sh",
    "archive/guides/wow-vanilla/install-wow-vanilla.sh",
    "archive/guides/work-in-progress/install-tortoise-wow-WSL.sh",
]
datas = [
    (os.path.join(ROOT, "manifests"), "manifests"),
    # Non-Python package data: the catalog lives next to its models.
    (os.path.join(ROOT, "yulon", "catalog", "catalog.json"), os.path.join("yulon", "catalog")),
]
for pattern in script_globs:
    for path in glob.glob(os.path.join(REPO, pattern)):
        rel_dir = os.path.relpath(os.path.dirname(path), REPO)
        datas.append((path, rel_dir))

hiddenimports = collect_submodules("yulon") + ["pydantic", "pydantic_core"]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick3D"],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="yulon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="yulon",
)

if os.name == "posix" and os.uname().sysname == "Darwin":
    app = BUNDLE(
        coll,
        name="Yulon.app",
        icon=None,
        bundle_identifier="org.dadsmmolab.yulon",
    )
