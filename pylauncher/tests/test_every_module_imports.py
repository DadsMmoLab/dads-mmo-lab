"""Every module of `yulon` imports on the interpreter this runs on (T687).

CI's `platform-smoke` job runs this on real Windows and macOS. The mypy passes
in `lint-type-test` check the other platforms' imports by SIMULATING them
(`--platform win32`), which is how a `pwd` import once went green on one OS and
red on the other; a real import on the real OS is the check that cannot be
simulated away. On Linux it is cheap, because the rest of the suite imports
these modules anyway.

The names come from the files, not from `pkgutil.walk_packages`: walking imports
each package to find what is under it, so one broken package would hide its
whole subtree from the list instead of failing by name.

`yulon.__main__` is left out (importing it starts the app) and `main` is put in
its place: it is the entry point PyInstaller freezes, and `__main__` only
re-exports it.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import yulon

PACKAGE_ROOT = Path(yulon.__file__).resolve().parent


def _module_names() -> list[str]:
    names = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        parts = path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
        if parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(parts))
    return ["main", *names]


MODULES = _module_names()


def test_the_list_is_the_package() -> None:
    """An empty or partial list would make every case below pass by not existing."""
    assert "yulon" in MODULES
    assert "main" in MODULES
    assert "yulon.__main__" not in MODULES, "importing it would start the app"
    assert "yulon.platform" in MODULES
    assert "yulon.winacl" in MODULES
    assert "yulon.selfupdate.layout" in MODULES
    assert len(MODULES) > 100, f"only {len(MODULES)} modules found under {PACKAGE_ROOT}"


@pytest.mark.parametrize("name", MODULES)
def test_the_module_imports(name: str) -> None:
    importlib.import_module(name)
