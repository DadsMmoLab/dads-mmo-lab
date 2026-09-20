"""Write `yulon/_build_version.py` from the tag a release is built from.

`yulon.__version__` was hand-edited and drifted from the tag (0.8.66-Public in a
build tagged v0.8.68). The self-update check compares that string with the tag,
so the bundle now carries the tag itself. A ref that is not a version tag (a
`workflow_dispatch` build of a branch) writes nothing and the hand-written
fallback stays. Exits 0 either way.

Stdlib only, and no import from `yulon`: it runs before the bundle is built.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

_TAG = re.compile(r"^v(\d+\.\d+\.\d+(?:[-A-Za-z0-9.]*)?)$")
_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "yulon" / "_build_version.py"


def version_from_ref(ref: str) -> str | None:
    """The version a `v*` tag carries, or None for anything else (a branch name)."""
    match = _TAG.match(ref.strip())
    return match[1] if match else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args(argv)
    version = version_from_ref(args.ref)
    if version is None:
        print(f"{args.ref!r} is not a version tag: no stamp written")
        return 0
    args.out.write_text(
        f'"""Written by build/stamp_version.py."""\n\nVERSION = "{version}"\n', encoding="utf-8"
    )
    print(f"stamped {version} into {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
