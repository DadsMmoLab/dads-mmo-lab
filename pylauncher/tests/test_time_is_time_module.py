"""T879: the Time Is Time module is in the WotLK list.

The manifest is read from the shipped tree. Its conf keys are the ones in the module's own
`.conf.dist` (read at the commit named below, no network here).
"""

from __future__ import annotations

import json

from yulon import resources
from yulon.manifest import Manifest
from yulon.manifest_store import ManifestStore

GAME_DIR = resources.manifests_dir() / "wow-wotlk"

TIME_IS_TIME_DIST_KEYS = (
    "TimeIsTime.Announce",
    "TimeIsTime.Enable",
    "TimeIsTime.SpeedRate",
    "TimeIsTime.TimeStart",
    "TimeIsTime.HourOffset",
    "TimeIsTime.RealignDate",
    "TimeIsTime.RealignDateLookahead",
)
"""`conf/mod-time_is_time.conf.dist` of pjerra/mod-TimeIsTime, master (upstream 155aaf1 + NathanHandley cab7766)."""  # noqa: E501


def _module(item: str) -> Manifest:
    store = ManifestStore(resources.manifests_dir(), "wow-wotlk")
    return next(m for m in store.load_all("module") if m.id == item)


def _listed() -> list[str]:
    index = json.loads((GAME_DIR / "modules.json").read_text(encoding="utf-8"))
    return [str(item) for item in index["items"]]


def test_time_is_time_is_listed_and_its_conf_keys_are_the_dists() -> None:
    m = _module("mod-timeistime")
    assert "mod-timeistime" in _listed()
    assert m.folder == "mod-TimeIsTime"  # the core finds the module by this exact name
    assert m.build.rebuild
    (conf,) = m.conf
    assert conf.template == "conf/mod-time_is_time.conf.dist"
    assert tuple(key.key for key in conf.keys) == TIME_IS_TIME_DIST_KEYS


def test_time_is_time_comes_from_the_fork_with_the_calendar_fix() -> None:
    m = _module("mod-timeistime")
    assert m.source.repo == "pjerra/mod-TimeIsTime"
    (conf,) = m.conf
    by_key = {key.key: key for key in conf.keys}
    assert by_key["TimeIsTime.RealignDate"].type == "bool"
