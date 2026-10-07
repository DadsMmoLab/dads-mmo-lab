"""Yu'lon — Dad's MMO Lab unified launcher.

Top-level package. See pyplan/README.md for the full design document.
"""

import importlib

_FALLBACK_VERSION = "0.9.14-Public"


def _stamped_version() -> str | None:
    try:
        module = importlib.import_module("yulon._build_version")
    except ImportError:
        return None
    version = getattr(module, "VERSION", None)
    return version if isinstance(version, str) and version else None


__version__ = _stamped_version() or _FALLBACK_VERSION
