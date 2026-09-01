# PyInstaller spec for the Yu'lon launcher (pyplan/README.md §4, roadmap 5.2).
#
# One-dir build per platform; the release workflow wraps dist/yulon into an
# AppImage (Linux), zips it (Windows) or builds a .dmg (macOS). Everything the
# app reads at runtime is listed as data so `yulon.resources` finds it under
# sys._MEIPASS with the SAME relative names as in the source tree:
#   manifests/            -> <bundle>/manifests
#   catalog/installers/** -> <bundle>/catalog/installers/**
# The app bundles a self-contained Python + PySide6 + pydantic; end users never
# install Python (README §3b).

import os

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))          # build/
ROOT = os.path.abspath(os.path.join(HERE, ".."))        # pylauncher/

block_cipher = None

# `catalog/installers/` is data the app reads at runtime, and it ships as a TREE
# rather than as a list of files: the families' compose templates
# (`<game>/native/` and `shared/<family>/`) and the Steam Deck gaming-mode
# script (`steam-deck/setup-gaming-mode.sh`, added in 7.2 as the shell file the
# app still carries). Shipping the tree is why 7.3 could add `shared/cmangos/`
# and the per-game `native/Dockerfile.tmpl` without touching this file, and why
# 7.2's deletions need no edit here either — a per-file list would have gone
# stale at both. `archive/guides/` stays out of the bundle entirely (it holds
# guides, MPQs and DBCs we must not ship, README §3a).
datas = [
    (os.path.join(ROOT, "manifests"), "manifests"),
    (os.path.join(ROOT, "catalog", "installers"), os.path.join("catalog", "installers")),
    # Non-Python package data: the catalog lives next to its models.
    (os.path.join(ROOT, "yulon", "catalog", "catalog.json"), os.path.join("yulon", "catalog")),
]

# certifi is imported lazily inside `yulon.platform.verify_context()`; naming it
# here (PyInstaller's own hook then collects `cacert.pem` as data) is what keeps
# the verified-download fallback working in a frozen build. Without it the frozen
# app falls back to the OS root store alone — still verifying, but missing the
# roots a fresh Windows install has not materialized yet.
hiddenimports = collect_submodules("yulon") + ["pydantic", "pydantic_core", "certifi"]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # `yulon.__main__` is the `python -m yulon` redirect for a CHECKOUT; it does
    # `from main import main`, so collect_submodules("yulon") would carry it into
    # the bundle and pull main.py in a second time as a library module named
    # `main` beside the real entry point (review, 2026-08-28). The frozen exe IS
    # main.py and never runs `-m yulon`.
    excludes=[
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.Qt3DCore",
        "PySide6.QtQuick3D",
        "yulon.__main__",
    ],
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
