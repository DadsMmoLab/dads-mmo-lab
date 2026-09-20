"""A packaged build knows its version from the tag it was built from."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))

import stamp_version  # noqa: E402


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("v0.8.70-Public", "0.8.70-Public"),
        ("v0.8.68-fixtest", "0.8.68-fixtest"),
        ("v0.6.59", "0.6.59"),
        ("Yulon", None),
        ("release/0.8.66-fixtest", None),
        ("", None),
    ],
)
def test_only_a_version_tag_is_a_version(ref: str, expected: str | None) -> None:
    assert stamp_version.version_from_ref(ref) == expected


def test_a_tag_writes_the_stamp(tmp_path: Path) -> None:
    target = tmp_path / "_build_version.py"
    assert stamp_version.main(["v0.8.70-Public", "--out", str(target)]) == 0
    namespace: dict[str, object] = {}
    exec(target.read_text(encoding="utf-8"), namespace)
    assert namespace["VERSION"] == "0.8.70-Public"


def test_a_branch_build_writes_nothing_and_still_exits_zero(tmp_path: Path) -> None:
    target = tmp_path / "_build_version.py"
    assert stamp_version.main(["Yulon", "--out", str(target)]) == 0
    assert not target.exists()


def test_the_package_prefers_the_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    import yulon

    fake = types.ModuleType("yulon._build_version")
    fake.VERSION = "9.9.9-Public"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yulon._build_version", fake)
    try:
        assert importlib.reload(yulon).__version__ == "9.9.9-Public"
    finally:
        monkeypatch.delitem(sys.modules, "yulon._build_version")
        importlib.reload(yulon)


def test_a_checkout_has_no_stamp_and_uses_the_written_version() -> None:
    import yulon

    assert not (Path(yulon.__file__).parent / "_build_version.py").exists()
    assert yulon.__version__ == yulon._FALLBACK_VERSION
