# PyInstaller spec for the Yu'lon launcher (pyplan/README.md §4, roadmap 5.2).
#
# One-dir build per platform; the release workflow wraps dist/yulon into an
# AppImage (Linux), zips it (Windows) or builds a .dmg (macOS). Everything the
# app reads at runtime is listed as data so `yulon.resources` finds it under
# sys._MEIPASS with the SAME relative names as in the source tree:
#   manifests/            -> <bundle>/manifests
#   catalog/installers/** -> <bundle>/catalog/installers/**
#   lua/**                -> <bundle>/lua/**
# The app bundles a self-contained Python + PySide6 + pydantic; end users never
# install Python (README §3b).

import os
import sys

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
    # `lua/` is My Party's server-side bridge (8.6): scripts the app copies into
    # somebody's server folder, never imports. A tree for the same reason as
    # above -- a family added under it must not need an edit here. Left out, the
    # bridge would deploy from a checkout and find nothing from a release build,
    # which is precisely the failure `party.deploy` refuses to report as success.
    (os.path.join(ROOT, "lua"), "lua"),
    # Non-Python package data: the catalog lives next to its models.
    (os.path.join(ROOT, "yulon", "catalog", "catalog.json"), os.path.join("yulon", "catalog")),
]

# certifi is imported lazily inside `yulon.platform.verify_context()`; naming it
# here (PyInstaller's own hook then collects `cacert.pem` as data) is what keeps
# the verified-download fallback working in a frozen build. Without it the frozen
# app falls back to the OS root store alone — still verifying, but missing the
# roots a fresh Windows install has not materialized yet.
# pygame is imported lazily inside `yulon.ui.gamepad._GamepadWorker.run()`, so
# static analysis recovers no reference to it; naming it lets PyInstaller's
# pygame hook collect the SDL2 shared libraries the joystick reader links at
# runtime (Windows ships `SDL2.dll`, macOS bundles the SDL2 framework).
# `collect_submodules("yulon")` RETURNED NOTHING FOR AS LONG AS IT HAS BEEN
# HERE, and said nothing about it. Measured on a real build 2026-09-21, after a
# fork release tag shipped a bundle that reported the wrong version:
#
#   MEASURE: collect_submodules found 0 modules
#   MEASURE: cwd=.../pylauncher
#   MEASURE: ROOT on sys.path: False
#   MEASURE: `import yulon` in the spec process: ModuleNotFoundError
#   MEASURE: with ROOT on sys.path, collect_submodules found 107 modules
#
# PyInstaller runs a spec with a sanitized `sys.path`: the working directory is
# `pylauncher/`, but neither it nor `ROOT` is on the path, so the isolated
# import `collect_submodules` does fails, and the helper answers with an empty
# list. Not even `on_error="raise"` raises - it returned 0 too - so there is no
# setting of it that would have complained.
#
# Every `yulon.*` module in the bundle got there because modulegraph followed a
# real `import` statement out of `main.py`, which works because `pathex` below
# puts ROOT on the analysis path. That is why nobody noticed: the ONE module
# this list was meant to catch is one that no `import` statement names.
sys.path.insert(0, ROOT)
hiddenimports = collect_submodules("yulon") + [
    "pydantic",
    "pydantic_core",
    "certifi",
    "pygame",
]

# AND THE STAMP BY NAME, not by trusting the machinery above a second time.
# `build/stamp_version.py` writes `yulon/_build_version.py` in the release job
# before this runs, and `yulon/__init__.py` reads it through
# `importlib.import_module` - a form modulegraph cannot follow, by design, since
# the module does not exist in a checkout. So it is named here when it exists,
# which is the only reason it reaches the bundle on all three runners.
#
# The shipped v0.8.69-fixtest bundle is what this is for: all three build jobs
# logged "stamped 0.8.69-fixtest", and the app it produced reported 0.8.66-Public
# because `grep -c yulon._build_version` over the frozen executable was 0.
# `tests/test_build_version.py` pins this name against the path the stamper
# writes; only a real build proves the rest, which is what the release job's
# version-check step is for.
_stamp = os.path.join(ROOT, "yulon", "_build_version.py")
if os.path.exists(_stamp):
    hiddenimports.append("yulon._build_version")

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

ico_path = os.path.join(ROOT, "assets", "yulon.ico")
icns_path = os.path.join(ROOT, "assets", "yulon.icns")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="yulon",
    icon=ico_path if os.path.exists(ico_path) else None,
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
        icon=icns_path if os.path.exists(icns_path) else None,
        bundle_identifier="org.dadsmmolab.yulon",
    )
