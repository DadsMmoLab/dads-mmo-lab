"""T554: the WoW Unbound entry's own files.

Step Y1: the four core patches the entry carries, pinned byte for byte. A patch that
drifts by one byte (an editor's CRLF, a re-diffed hunk, a swapped order) would change
what a player's core is built from, so each file's sha256 is written down here as a
constant and the test fails on any difference.
"""

from __future__ import annotations

import hashlib

import pytest

from yulon import resources

PATCH_DIR = resources.installers_dir() / "wow-unbound" / "patches"

# name -> sha256, in the order the entry applies them (plan 2026-10-08 §2.2).
# 01 is the `mod-unbound` branch's core-patch/unbound-core-access.patch, whose sha256
# is the one in that branch's MANIFEST.sha256 at d29fac97.
UNBOUND_PATCHES: dict[str, str] = {
    "01-unbound-core-access.patch": "f20915389825e73dc6d4fbb4f420e30e6e27b37ddb57a1fcf399e39986449813",  # noqa: E501
    "02-player-learntalent.patch": "07cbaeeb7840aaff1658d86aec5967ca23bc2653be936b7aee7b1fa9b699f034",  # noqa: E501
    "03-feral-spirit-coexist.patch": "0b172ff6b18fbe2e6551a189781b26ab6f673930edce9c3ec1c3b0e544c7bec1",  # noqa: E501
    "04-pet-commands.patch": "7be5aed89bdfff62ac06d086198c41c7c7ea04be194e620ac6b2d83c93e5a4c6",
}


def test_the_patch_folder_holds_exactly_the_four_patches_in_order() -> None:
    assert sorted(p.name for p in PATCH_DIR.glob("*")) == list(UNBOUND_PATCHES)


@pytest.mark.parametrize(("name", "digest"), UNBOUND_PATCHES.items())
def test_every_unbound_patch_is_shipped_byte_exact(name: str, digest: str) -> None:
    data = (PATCH_DIR / name).read_bytes()
    assert b"\r" not in data, f"{name} must be LF-only"
    assert hashlib.sha256(data).hexdigest() == digest
