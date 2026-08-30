"""`python -m yulon` — the same entry point as `main.py`.

Every box in the 2026-08-28 run tried `python -m yulon` first and got
"No module named yulon.__main__", because the entry point is the sibling
`main.py` (which is what PyInstaller freezes). This is a redirect, not a
second entry point, so there is nothing here for the two to disagree about.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `main.py` sits beside the package, not inside it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import main  # noqa: E402

raise SystemExit(main())
