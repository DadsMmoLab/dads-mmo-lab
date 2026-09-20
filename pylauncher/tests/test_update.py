"""Tests for the self-update check (`yulon.update`, README §10): what one answer means."""

from __future__ import annotations

import email.message
import json
import urllib.error
from pathlib import Path

import pytest

from tests.support_update import FEED, release
from yulon.update import (
    RELEASES_API,
    RELEASES_PAGE,
    RELEASES_REPO,
    check_for_update,
    evaluate_feed,
    is_newer,
    is_public_tag,
    parse_version,
    safe_release_url,
)


def fake_github(url: str) -> str:
    """Both release endpoints, answering the way GitHub answers them for this repo."""
    if url.endswith("/releases/latest"):
        # `/releases/latest` means "latest non-prerelease". There isn't one, so
        # GitHub has 404'd this call for every user since the feature shipped.
        raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
    if "/releases" in url:
        return FEED
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


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.8.70-Public", True),
        ("v0.8.70-public", True),
        ("v0.8.70-PUBLIC", True),
        ("v0.8.71-fixtest", False),
        ("v0.8.5-DeckTest", False),
        ("v0.6.60-phase8", False),
        ("v0.6.59Public", False),
        ("v0.6.59", False),
        ("0.8.70-Public", False),
        ("", False),
        ("v0.8.70-Public-rc1", False),
    ],
)
def test_only_a_dash_public_tag_is_a_release_for_players(tag: str, expected: bool) -> None:
    """The defect this closes: a test tag shares this feed and was offered to players.

    `-fixtest` and `-DeckTest` builds are cut from the same repository, and the
    check compared the numeric triple only — so the newest thing anybody had
    tagged became the update every user was told to install.
    """
    assert is_public_tag(tag) is expected


def test_a_test_tag_is_never_offered() -> None:
    result = evaluate_feed(FEED, "0.8.66-Public")
    assert result.latest == "v0.8.70-Public" and result.available


def test_the_newest_is_by_version_not_by_position_and_a_draft_does_not_count() -> None:
    """The feed is ordered by creation date, and a draft sits in it invisibly."""
    assert evaluate_feed(FEED, "0.8.0-Public").latest == "v0.8.70-Public"


def test_notes_cover_every_public_release_newer_than_mine_newest_first() -> None:
    notes = evaluate_feed(FEED, "0.8.66-Public").notes_markdown
    assert notes == (
        "## v0.8.70-Public\n\n### New\n- Ten.\n\n## v0.8.69-Public\n\n### Fixed\n- Nine.\n"
    )


def test_notes_stop_at_my_version() -> None:
    assert "Six." not in evaluate_feed(FEED, "0.8.66-Public").notes_markdown


def test_assets_and_the_checksum_file_are_carried() -> None:
    result = evaluate_feed(FEED, "0.8.66-Public")
    assert [a.name for a in result.assets] == ["Yulon-v0.8.70-Public-x86_64.AppImage", "SHA256SUMS"]
    assert result.assets[0].size == 10
    assert result.assets[0].url.endswith(".AppImage")
    assert result.has_checksums


def test_up_to_date_is_not_an_error() -> None:
    result = evaluate_feed(FEED, "0.8.70-Public")
    assert not result.available and result.error is None and result.latest == "v0.8.70-Public"
    assert result.notes_markdown == ""


def test_a_test_build_newer_than_every_public_release_is_offered_nothing() -> None:
    """A developer running `0.8.71-fixtest` is not told to downgrade to 0.8.70."""
    assert not evaluate_feed(FEED, "0.8.71-fixtest").available


def test_a_feed_with_no_public_release_says_so() -> None:
    result = evaluate_feed(json.dumps([release("v0.8.71-fixtest")]), "0.8.0")
    assert not result.available and result.error == "no public release"


def test_a_rate_limit_body_is_json_but_not_a_feed() -> None:
    """`{"message": "API rate limit exceeded"}` is valid JSON and nothing to offer."""
    result = evaluate_feed(json.dumps({"message": "API rate limit exceeded"}), "0.8.0")
    assert not result.available and result.error == "no public release"


def test_an_empty_feed_is_no_public_release() -> None:
    assert evaluate_feed("[]", "0.8.0").error == "no public release"


def test_a_body_that_is_not_json_at_all_raises_for_the_caller_to_catch() -> None:
    """`evaluate_feed` is pure and says so by raising; `check_for_update` never does."""
    with pytest.raises(ValueError):
        evaluate_feed("<html>rate limited</html>", "0.8.0")


