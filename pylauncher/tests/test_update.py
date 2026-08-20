"""Tests for the self-update check (`yulon.update`, README §10): check + notify only."""

from __future__ import annotations

import json

import pytest

from yulon.update import RELEASES_PAGE, check_for_update, is_newer, parse_version


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


def test_check_reports_available_only_for_a_newer_release() -> None:
    payload = json.dumps({"tag_name": "v9.9.9", "html_url": "https://example.test/rel/v9.9.9"})
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

    odd = check_for_update("0.1.4", http_get=lambda url: json.dumps({"tag_name": "latest"}))
    assert odd.available is False and "unrecognized" in (odd.error or "")

    broken = check_for_update("0.1.4", http_get=lambda url: "<html>rate limited</html>")
    assert broken.available is False and broken.error is not None
