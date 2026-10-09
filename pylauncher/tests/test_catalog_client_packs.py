"""The catalog's ready-to-play client section: packs, a Wow.exe patch, Config.wtf (T181 b/c).

Every rule is shown twice: once on a fixture that is valid in every other respect
(`_client()` loads, which is asserted on its own so a refusal below can never be
the base fixture's fault), and once on a copy that breaks that rule and nothing
else. Each refusal is matched on a fragment of its own message, so a fixture that
trips a DIFFERENT rule than the one it is about fails rather than passes.

Nothing here touches a network or a real Wow.exe: hosts are `example.org` names
and the sha256 is a stand-in, because these are rules about the catalog's shape.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import (
    CATALOG_FILE,
    Client,
    ClientPack,
    ConfigWtf,
    ExePatch,
    ExeWrite,
    load_catalog,
)

STOCK_SIZE = 7_704_216


def _client() -> dict[str, Any]:
    """A client section that uses every new field once and is valid."""
    return {
        "version": "3.3.5a",
        "build": 12340,
        "packs": [
            {
                "id": "patch-y",
                "label": "World patch",
                "source": {"kind": "checkout", "path": "centurion/patches/patch-Y.zip"},
                "md5": "0" * 32,
                "install": [{"member": "patch-Y.MPQ", "to": "Data/patch-X.MPQ"}],
            },
            {
                "id": "addons",
                "label": "Addons",
                "source": {"kind": "checkout", "path": "centurion/patches/addons.zip"},
                "sha256": "1" * 64,
                "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
            },
            {
                "id": "client-tweaks",
                "label": "Client tweaks",
                "source": {"kind": "checkout", "path": "centurion/patches/client-tweaks.zip"},
                # Its md5 is read from the server's checkout at the commit it is on
                # (T179 Task 7, the lead's ruling), not pinned here.
                "md5_file": "centurion/patches/patches.md5",
                "install": [{"member": "*", "to_dir": "."}],
            },
            {
                "id": "hd-creatures",
                "label": "HD creatures",
                "description": "Higher-resolution creature models.",
                "source": {
                    "kind": "url",
                    "url": "https://Packs.Example.org/downloads/hd-creatures.zip",
                    "version_url": "https://packs.example.org/downloads/hd-creatures.version",
                },
                "install": [{"member": "patch-F.MPQ", "to": "Data/patch-F.MPQ"}],
                "remove_when_off": ["Data/patch-F.MPQ"],
                "optional": True,
                "default": False,
                "size_hint": 1_450_000_000,
            },
            {
                # A folder of the checkout, each file proved against the module's own
                # sha256sum list (T555 T1): mod-unbound ships its addons loose, no zip.
                "id": "unbound-addons",
                "label": "Unbound addons",
                "source": {
                    "kind": "checkout_folder",
                    "path": "modules/mod-unbound/client/Interface/AddOns",
                },
                "sha256_file": "modules/mod-unbound/MANIFEST.sha256",
                "install": [{"member": "*", "to_dir": "Interface/AddOns"}],
            },
        ],
        "exe_patch": {
            "expect_sha256": "a" * 64,
            "expect_size": STOCK_SIZE,
            "clean_sources": [
                {
                    "url": "https://clean.example.org/WoW-Client-3.3.5a.zip",
                    "member": "WoW-3.3.5a/Wow.exe",
                }
            ],
            "fallback_page": "https://fallback.example.org/downloads/",
            "writes": [
                {"offset": 0x5F3A00, "bytes": "313233343200"},
                {"offset": 0x2E1C67, "fill": 0x90, "count": 11},
                # The last byte of the file: a write that ENDS at expect_size is inside it.
                {"offset": STOCK_SIZE - 1, "bytes": "00"},
            ],
            "options": {
                "borderless": {
                    "label": "Borderless window",
                    "default": True,
                    "on": [{"offset": 0x0E94, "bytes": "eb"}],
                    "off": [{"offset": 0x0E94, "bytes": "74"}],
                }
            },
            "pe_large_address_aware": True,
            "build": 12342,
        },
        "config_wtf": {
            "always": {"realmList": "127.0.0.1", "realmName": "Centurion"},
            "seed": {"gxWindow": "1"},
            "remove_locale_realmlists": True,
        },
    }


def test_the_fixture_every_refusal_below_is_cut_from_loads() -> None:
    """Without this, every refusal test could pass because the base itself was bad."""
    client = Client.model_validate(_client())
    assert [pack.id for pack in client.packs] == [
        "patch-y",
        "addons",
        "client-tweaks",
        "hd-creatures",
        "unbound-addons",
    ]
    assert client.packs[3].source.version_url is not None
    folder = client.packs[4]
    assert folder.source.kind == "checkout_folder" and folder.source.from_checkout
    assert folder.sha256_file == "modules/mod-unbound/MANIFEST.sha256"
    assert [pack.source.from_checkout for pack in client.packs] == [True, True, True, False, True]
    assert client.exe_patch is not None
    assert client.exe_patch.options["borderless"].default is True
    assert client.exe_patch.writes[1].length == 11
    assert client.exe_patch.writes[1].payload() == b"\x90" * 11
    assert client.exe_patch.writes[0].payload() == b"12342\x00"
    assert client.config_wtf is not None
    assert client.config_wtf.always["realmName"] == "Centurion"
    assert client.packs[2].md5_file == "centurion/patches/patches.md5"
    assert client.packs[2].md5 is None and client.packs[2].sha256 is None


def test_hosts_names_every_host_a_download_may_reach_and_nothing_else() -> None:
    """Packs' url and version_url, and the clean-exe sources; never the fallback page.

    The fallback page is a page a PERSON is sent to in a refusal, so it is not a
    host Yu'lon fetches from and must not widen the allow-list later tasks check.
    Hostnames compare lowercased, as DNS does.
    """
    assert Client.model_validate(_client()).hosts() == frozenset(
        {"packs.example.org", "clean.example.org"}
    )


def test_a_client_without_the_new_fields_names_no_host() -> None:
    client = Client.model_validate({"version": "1", "build": 1})
    assert client.packs == ()
    assert client.exe_patch is None
    assert client.config_wtf is None
    assert client.hosts() == frozenset()


def test_the_shipped_catalog_uses_the_new_fields_on_centurion_and_unbound_alone() -> None:
    """Groundwork for T179: Centurion is the first entry with a ready-to-play client section,
    WoW Unbound (T555) the second; every other entry behaves exactly as before (its own tests:
    test_catalog_centurion, test_unbound_entry)."""
    raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    for entry in raw["games"]:
        if entry["id"] not in ("wow-centurion", "wow-unbound"):
            assert set(entry["client"]) <= {
                "version",
                "build",
                "required_build",
                "also_builds",
                "foreign_data_dies_after",
                "realmlist_file",
                "notes",
            }
    for game in load_catalog().games:
        if game.id == "wow-centurion":
            assert game.client.packs and game.client.exe_patch and game.client.config_wtf
            assert game.client.hosts() == frozenset({"centurionpvp.com", "wow.baerthe.com"})
            continue
        if game.id == "wow-unbound":
            assert game.client.packs and game.client.config_wtf
            assert game.client.exe_patch is None
            assert game.client.hosts() == frozenset()
            continue
        assert game.client.packs == ()
        assert game.client.exe_patch is None
        assert game.client.config_wtf is None
        assert game.client.hosts() == frozenset()


Mutation = Callable[[dict[str, Any]], None]


def _set(path: str, value: object) -> Mutation:
    """Set one dotted path (list indices as digits) in the fixture to `value`."""

    def apply(data: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        node: Any = data
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        if last.isdigit():
            node[int(last)] = value
        else:
            node[last] = value

    return apply


def _drop(path: str) -> Mutation:
    def apply(data: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        node: Any = data
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        del node[last]

    return apply


def _both(*mutations: Mutation) -> Mutation:
    """Several edits that together break ONE rule (e.g. swap `path` for `url`)."""

    def apply(data: dict[str, Any]) -> None:
        for mutation in mutations:
            mutation(data)

    return apply


def _append(path: str, value: object) -> Mutation:
    def apply(data: dict[str, Any]) -> None:
        node: Any = data
        for key in path.split("."):
            node = node[int(key)] if key.isdigit() else node[key]
        node.append(copy.deepcopy(value))

    return apply


REFUSALS: dict[str, tuple[Mutation, str]] = {
    # https only, on every URL the section names.
    "pack url over http": (
        _set("packs.3.source.url", "http://packs.example.org/hd-creatures.zip"),
        "must be an https URL",
    ),
    "version url over http": (
        _set("packs.3.source.version_url", "http://packs.example.org/hd.version"),
        "must be an https URL",
    ),
    "clean source over http": (
        _set("exe_patch.clean_sources.0.url", "http://clean.example.org/c.zip"),
        "must be an https URL",
    ),
    "fallback page over http": (
        _set("exe_patch.fallback_page", "http://fallback.example.org/"),
        "must be an https URL",
    ),
    "url with no host": (
        _set("packs.3.source.url", "https:///hd-creatures.zip"),
        "must be an https URL",
    ),
    "url carrying credentials": (
        _set("packs.3.source.url", "https://user:pw@packs.example.org/hd.zip"),
        "must not carry credentials",
    ),
    # Relative paths that stay inside the folder they are relative to.
    "install target climbs out": (
        _set("packs.0.install.0.to", "../Wow.exe"),
        "relative POSIX path",
    ),
    "install target absolute": (
        _set("packs.0.install.0.to", "/Data/patch-X.MPQ"),
        "relative POSIX path",
    ),
    "install target on a Windows drive": (
        _set("packs.0.install.0.to", "C:/Windows/patch-X.MPQ"),
        "relative POSIX path",
    ),
    "install target with a backslash": (
        _set("packs.0.install.0.to", "Data\\patch-X.MPQ"),
        "relative POSIX path",
    ),
    "install target names no file": (
        _set("packs.0.install.0.to", "."),
        "must name a file",
    ),
    "install dir climbs out": (
        _set("packs.1.install.0.to_dir", "Interface/../.."),
        "relative POSIX path",
    ),
    "checkout path climbs out": (
        _set("packs.0.source.path", "../elsewhere/patch-Y.zip"),
        "relative POSIX path",
    ),
    "remove_when_off absolute": (
        _set("packs.3.remove_when_off.0", "/Data/patch-F.MPQ"),
        "relative POSIX path",
    ),
    "member climbs out": (
        _set("packs.0.install.0.member", "../patch-Y.MPQ"),
        "relative POSIX path",
    ),
    # Install rule shape.
    "star member with a file target": (
        _set("packs.1.install.0", {"member": "*", "to": "Interface/AddOns"}),
        'member "*" installs into to_dir',
    ),
    "named member with a dir target": (
        _set("packs.0.install.0", {"member": "patch-Y.MPQ", "to_dir": "Data"}),
        "a named member installs to a file",
    ),
    "named member with both targets": (
        _set(
            "packs.0.install.0",
            {"member": "patch-Y.MPQ", "to": "Data/patch-X.MPQ", "to_dir": "Data"},
        ),
        "a named member installs to a file",
    ),
    "pack that installs nothing": (_set("packs.0.install", []), "at least 1 item"),
    # Pack source shape.
    "checkout source with a url": (
        _both(
            _drop("packs.0.source.path"),
            _set("packs.0.source.url", "https://packs.example.org/p.zip"),
        ),
        "a checkout source names a path",
    ),
    "url source with a path": (
        _both(_drop("packs.3.source.url"), _set("packs.3.source.path", "x/hd.zip")),
        "a url source names a url",
    ),
    "checkout source with a version url": (
        _set("packs.0.source.version_url", "https://packs.example.org/p.version"),
        "a checkout source names a path",
    ),
    # Pack checksums and choice.
    "checkout pack with no checksum": (
        _drop("packs.0.md5"),
        "a checkout pack needs a checksum",
    ),
    "url pack with no checksum and no version_url": (
        _drop("packs.3.source.version_url"),
        "a url pack needs a checksum or a version_url",
    ),
    "pack with two checksums": (
        _set("packs.0.sha256", "3" * 64),
        "takes exactly one of sha256, md5 and md5_file",
    ),
    "pack with a pinned md5 and an md5_file": (
        _set("packs.2.md5", "3" * 32),
        "takes exactly one of sha256, md5 and md5_file",
    ),
    "pack with a pinned sha256 and an md5_file": (
        _set("packs.1.md5_file", "centurion/patches/patches.md5"),
        "takes exactly one of sha256, md5 and md5_file",
    ),
    "url pack naming an md5_file": (
        _set("packs.3.md5_file", "centurion/patches/patches.md5"),
        "only a checkout pack takes md5_file",
    ),
    "md5_file leaving the server dir": (
        _set("packs.2.md5_file", "../patches.md5"),
        "must be a relative POSIX path",
    ),
    "md5_file absolute": (
        _set("packs.2.md5_file", "/srv/patches.md5"),
        "must be a relative POSIX path",
    ),
    "md5_file in another folder than the zip": (
        _set("packs.2.md5_file", "centurion/other/patches.md5"),
        "inside the folder of its md5_file",
    ),
    # A checkout folder: proved file by file against a sha256sum list, nothing else (T555 T1).
    "folder source with a url": (
        _set("packs.4.source.url", "https://packs.example.org/p.zip"),
        "a checkout_folder source names a path",
    ),
    "folder source with a version url": (
        _set("packs.4.source.version_url", "https://packs.example.org/p.version"),
        "a checkout_folder source names a path",
    ),
    "folder source with no path": (
        _drop("packs.4.source.path"),
        "a checkout_folder source names a path",
    ),
    "folder path climbs out": (
        _set("packs.4.source.path", "modules/../../etc"),
        "relative POSIX path",
    ),
    "folder pack with no sha256_file": (
        _drop("packs.4.sha256_file"),
        "a checkout_folder pack takes exactly sha256_file",
    ),
    "folder pack with a pinned sha256 too": (
        _set("packs.4.sha256", "3" * 64),
        "a checkout_folder pack takes exactly sha256_file",
    ),
    "folder pack with a pinned md5 instead": (
        _both(_drop("packs.4.sha256_file"), _set("packs.4.md5", "3" * 32)),
        "a checkout_folder pack takes exactly sha256_file",
    ),
    "folder pack with an md5_file instead": (
        _both(
            _drop("packs.4.sha256_file"),
            _set("packs.4.md5_file", "modules/mod-unbound/MANIFEST.md5"),
        ),
        "a checkout_folder pack takes exactly sha256_file",
    ),
    "folder outside the sha256_file's folder": (
        _set("packs.4.sha256_file", "modules/other/MANIFEST.sha256"),
        "inside the folder of its sha256_file",
    ),
    "sha256_file leaving the server dir": (
        _set("packs.4.sha256_file", "../MANIFEST.sha256"),
        "must be a relative POSIX path",
    ),
    "a zip pack naming a sha256_file": (
        _set("packs.2.sha256_file", "centurion/patches/patches.sha256"),
        "only a checkout_folder pack takes sha256_file",
    ),
    "a url pack naming a sha256_file": (
        _set("packs.3.sha256_file", "centurion/patches/patches.sha256"),
        "only a checkout_folder pack takes sha256_file",
    ),
    "sha256 not hex": (_set("packs.1.sha256", "z" * 64), "String should match pattern"),
    "required pack defaulting on": (
        _set("packs.0.default", True),
        "default only means something on an optional pack",
    ),
    "duplicate pack ids": (
        _set("packs.1.id", "patch-y"),
        "pack ids must be unique",
    ),
    "a pack removing another pack's file": (
        _set("packs.3.remove_when_off.0", "DATA/patch-x.mpq"),
        "removes",
    ),
    "two packs writing one file": (
        _set("packs.3.install.0.to", "data/PATCH-x.mpq"),
        "two packs install",
    ),
    # Exe writes.
    "write with bytes and fill": (
        _set("exe_patch.writes.0.fill", 0x90),
        "exactly one of bytes and fill",
    ),
    "write with neither bytes nor fill": (
        _drop("exe_patch.writes.0.bytes"),
        "exactly one of bytes and fill",
    ),
    "fill without a count": (
        _drop("exe_patch.writes.1.count"),
        "count goes with fill",
    ),
    "bytes with a count": (
        _set("exe_patch.writes.0.count", 6),
        "count goes with fill",
    ),
    "bytes not whole hex bytes": (
        _set("exe_patch.writes.0.bytes", "eb0"),
        "String should match pattern",
    ),
    "write running past the end": (
        _set("exe_patch.writes.2.bytes", "0000"),
        "past the end of the stock exe",
    ),
    "fill running past the end": (
        _set("exe_patch.writes.1.offset", STOCK_SIZE - 10),
        "past the end of the stock exe",
    ),
    "option-on write past the end": (
        _set("exe_patch.options.borderless.on.0.offset", STOCK_SIZE),
        "past the end of the stock exe",
    ),
    "option-off write past the end": (
        _set("exe_patch.options.borderless.off.0.offset", STOCK_SIZE),
        "past the end of the stock exe",
    ),
    "two fixed writes overlapping": (
        _set("exe_patch.writes.1.offset", 0x5F3A02),
        "write the same bytes",
    ),
    "option write overlapping a fixed write": (
        _set("exe_patch.options.borderless.on.0.offset", 0x2E1C67 + 3),
        "write the same bytes",
    ),
    "option-off write overlapping a fixed write": (
        _set("exe_patch.options.borderless.off.0.offset", 0x5F3A05),
        "write the same bytes",
    ),
    "no clean source": (_set("exe_patch.clean_sources", []), "at least 1 item"),
    "option name not a slug": (
        _both(
            _set(
                "exe_patch.options",
                {
                    "Border less": {
                        "label": "Borderless window",
                        "default": True,
                        "on": [{"offset": 0x0E94, "bytes": "eb"}],
                        "off": [],
                    }
                },
            )
        ),
        "String should match pattern",
    ),
    # Config.wtf.
    "a key both always and seeded": (
        _set("config_wtf.seed.realmList", "10.0.0.1"),
        "both always and seed",
    ),
    "a value that would close its quotes": (
        _set("config_wtf.always.realmName", 'Centurion"\nSET x "1'),
        "String should match pattern",
    ),
    "a key with a space": (
        _set("config_wtf.seed.gx Window", "1"),
        "String should match pattern",
    ),
}


@pytest.mark.parametrize("name", sorted(REFUSALS))
def test_each_rule_refuses_its_one_bad_input(name: str) -> None:
    mutate, fragment = REFUSALS[name]
    data = _client()
    mutate(data)
    with pytest.raises(ValidationError, match=re.escape(fragment)):
        Client.model_validate(data)


def test_the_mutations_left_the_fixture_alone() -> None:
    """`_client()` returns a fresh dict each call, so no refusal leaks into the next."""
    for mutate, _ in REFUSALS.values():
        mutate(_client())
    Client.model_validate(_client())


def test_a_duplicate_option_name_in_the_file_is_refused(tmp_path: Path) -> None:
    """JSON keeps the LAST of two equal keys silently; the loader must not.

    Two `borderless` options in the text would otherwise load as one, and the
    one that lost would be a write nobody could ever turn on. Shown through
    `load_catalog` because the rule lives in the reader, not in a model: by the
    time a model sees a dict, the duplicate is already gone.
    """
    raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    raw["games"][0]["client"] = _client()
    text = json.dumps(raw, indent=1)
    option = json.dumps(_client()["exe_patch"]["options"]["borderless"])
    doubled = text.replace('"borderless": ', f'"borderless": {option}, "borderless": ', 1)
    assert doubled != text
    good = tmp_path / "good.json"
    good.write_text(text, encoding="utf-8")
    assert load_catalog(good).games[0].client.exe_patch is not None
    bad = tmp_path / "bad.json"
    bad.write_text(doubled, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate key 'borderless'"):
        load_catalog(bad)


def test_write_and_model_types_are_importable_for_the_engine() -> None:
    """Later tasks build on these names; a rename must fail here first."""
    write = ExeWrite(offset=0, bytes="eb")
    assert write.length == 1 and write.payload() == b"\xeb"
    assert ConfigWtf().always == {} and ConfigWtf().seed == {}
    assert ExePatch.model_fields["options"].default_factory is dict
    assert ClientPack.model_fields["optional"].default is False
