"""What the install plans every public release shipped applied, phase by phase (T129).

Data, not family code, and kept out of `families/` for that reason: it names
games' phases, and a family module names no game (`test_catalog_invariants`).
`sqlplan.MarkerGate.phase_ledger()` reads it for an install whose marker was
written before T129, which carries a plan hash and no per-phase rows.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["RELEASED_PHASE_DIGESTS"]

RELEASED_PHASE_DIGESTS: Mapping[str, Mapping[str, str]] = {
    # wow-tbc, v0.8.0-Public through v0.8.90-Public.
    "b64174fea797fad4": {
        "realmd base": "838571a497c07875",
        "characters base": "d0040e09e2cdc246",
        "logs base": "69a4008f60b4cbc4",
        "world content": "05bdcd9bfba07206",
        "content updates": "e4a3af493440852a",
        "ACID": "7b95102e4a617c00",
        "dbc data": "92e87b8904aed5f5",
        "core updates": "60f19ef19945d487",
        "spell_template hotfix": "19d9fdffb22947a8",
        "playerbots characters": "4f1f6075fac03500",
        "playerbots world": "bd44067edb55197f",
        "expansion unlock": "c2a7b24c6880b8bb",
    },
    # wow-vanilla, v0.8.0-Public through v0.8.90-Public.
    "5b654233d1dd2279": {
        "realmd base": "574261f5a3f56d91",
        "characters base": "3f62c6e71f766da8",
        "logs base": "f773bae22c9a507f",
        "world content": "4a52f74be32b828e",
        "content updates": "3ea4bcff52d85906",
        "ACID": "014cd83e4525de14",
        "core updates": "2283ba93fb2c9e7c",
        "spell_template hotfix": "19d9fdffb22947a8",
        "playerbots characters": "fa74d976e6c70230",
        "playerbots world": "ac3d0fa7d2bdbf3f",
    },
    # wow-tortoise, v0.8.4-Public through v0.8.90-Public (the Penqle core, T30).
    "6f0ae5810a40956a": {
        "schemas": "8e2470fb857a9e9b",
        "app user and grants": "cfb03b4dbf18e085",
        "world base": "bfb0fdc12b182c26",
        "realm row": "7363b1388e033f65",
    },
}
"""What an install marked before T129 has, keyed by the plan hash its marker row holds.

Those markers carry one hash over the whole plan and no phase rows, so the hash
is the only thing saying which version of each phase went in. These are the
only plan hashes any public release marked an install with -- measured
2026-09-27 by running each tag's own `plan_hash()` over its own catalog -- and
each maps to its plan's phase digests. Frozen: every marker written from T129 on
carries its own rows. A hash not here (a development build, or the retired
Tortoise core of v0.8.0-Public, `8b60e764371f2293`, whose phases name
directories the current core does not have) reads as knowing nothing, and
nothing is offered to that install.
"""
