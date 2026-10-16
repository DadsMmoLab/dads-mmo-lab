"""Tests for the catalog's second kind of entry: single-container servers (`SingleEntry`)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import (
    CATALOG_FILE,
    SingleEntry,
    load_catalog,
    parse_catalog,
    port_token,
)

RAW: dict[str, Any] = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def _ddon_raw() -> dict[str, Any]:
    entry = next(e for e in RAW["single"] if e["id"] == "ddon")
    return copy.deepcopy(entry)


def _catalog_with(entry: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(RAW)
    data["single"] = [entry]
    return data


def test_the_shipped_catalog_has_ddon_and_dfo_as_its_single_servers() -> None:
    """DDON (one container) and DFO (host programs) are the second kind, not `games` entries."""
    catalog = load_catalog()
    assert tuple(e.id for e in catalog.single) == ("ddon", "dfo")
    assert not {"ddon", "dfo"} & {g.id for g in catalog.games}
    assert catalog.is_single("ddon") and catalog.is_single("dfo")
    assert not catalog.is_single("wow-wotlk")
    with pytest.raises(KeyError):
        catalog.get("ddon")
    with pytest.raises(KeyError):
        catalog.get_single("wow-wotlk")


def test_ddon_publishes_the_three_ports_arrowgene_listens_on() -> None:
    """Login 52100, game 52000 and web 52099: the server's own defaults, measured 2026-10-12."""
    ddon = load_catalog().get_single("ddon")
    assert ddon.port("login") == 52100
    assert ddon.port("game") == 52000
    assert ddon.port("web") == 52099
    assert ddon.host_ports() == (52100, 52000, 52099)
    assert ddon.port_tokens() == {
        "PORT_LOGIN": "52100",
        "PORT_GAME": "52000",
        "PORT_WEB": "52099",
    }
    with pytest.raises(KeyError):
        ddon.port("auth")


def test_ddon_is_pinned_to_a_full_commit() -> None:
    """A pinned server must rebuild the same bytes, so the source names a full SHA."""
    (source,) = load_catalog().get_single("ddon").emulator.sources
    assert source.rev is not None and len(source.rev) == 40
    assert source.dest == "src"


def test_port_token_spells_the_role_in_capitals() -> None:
    assert port_token("login") == "PORT_LOGIN"
    assert port_token("rpc-web") == "PORT_RPC_WEB"


@pytest.mark.parametrize("field", ["role", "port"])
def test_a_port_or_role_named_twice_is_refused(field: str) -> None:
    entry = _ddon_raw()
    entry["ports"][1][field] = entry["ports"][0][field]
    with pytest.raises(ValidationError, match="twice"):
        SingleEntry.model_validate(entry)


def test_a_checkout_that_is_not_a_source_is_refused() -> None:
    """The Dockerfile COPYs the checkout, so it must be a folder a source is cloned into."""
    entry = _ddon_raw()
    entry["install"]["native"]["checkout"] = "elsewhere"
    with pytest.raises(ValidationError, match="not a dest"):
        SingleEntry.model_validate(entry)


@pytest.mark.parametrize(
    ("where", "text"),
    [
        ("ready", "Listening on 0.0.0.0:{{PORT_AUTH}}."),
        ("play", "addr={{REALM}}"),
    ],
)
def test_a_token_the_entry_cannot_fill_is_refused(where: str, text: str) -> None:
    """Otherwise a ready wait that never ends, or a client started with a literal `{{...}}`."""
    entry = _ddon_raw()
    if where == "ready":
        entry["install"]["native"]["ready"] = [text]
    else:
        entry["play"]["args"] = [text]
    with pytest.raises(ValidationError, match="does not fill"):
        SingleEntry.model_validate(entry)


def test_host_and_account_are_play_tokens_but_not_ready_tokens() -> None:
    """A ready line is read off the server's log, where no player's account appears."""
    entry = _ddon_raw()
    entry["install"]["native"]["ready"] = ["{{ACCOUNT}} logged in"]
    with pytest.raises(ValidationError, match="does not fill"):
        SingleEntry.model_validate(entry)


@pytest.mark.parametrize(
    ("block", "key", "value"),
    [
        ("advertise", "dest", "../GameServerList.csv"),
        ("advertise", "source", "C:/Windows/x.csv"),
        ("play", "exe", "/usr/bin/ddo"),
    ],
)
def test_a_path_that_could_leave_its_folder_is_refused(block: str, key: str, value: str) -> None:
    entry = _ddon_raw()
    entry[block][key] = value
    with pytest.raises(ValidationError, match="stays inside"):
        SingleEntry.model_validate(entry)


def test_an_id_used_by_both_kinds_is_refused() -> None:
    """`state.json` keys an install by game id alone, so the id must say which kind it is."""
    entry = _ddon_raw()
    entry["id"] = "wow-wotlk"
    entry["container"] = "somewhere-else"
    with pytest.raises(ValidationError, match="twice"):
        parse_catalog(_catalog_with(entry))


def test_a_container_name_a_wow_entry_uses_is_refused() -> None:
    entry = _ddon_raw()
    entry["container"] = "ac-worldserver"
    with pytest.raises(ValidationError, match="ac-worldserver"):
        parse_catalog(_catalog_with(entry))


def test_a_port_a_wow_entry_publishes_is_refused() -> None:
    """A single-container server has no "stop the other one first" pairing to excuse it."""
    entry = _ddon_raw()
    entry["ports"][0]["port"] = 3724
    with pytest.raises(ValidationError, match="3724"):
        parse_catalog(_catalog_with(entry))


