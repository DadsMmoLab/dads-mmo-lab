"""Tests for the catalog's third runtime (`SingleStack`) and the Play blocks a loader needs.

What the schema refuses is what the stack engine and Play would otherwise meet at the
worst moment: an image named by tag, a service running an image nobody listed, a
one-shot asked to print ready lines, a loader argument nothing fills.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import CATALOG_FILE, SingleEntry, load_catalog, parse_catalog

RAW: dict[str, Any] = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def _ffxi_raw() -> dict[str, Any]:
    return copy.deepcopy(next(e for e in RAW["single"] if e["id"] == "ffxi"))


def test_ffxi_names_its_seven_containers_for_the_catalogs_name_checks() -> None:
    ffxi = load_catalog().get_single("ffxi")
    assert ffxi.containers() == (
        "ffxi-database",
        "ffxi-database-update",
        "ffxi-connect",
        "ffxi-search",
        "ffxi-profile",
        "ffxi-world",
        "ffxi-map",
    )
    assert ffxi.container is None and ffxi.service is None


def test_ffxi_publishes_the_ports_landsandboat_listens_on() -> None:
    ffxi = load_catalog().get_single("ffxi")
    assert ffxi.host_ports() == (54001, 54230, 54231, 54002, 51220, 51240)


def test_the_server_image_is_the_pinned_commit_the_source_names() -> None:
    ffxi = load_catalog().get_single("ffxi")
    (source,) = ffxi.emulator.sources
    assert source.rev == "1858bbf6cfc6fce29e9e766e3121dba8830a1320"
    assert ffxi.install.stack is not None and ffxi.install.stack.source == source.dest


def test_an_image_named_by_tag_is_refused() -> None:
    entry = _ffxi_raw()
    entry["install"]["stack"]["images"][0]["ref"] = "ghcr.io/landsandboat/server:latest"
    with pytest.raises(ValidationError, match="pinned by digest"):
        SingleEntry.model_validate(entry)


def test_a_service_running_an_unlisted_image_is_refused() -> None:
    entry = _ffxi_raw()
    entry["install"]["stack"]["services"][2]["image"] = "nightly"
    with pytest.raises(ValidationError, match="not listed"):
        SingleEntry.model_validate(entry)


def test_a_one_shot_with_ready_lines_is_refused() -> None:
    entry = _ffxi_raw()
    entry["install"]["stack"]["services"][1]["ready"] = ["done"]
    with pytest.raises(ValidationError, match="runs once and exits"):
        SingleEntry.model_validate(entry)


def test_one_container_named_twice_is_refused() -> None:
    entry = _ffxi_raw()
    entry["install"]["stack"]["services"][3]["container"] = "ffxi-connect"
    with pytest.raises(ValidationError, match="one container twice"):
        SingleEntry.model_validate(entry)


def test_a_stack_with_no_ready_line_at_all_is_refused() -> None:
    entry = _ffxi_raw()
    for service in entry["install"]["stack"]["services"]:
        service["ready"] = []
    with pytest.raises(ValidationError, match="at least one ready line"):
        SingleEntry.model_validate(entry)


def test_a_stack_entry_naming_a_top_level_container_is_refused() -> None:
    entry = _ffxi_raw()
    entry["container"] = "ffxi-server"
    entry["service"] = "server"
    with pytest.raises(ValidationError, match="stack.services"):
        SingleEntry.model_validate(entry)


def test_two_runtimes_in_one_install_are_refused() -> None:
    entry = _ffxi_raw()
    ddon = next(e for e in RAW["single"] if e["id"] == "ddon")
    entry["install"]["native"] = copy.deepcopy(ddon["install"]["native"])
    with pytest.raises(ValidationError, match="exactly one of"):
        SingleEntry.model_validate(entry)


def test_a_secret_that_is_not_a_variable_name_is_refused() -> None:
    entry = _ffxi_raw()
    entry["install"]["stack"]["secrets"] = ["db password"]
    with pytest.raises(ValidationError, match="names a variable"):
        SingleEntry.model_validate(entry)


def test_a_loader_argument_spelling_an_unknown_token_is_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["loader"]["args"] = ["--user", "{{ACCOUNT}}"]
    with pytest.raises(ValidationError, match="does not fill"):
        SingleEntry.model_validate(entry)


def test_a_loader_language_needs_the_registry_that_says_it() -> None:
    entry = _ffxi_raw()
    del entry["play"]["registry"]
    with pytest.raises(ValidationError, match="only `play.registry` can fill"):
        SingleEntry.model_validate(entry)


def test_a_loader_takes_its_own_arguments_never_the_clients() -> None:
    entry = _ffxi_raw()
    entry["play"]["args"] = ["--server {{HOST}}"]
    with pytest.raises(ValidationError, match="takes its own play.loader.args"):
        SingleEntry.model_validate(entry)


def test_a_stack_container_another_entry_names_is_refused_by_the_catalog() -> None:
    data = copy.deepcopy(RAW)
    ffxi = next(e for e in data["single"] if e["id"] == "ffxi")
    ffxi["install"]["stack"]["services"][0]["container"] = "ddon-server"
    with pytest.raises(ValidationError, match="ddon-server"):
        parse_catalog(data)


def test_a_writable_folder_outside_the_client_is_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["writable"] = ["../PlayOnlineViewer"]
    with pytest.raises(ValidationError):
        SingleEntry.model_validate(entry)


def test_ffxi_signs_in_through_a_login_file_never_the_command_line() -> None:
    ffxi = load_catalog().get_single("ffxi")
    assert ffxi.play is not None and ffxi.play.loader is not None
    login = ffxi.play.loader.login
    assert login is not None and login.port == "auth" and ffxi.port(login.port) == 54231
    assert login.loader_version == (2, 2, 0)
    assert login.file == {"username": "{{ACCOUNT}}", "password": "{{PASSWORD}}", "hide": True}
    assert login.args == ("--json", "{{LOGIN_FILE}}")
    assert not any("PASSWORD" in arg for arg in ffxi.play.loader.args)


def test_a_login_file_without_the_password_is_refused() -> None:
    entry = _ffxi_raw()
    del entry["play"]["loader"]["login"]["file"]["password"]
    with pytest.raises(ValidationError, match="sign in as nobody"):
        SingleEntry.model_validate(entry)


def test_a_login_file_spelling_another_token_is_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["loader"]["login"]["file"]["server"] = "{{HOST}}"
    with pytest.raises(ValidationError, match="fills only"):
        SingleEntry.model_validate(entry)


def test_login_arguments_that_never_name_the_file_are_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["loader"]["login"]["args"] = ["--user", "{{ACCOUNT}}"]
    with pytest.raises(ValidationError, match="must spell"):
        SingleEntry.model_validate(entry)


def test_a_password_on_the_loaders_command_line_is_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["loader"]["args"] += ["--pass", "{{PASSWORD}}"]
    with pytest.raises(ValidationError, match="does not fill"):
        SingleEntry.model_validate(entry)


def test_a_login_on_a_port_the_entry_does_not_have_is_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["loader"]["login"]["port"] = "login"
    with pytest.raises(ValidationError, match="not one of this entry's port roles"):
        SingleEntry.model_validate(entry)


def test_a_name_range_that_is_upside_down_is_refused() -> None:
    entry = _ffxi_raw()
    entry["play"]["loader"]["login"]["name_min"] = 16
    with pytest.raises(ValidationError, match="name_min"):
        SingleEntry.model_validate(entry)
