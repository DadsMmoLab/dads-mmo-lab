"""The releases feed the update tests judge, in one place (T90).

Two test files ask questions of the same feed — `test_update.py` about what a
single answer means, `test_update_cache.py` about how often it is asked for —
and a feed that drifted between them would let one of the two pass about a
repository the other no longer describes.

Its shape is the one GitHub really answers for this repo: every entry a
prerelease, ordered newest-first BY CREATION rather than by version, test tags
and public tags side by side in it, and a draft that nobody can download.
"""

from __future__ import annotations

import json


def release(
    tag: str,
    *,
    body: str = "",
    draft: bool = False,
    assets: tuple[str, ...] = (),
) -> dict[str, object]:
    """One entry of the releases feed, trimmed to the fields the check reads."""
    return {
        "tag_name": tag,
        "prerelease": True,
        "draft": draft,
        "html_url": f"https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/{tag}",
        "body": body,
        "assets": [
            {
                "name": n,
                "browser_download_url": f"https://example.invalid/{tag}/{n}",
                "size": 10 + i,
            }
            for i, n in enumerate(assets)
        ],
    }


FEED = json.dumps(
    [
        release("v0.8.71-fixtest", body="test build"),
        release("v0.8.69-Public", body="### Fixed\n- Nine."),
        release(
            "v0.8.70-Public",
            body="### New\n- Ten.",
            assets=("Yulon-v0.8.70-Public-x86_64.AppImage", "SHA256SUMS"),
        ),
        release("v0.8.5-DeckTest"),
        release("v0.8.72-Public", draft=True),
        release("v0.8.66-Public", body="### New\n- Six."),
        release("v0.6.59Public"),
    ]
)
"""The feed both update test files read. Newest by creation is NOT newest by version."""
