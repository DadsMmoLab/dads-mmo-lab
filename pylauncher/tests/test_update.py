"""Tests for the self-update check (`yulon.update`, README §10): check + notify only."""

from __future__ import annotations

import email.message
import json
import urllib.error

import pytest

from yulon.update import RELEASES_PAGE, check_for_update, is_newer, parse_version

# The releases feed as GitHub really answers it for this repo: newest first, and
# every single entry flagged `prerelease`. Trimmed to the fields the check reads.
REAL_FEED = json.dumps(
    [
        {
            "tag_name": "v0.6.57Public",
            "prerelease": True,
            "draft": False,
            "html_url": "https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.6.57Public",
        },
        {
            "tag_name": "v0.6.55Public",
            "prerelease": True,
            "draft": False,
            "html_url": "https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.6.55Public",
        },
        {
            "tag_name": "v0.6.53",
            "prerelease": True,
            "draft": False,
            "html_url": "https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.6.53",
        },
    ]
)


def fake_github(url: str) -> str:
    """Both release endpoints, answering the way GitHub answers them for this repo."""
    if url.endswith("/releases/latest"):
        # `/releases/latest` means "latest non-prerelease". There isn't one, so
        # GitHub has 404'd this call for every user since the feature shipped.
        raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
    if "/releases" in url:
        return REAL_FEED
    raise AssertionError(f"the check asked for an endpoint nobody serves: {url}")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.2.3", (1, 2, 3)),
        ("v0.1.4-rc1", (0, 1, 4)),
        ("nightly", None),
        ("", None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert parse_version(text) == expected


def test_is_newer_compares_numerically_not_lexically() -> None:
    assert is_newer("v0.10.0", "0.9.9") is True
    assert is_newer("v0.1.4", "0.1.4") is False
    assert is_newer("garbage", "0.1.4") is False


def test_a_feed_of_nothing_but_prereleases_still_yields_an_update() -> None:
    """The bug: every release upstream cuts is a prerelease, so `/releases/latest` 404s.

    The check must ask the endpoint that lists them and take the newest, or it
    finds nothing for anybody — which is what it did for its whole life.
    """
    result = check_for_update("0.6.53", http_get=fake_github)

    assert result.available is True
    assert result.latest == "v0.6.57Public"
    assert result.url.endswith("/releases/tag/v0.6.57Public")
    assert result.error is None


def test_a_draft_is_not_a_release_anyone_can_download() -> None:
    """Drafts sit at the top of the feed and are invisible to users; skip past them."""
    feed = json.dumps(
        [
            {"tag_name": "v9.9.9", "draft": True, "html_url": "https://example.test/draft"},
            {"tag_name": "v0.6.57Public", "draft": False, "html_url": "https://example.test/57"},
        ]
    )

    result = check_for_update("0.6.53", http_get=lambda url: feed)

    assert result.latest == "v0.6.57Public" and result.available is True


def test_a_re_cut_of_an_old_tag_is_not_offered_as_an_upgrade() -> None:
    """The feed is ordered by creation date, not by version, so newest can be older."""
    feed = json.dumps(
        [
            {"tag_name": "v0.6.10", "draft": False, "html_url": "https://example.test/10"},
            {"tag_name": "v0.6.57Public", "draft": False, "html_url": "https://example.test/57"},
        ]
    )

    result = check_for_update("0.6.57", http_get=lambda url: feed)

    assert result.latest == "v0.6.10" and result.available is False


def test_check_reports_available_only_for_a_newer_release() -> None:
    payload = json.dumps([{"tag_name": "v9.9.9", "html_url": "https://example.test/rel/v9.9.9"}])
    result = check_for_update("0.1.4", http_get=lambda url: payload)
    assert result.available is True and result.latest == "v9.9.9"
    assert result.url == "https://example.test/rel/v9.9.9" and result.error is None

    same = check_for_update("9.9.9", http_get=lambda url: payload)
    assert same.available is False and same.latest == "v9.9.9"


def test_check_degrades_cleanly_offline_or_on_odd_payloads() -> None:
    def offline(url: str) -> str:
        raise OSError("no network")

    off = check_for_update("0.1.4", http_get=offline)
    assert off.available is False and off.latest is None and off.url == RELEASES_PAGE
    assert off.error is not None and "no network" in off.error

    odd = check_for_update("0.1.4", http_get=lambda url: json.dumps([{"tag_name": "latest"}]))
    assert odd.available is False and "unrecognized" in (odd.error or "")

    broken = check_for_update("0.1.4", http_get=lambda url: "<html>rate limited</html>")
    assert broken.available is False and broken.error is not None

    # A rate-limited answer is valid JSON, just not a feed: `{"message": "API rate
    # limit exceeded", ...}`. Nothing published in it, and nothing to crash on.
    throttled = check_for_update("0.1.4", http_get=lambda url: json.dumps({"message": "slow down"}))
    assert throttled.available is False and throttled.latest is None
    assert throttled.error is not None and throttled.url == RELEASES_PAGE

    empty = check_for_update("0.1.4", http_get=lambda url: "[]")
    assert empty.available is False and empty.latest is None and empty.error is not None
