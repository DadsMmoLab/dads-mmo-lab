# PyInstaller spec for the Yu'lon launcher.
# Produces a single binary per platform (.AppImage/.dmg/.exe).
# See pyplan/README.md §4.

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("py")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("manifests", "manifests"),
        ("py", "py"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="yulon",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="yulon",
)