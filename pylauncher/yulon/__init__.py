"""Yu'lon — Dad's MMO Lab unified launcher.

Top-level package. See pyplan/README.md for the full design document.
"""

import importlib

_FALLBACK_VERSION = "0.8.70-Public"
"""What a checkout, and a build of a branch, reports. Hand-edited as before."""


def _stamped_version() -> str | None:
    """The tag a packaged build was cut from (`build/stamp_version.py`), if any.

    `importlib`, not an `import` statement: the module does not exist in a
    checkout, and mypy's strict mode rejects an import it cannot find while
    `warn_unused_ignores` rejects the ignore on the box where it can.

    What puts it in the bundle is `build/pylauncher.spec` naming it: measured
    2026-09-21, `collect_submodules("yulon")` collects nothing at all, because
    PyInstaller runs a spec with the package off `sys.path`. v0.8.69-fixtest
    shipped reporting the fallback for exactly that reason.
    """
    try:
        module = importlib.import_module("yulon._build_version")
    except ImportError:
        return None
    version = getattr(module, "VERSION", None)
    return version if isinstance(version, str) and version else None


__version__ = _stamped_version() or _FALLBACK_VERSION