def test_unknown_keys_are_refused_like_everywhere_else_in_the_catalog() -> None:
    entry = _ddon_raw()
    entry["realmlist"] = {"table": "realmlist"}
    with pytest.raises(ValidationError):
        SingleEntry.model_validate(entry)


def test_a_catalog_without_single_servers_still_loads() -> None:
    """The key is optional: an older catalog file, or one with only WoW cores, is valid."""
    data = copy.deepcopy(RAW)
    del data["single"]
    assert parse_catalog(data).single == ()


# -- the second runtime: host programs (DFO) ------------------------------------------


def _dfo_raw() -> dict[str, Any]:
    entry = next(e for e in RAW["single"] if e["id"] == "dfo")
    return copy.deepcopy(entry)


def test_dfo_is_built_and_run_as_host_programs_on_windows_and_linux() -> None:
    dfo = load_catalog().get_single("dfo")
    assert dfo.install.native is None and dfo.install.process is not None
    assert dfo.install.runtime is dfo.install.process
    assert dfo.install.platforms == ("linux", "windows")
    assert dfo.container is None and dfo.service is None
    assert dfo.advertise is None, "the server answers on loopback; there is no address to give"
    assert dfo.port_tokens() == {"PORT_GATEWAY": "7001"}


def test_dfo_is_pinned_and_its_archive_is_of_that_pin() -> None:
    dfo = load_catalog().get_single("dfo")
    (source,) = dfo.emulator.sources
    process = dfo.install.process
    assert process is not None
    assert source.rev == "db707d59e303b49426c62d9e35d14181e7823631"
    assert source.rev in process.archive.url and process.archive.root.endswith(source.rev)
    assert [p.file.rsplit("/", 1)[-1][:4] for p in process.patches] == [
        "0001",
        "0002",
        "0003",
        "0004",
    ]
    # 0004 lets the launcher build off Windows (the Deck's), and is applied on Linux only.
    assert [p.platforms for p in process.patches] == [(), (), (), ("linux",)]
    linux_go = process.toolchain_for("linux")
    assert linux_go.url.endswith("go1.26.5.linux-amd64.tar.gz") and linux_go.dest == (
        process.toolchain.dest
    )
    assert process.toolchain_for("windows") is process.toolchain


def test_the_dfo_patches_and_templates_ship() -> None:
    from yulon import resources

    process = load_catalog().get_single("dfo").install.process
    assert process is not None
    root = resources.installers_dir()
    for patch in process.patches:
        assert (root / patch.file).is_file(), patch.file
    for spec in process.files:
        assert (root / process.templates / spec.template).is_file(), spec.template


@pytest.mark.parametrize("both", [True, False])
def test_an_install_names_exactly_one_runtime(both: bool) -> None:
    entry = _dfo_raw()
    if both:
        entry["install"]["native"] = _ddon_raw()["install"]["native"]
    else:
        del entry["install"]["process"]
    with pytest.raises(ValidationError, match="exactly one"):
        SingleEntry.model_validate(entry)


def test_a_process_install_names_no_container() -> None:
    entry = _dfo_raw()
    entry["container"] = "dfo-server"
    with pytest.raises(ValidationError, match="runs no container"):
        SingleEntry.model_validate(entry)


def test_a_container_install_names_its_container() -> None:
    entry = _ddon_raw()
    del entry["container"]
    with pytest.raises(ValidationError, match="names its container"):
        SingleEntry.model_validate(entry)


def test_an_archive_of_another_commit_than_the_pin_is_refused() -> None:
    entry = _dfo_raw()
    archive = entry["install"]["process"]["archive"]
    archive["url"] = "https://gitgud.io/fuckworld/115/-/archive/main/115-main.zip"
    with pytest.raises(ValidationError, match="pinned rev"):
        SingleEntry.model_validate(entry)


def test_a_serve_argument_spelling_a_token_nothing_fills_is_refused() -> None:
    entry = _dfo_raw()
    entry["install"]["process"]["serve"]["argv"].append("{{REALM}}")
    with pytest.raises(ValidationError, match="does not fill"):
        SingleEntry.model_validate(entry)


def test_a_patch_applied_outside_the_checkout_is_refused() -> None:
    entry = _dfo_raw()
    entry["install"]["process"]["patches"][0]["source"] = "elsewhere"
    with pytest.raises(ValidationError, match="not the checkout"):
        SingleEntry.model_validate(entry)


def test_dfo_shares_no_port_with_ddon() -> None:
    """7001 is DFO's own; the catalog's port check covers both runtimes."""
    entry = _dfo_raw()
    entry["ports"][0]["port"] = 52100
    data = copy.deepcopy(RAW)
    data["single"] = [_ddon_raw(), entry]
    with pytest.raises(ValidationError, match="52100"):
        parse_catalog(data)


def test_gitgud_is_a_forge_the_catalog_may_name_and_a_manifest_may_not() -> None:
    from yulon.catalog.catalog import EmulatorSource
    from yulon.manifest import Source

    url = "https://gitgud.io/fuckworld/115"
    assert EmulatorSource(repo=url, dest="src").repo == url
    with pytest.raises(ValidationError, match="allowed host"):
        Source(repo=url)
    with pytest.raises(ValidationError, match="allowed host"):
        EmulatorSource(repo="https://evil.example/x/y", dest="src")
