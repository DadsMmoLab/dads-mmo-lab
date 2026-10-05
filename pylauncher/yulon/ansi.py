"""Terminal colour codes, and taking them out of text Yu'lon shows or keeps (T214).

AzerothCore's importer colours every line it prints (`ESC[36m` ... `ESC[0m`), and
a Qt text box draws none of it: PR 305's live check read "[0m[36m" at the start
of each line of the Modules tab's Last action box, in Details and in yulon.log.
One rule, here, so the run's output, the Details fold and the log file cannot
come to disagree about what a colour code is.
"""

from __future__ import annotations

import re

_CODES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
"""A CSI sequence: ESC [ parameters, intermediates, final byte. Colours are `ESC[…m`."""


def strip(text: str) -> str:
    """`text` without its terminal escape sequences."""
    return _CODES.sub("", text)
