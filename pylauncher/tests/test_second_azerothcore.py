"""T552: a second AzerothCore server is a server of its own, beside WotLK.

Docker container names are global to a daemon, so two installs that both
name `ac-database` cannot exist at once, even with one of them stopped. The
three WotLK templates spelled every `ac-*` name as a literal; they now take the
entry's prefix, and WotLK's own render stays the committed bytes
(`test_composegen.py::test_the_wotlk_render_reproduces_the_committed_snapshot_byte_for_byte`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tests.support_second_ac import (
    SECOND_CONTAINERS,
    SECOND_PORTS,
    SECOND_SOAP,
    second_ac_entry,
    second_ac_json,
    wotlk_json,
)
from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import Catalog, load_catalog, parse_catalog

TEMPLATES = resources.installers_dir()
WOTLK_NAMES = ("ac-database", "ac-authserver", "ac-worldserver", "ac-db-import", "ac-client-data")


def render(tmp_path: Path) -> composegen.ComposePlan:
    return composegen.render(
        second_ac_entry(),
        tmp_path / "yulon-second-ac",
        templates_root=TEMPLATES,
        platform_id=lambda: "linux",
    )


def code_lines(text: str) -> str:
    """The file without its comment lines: prose about WotLK's history may name `ac-*`."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_a_second_entry_renders_its_own_services_and_container_names(tmp_path: Path) -> None:
    plan = render(tmp_path)
    base = yaml.safe_load(plan.base)
    expected = {
        SECOND_CONTAINERS["db"],
        SECOND_CONTAINERS["auth"],
        SECOND_CONTAINERS["world"],
        SECOND_CONTAINERS["db_import"],
        SECOND_CONTAINERS["client_data"],
    }
    assert set(base["services"]) == expected
    for key, service in base["services"].items():
        assert service["container_name"] == key
        for needed in service.get("depends_on", {}):
            assert needed in expected, (key, needed)
    assert set(yaml.safe_load(plan.override)["services"]) == {SECOND_CONTAINERS["world"]}
    assert set(yaml.safe_load(plan.build)["services"]) == expected - {SECOND_CONTAINERS["db"]}


def test_no_wotlk_name_is_left_outside_a_comment(tmp_path: Path) -> None:
    plan = render(tmp_path)
    for name, text in (("base", plan.base), ("override", plan.override), ("build", plan.build)):
        for wotlk in WOTLK_NAMES:
            assert wotlk not in code_lines(text), (name, wotlk)


def test_every_server_reaches_its_own_database(tmp_path: Path) -> None:
    base = yaml.safe_load(render(tmp_path).base)
    db = SECOND_CONTAINERS["db"]
    infos = [
        value
        for service in base["services"].values()
        for key, value in (service.get("environment") or {}).items()
        if key.endswith("_DATABASE_INFO")
    ]
    assert len(infos) == 9
    assert all(info.startswith(f"{db};3306;") for info in infos), infos
    assert f"-h {db} " in base["services"][db]["healthcheck"]["test"]


def test_a_second_entry_publishes_its_own_host_ports(tmp_path: Path) -> None:
    base = yaml.safe_load(render(tmp_path).base)
    services = base["services"]
    assert services[SECOND_CONTAINERS["auth"]]["ports"] == [
        f"${{DOCKER_AUTH_EXTERNAL_PORT:-{SECOND_PORTS['auth']}}}:3724"
    ]
    assert services[SECOND_CONTAINERS["world"]]["ports"] == [
        f"${{DOCKER_WORLD_EXTERNAL_PORT:-{SECOND_PORTS['world']}}}:8085",
        f"${{DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:{SECOND_SOAP}}}:7878",
    ]
    assert services[SECOND_CONTAINERS["db"]]["ports"] == [
        f"127.0.0.1:${{DOCKER_DB_EXTERNAL_PORT:-{SECOND_PORTS['db']}}}:3306"
    ]


def test_the_two_projects_share_no_name_docker_holds_globally(tmp_path: Path) -> None:
    """Containers are global; networks and volumes are keyed by the project name."""
    wotlk = composegen.render(
        load_catalog().get("wow-wotlk"),
        tmp_path / "yulon-wotlk",
        templates_root=TEMPLATES,
        platform_id=lambda: "linux",
    )
    second = render(tmp_path)

    def globals_of(text: str) -> set[str]:
        doc = yaml.safe_load(text)
        return {s["container_name"] for s in doc["services"].values()} | {doc["name"]}

    assert not globals_of(wotlk.base) & globals_of(second.base)


# -- what the catalog may say -------------------------------------------------


@pytest.mark.parametrize("field", ["db_import", "client_data"])
def test_an_azerothcore_one_shot_off_the_prefix_is_refused(field: str) -> None:
    """The template derives all five names from one prefix; the entry may not say otherwise.

    The engine selects `db_import` and `client_data` by name, so a name the
    rendered file does not define is `no such service` after a multi-hour build.
    """
    containers = dict(SECOND_CONTAINERS)
    containers[field] = "ac-" + containers[field].removeprefix("ub-")
    with pytest.raises(ValidationError, match=field):
        second_ac_entry(containers=containers)


def test_an_azerothcore_server_container_off_the_template_names_is_refused() -> None:
    containers = dict(SECOND_CONTAINERS, world="ub-world")
    with pytest.raises(ValidationError, match="ub-worldserver"):
        second_ac_entry(containers=containers)


def test_two_entries_naming_one_container_are_refused() -> None:
    """A container name is global to the daemon, so the catalog may hand it out once."""
    clash = second_ac_json(
        containers={
            "db": "ac-database",
            "auth": "ac-authserver",
            "world": "ac-worldserver",
            "db_import": "ac-db-import",
            "client_data": "ac-client-data-init",
        }
    )
    with pytest.raises(ValidationError, match="ac-database"):
        parse_catalog({"games": [wotlk_json(), clash]})


def test_two_azerothcore_entries_with_their_own_names_load_together() -> None:
    both = parse_catalog({"games": [wotlk_json(), second_ac_json()]})
    assert isinstance(both, Catalog)
    assert [g.id for g in both.games] == ["wow-wotlk", "wow-second-ac"]


def test_the_shipped_catalog_names_every_container_once() -> None:
    seen: dict[str, str] = {}
    for entry in load_catalog().games:
        c = entry.containers
        for name in (c.db, c.auth, c.world, c.db_import, c.client_data):
            if name is None:
                continue
            assert name not in seen, (name, seen.get(name), entry.id)
            seen[name] = entry.id


def test_wotlk_templates_spell_no_container_name_outside_a_comment() -> None:
    """The guard on the templates themselves: a literal re-added would pass WotLK's snapshot."""
    root = TEMPLATES / "wow-wotlk" / "native"
    for name in ("base.yml.tmpl", "override.yml.tmpl", "build.yml.tmpl"):
        text = code_lines((root / name).read_text(encoding="utf-8"))
        hits = [w for w in WOTLK_NAMES if re.search(re.escape(w), text)]
        assert not hits, (name, hits)


def test_a_soap_port_the_channel_does_not_dial_is_refused() -> None:
    """The base file publishes `install.native.soap_port`; the channel dials `operations.port`."""
    raw = second_ac_json()
    raw["operations"]["port"] = 7878
    with pytest.raises(ValidationError, match="soap_port"):
        parse_catalog({"games": [raw]})