def test_malformed_assets_are_dropped_not_fatal() -> None:
    feed = json.dumps(
        [
            {
                **release("v0.9.0-Public"),
                "assets": [
                    {"name": 3},
                    "junk",
                    {"name": "a", "browser_download_url": "u", "size": "big"},
                ],
            }
        ]
    )
    assert evaluate_feed(feed, "0.8.0").assets == ()


def test_the_fallback_page_is_one_that_exists() -> None:
    """`/releases/latest` 404s here: every release this project cuts is a prerelease."""
    assert RELEASES_PAGE.endswith("/releases")


def test_the_feed_asks_for_as_many_releases_as_the_api_will_give() -> None:
    """20 meant a week of test tags could push the newest public release off the page."""
    assert "per_page=100" in RELEASES_API


def test_both_urls_name_the_same_repository() -> None:
    """A gate that repoints the feed must not leave the opener trusting the other repo."""
    assert RELEASES_REPO in RELEASES_API and RELEASES_REPO in RELEASES_PAGE


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/DadsMmoLab/dads-mmo-lab/releases/tag/v0.8.70-Public", True),
        ("https://github.com/DadsMmoLab/dads-mmo-lab", True),
        ("https://github.com/DadsMmoLab/dads-mmo-lab/releases", True),
        ("http://github.com/DadsMmoLab/dads-mmo-lab/releases", False),
        ("https://github.com.evil.example/DadsMmoLab/dads-mmo-lab/releases", False),
        ("https://github.com/DadsMmoLabX/dads-mmo-lab/releases", False),
        ("https://github.com/DadsMmoLab/dads-mmo-lab-evil/releases", False),
        ("file:///etc/passwd", False),
        ("//host/share/x.exe", False),
        ("", False),
    ],
)
def test_only_this_repositorys_own_pages_are_ever_opened(url: str, allowed: bool) -> None:
    """`html_url` is a string the feed chose; `QDesktopServices` starts what it says."""
    assert safe_release_url(url) == (url if allowed else RELEASES_PAGE)


def test_the_allowed_prefix_follows_the_configured_repository() -> None:
    """A fork gate points the feed at pjerra; the opener has to move with it."""
    fork = "https://github.com/pjerra/dads-mmo-lab/releases"

    assert safe_release_url(f"{fork}/tag/v0.0.1-Public", page=fork).startswith(fork)
    assert safe_release_url(RELEASES_PAGE, page=fork) == fork


def test_a_feed_of_nothing_but_prereleases_still_yields_an_update() -> None:
    """The endpoint that lists them all, not `/releases/latest`, which 404s for everyone."""
    result = check_for_update("0.8.66-Public", http_get=fake_github)

    assert result.available is True
    assert result.latest == "v0.8.70-Public"
    assert result.url.endswith("/releases/tag/v0.8.70-Public")
    assert result.error is None


def test_check_reports_available_only_for_a_newer_release() -> None:
    payload = json.dumps([release("v9.9.9-Public")])
    result = check_for_update("0.1.4", http_get=lambda url: payload)
    assert result.available is True and result.latest == "v9.9.9-Public"
    assert result.url.endswith("/releases/tag/v9.9.9-Public") and result.error is None

    same = check_for_update("9.9.9", http_get=lambda url: payload)
    assert same.available is False and same.latest == "v9.9.9-Public"


def test_check_degrades_cleanly_offline_or_on_odd_payloads() -> None:
    def offline(url: str) -> str:
        raise OSError("no network")

    off = check_for_update("0.1.4", http_get=offline)
    assert off.available is False and off.latest is None and off.url == RELEASES_PAGE
    assert off.error is not None and "no network" in off.error

    broken = check_for_update("0.1.4", http_get=lambda url: "<html>rate limited</html>")
    assert broken.available is False and broken.error is not None

    # A rate-limited answer is valid JSON, just not a feed: `{"message": "API rate
    # limit exceeded", ...}`. Nothing published in it, and nothing to crash on.
    throttled = check_for_update("0.1.4", http_get=lambda url: json.dumps({"message": "slow down"}))
    assert throttled.available is False and throttled.latest is None
    assert throttled.error is not None and throttled.url == RELEASES_PAGE

    empty = check_for_update("0.1.4", http_get=lambda url: "[]")
    assert empty.available is False and empty.latest is None and empty.error is not None


def test_the_build_script_agrees_on_what_public_means() -> None:
    """One definition of `-Public`, or the release notes describe a different release."""
    script = Path(__file__).resolve().parents[1] / "build" / "release_notes.py"
    if not script.exists():
        pytest.skip("plan 1 not merged into this tree")
    assert 'r"^v(\\d+)\\.(\\d+)\\.(\\d+)-public$", re.IGNORECASE' in script.read_text(
        encoding="utf-8"
    )
