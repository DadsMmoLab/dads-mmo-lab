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
    PackSetup,
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


def test_the_shipped_catalog_uses_the_new_fields_on_wotlk_centurion_and_unbound() -> None:
    """T809 W2: wow-wotlk and wow-unbound carry the Lumenveil ClientPack; Centurion does not.
    WoW Unbound (T555) keeps its required addons pack beside it; every other entry behaves
    exactly as before (its own tests: test_catalog_centurion, test_unbound_entry)."""
    raw = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    for entry in raw["games"]:
        if entry["id"] in ("wow-wotlk", "wow-centurion", "wow-unbound"):
            assert "packs" in entry["client"], entry["id"]
            continue
        assert set(entry["client"]) <= {
            "version",
            "build",
            "required_build",
            "also_builds",
            "foreign_data_dies_after",
            "addon_interface",
            "realmlist_file",
            "notes",
        }
    for game in load_catalog().games:
        if game.id == "wow-wotlk":
            assert [pack.id for pack in game.client.packs] == ["lumenveil"]
            assert game.client.exe_patch is None
            assert game.client.config_wtf is None
            assert game.client.hosts() == frozenset(
                {
                    "github.com",
                    "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com",
                }
            )
            continue
        if game.id == "wow-centurion":
            assert game.client.packs and game.client.exe_patch and game.client.config_wtf
            assert game.client.hosts() == frozenset({"centurionpvp.com", "wow.baerthe.com"})
            continue
        if game.id == "wow-unbound":
            assert [pack.id for pack in game.client.packs] == ["unbound-addons", "lumenveil"]
            assert game.client.config_wtf
            assert game.client.exe_patch is None
            assert game.client.hosts() == frozenset(
                {
                    "github.com",
                    "release-assets.githubusercontent.com",
                    "objects.githubusercontent.com",
                }
            )
            continue
        assert game.client.packs == ()
        assert game.client.exe_patch is None
        assert game.client.config_wtf is None
        assert game.client.hosts() == frozenset()


LUMENVEIL_URL = (
    "https://github.com/lumenveil/Lumenveil/releases/download/v0.1.0/Lumenveil-0.1.0.zip"
)
LUMENVEIL_SHA256 = "78d1e1ca7450858a67d6ec2298343c3f67cda9aa920aa800e28982d8d3fee83b"
LUMENVEIL_SIZE = 1_233_402
LUMENVEIL_RELEASE = "https://github.com/lumenveil/Lumenveil/releases"
LUMENVEIL_RELEASE_TAG = "v0.1.0"
LUMENVEIL_RELEASE_DATE = "2026-10-21"
"""T809 W2 pin (verified 2026-10-24): release page + tag + release date, and how the bytes proved.

Zip hash recomputed locally from the downloaded asset and compared with the
release's SHA256SUMS.txt line for Lumenveil-0.1.0.zip: match; all 16 zip members rechecked
against their SHA256SUMS.txt lines: all OK. Size is the asset's byte count.
`test_the_lumenveil_pin_names_its_release` holds the page, tag and date beside the hash,
so a silent re-point at another tag fails here rather than in the download."""

LUMENVEIL_MEMBERS = (
    "version.dll",
    "CoAVolFog.dll",
    "CoAVolFog.ini",
    "CoAVolFog-regions.ini",
    "Lumenveil-Setup.cmd",
    "Lumenveil-Uninstall.cmd",
    "Lumenveil/lamp_extract.exe",
    "Lumenveil/LEGAL.txt",
    "Lumenveil/LICENSE.txt",
    "Lumenveil/README.txt",
    "Lumenveil/THIRD-PARTY-NOTICES.txt",
    "Lumenveil/licenses/bzip2.txt",
    "Lumenveil/licenses/DearImGui-MIT.txt",
    "Lumenveil/licenses/Everwood-MIT.txt",
    "Lumenveil/licenses/GPL-2.0.txt",
    "Lumenveil/licenses/zlib.txt",
)
"""The zip's 16 members as the catalog installs them: root members verbatim, and the
`Lumenveil\\...` members with forward slashes, because the catalog's `_inside` refuses
backslashes. Spelled per member (no `"*"`) so an unexpected member is never laid in."""


def _lumenveil_of(game_id: str) -> ClientPack:
    """The shipped Lumenveil pack on one entry, failing loudly when it is missing."""
    packs = {pack.id: pack for pack in load_catalog().get(game_id).client.packs}
    assert "lumenveil" in packs, f"{game_id} lost its Lumenveil pack"
    return packs["lumenveil"]


def test_the_lumenveil_pack_is_pinned_identical_and_windows_only() -> None:
    """The pin both entries share: URL, hash, size, members, platform, hosts, setup, credit."""
    for game_id in ("wow-wotlk", "wow-unbound"):
        pack = _lumenveil_of(game_id)
        assert pack.label == "Lumenveil"
        assert (pack.source.kind, pack.source.url) == ("url", LUMENVEIL_URL)
        assert pack.source.version_url is None, "no latest to chase: an update is a catalog change"
        assert pack.sha256 == LUMENVEIL_SHA256
        assert (pack.md5, pack.md5_file, pack.sha256_file) == (None, None, None)
        assert pack.optional is True and pack.default is False, "opt-in extra, off until chosen"
        assert pack.size_hint == LUMENVEIL_SIZE
        assert pack.platforms == ("windows",), "the setup exe runs on the player's machine"
        assert pack.redirect_hosts == (
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        )
        assert [(rule.member, rule.to) for rule in pack.install] == [
            (member, member) for member in LUMENVEIL_MEMBERS
        ]
        assert pack.remove_when_off == ("CoAVolFog.log",), "spots stay: player data, plan D5"
        assert pack.setup is not None
        assert pack.setup.run == "Lumenveil/lamp_extract.exe"
        assert pack.setup.runs == (
            (
                "--data",
                "{data}",
                "--out",
                "{dir}/lampdata.bin",
                "--locale",
                "{locale}",
                "--background",
            ),
            (
                "--data",
                "{data}",
                "--liquids",
                "{dir}/liquiddata.bin",
                "--maps",
                "0,1,530,571",
                "--locale",
                "{locale}",
                "--background",
            ),
        )
        assert pack.setup.writes == ("lampdata.bin", "liquiddata.bin")
        assert pack.setup.timeout_seconds == 1800
        assert pack.credit.startswith("Lumenveil") and "GPL-2.0" in pack.credit
        assert "CoAVolFog" in pack.credit


def test_the_lumenveil_pin_names_its_release() -> None:
    """Item 7: the pinned hash is answerable -- the release page, tag and date it came from."""
    assert LUMENVEIL_URL == (
        f"{LUMENVEIL_RELEASE}/download/{LUMENVEIL_RELEASE_TAG}/Lumenveil-0.1.0.zip"
    )
    assert LUMENVEIL_RELEASE == "https://github.com/lumenveil/Lumenveil/releases"
    assert LUMENVEIL_RELEASE_TAG == "v0.1.0"
    assert LUMENVEIL_RELEASE_DATE == "2026-10-21"


def test_the_lumenveil_setup_runs_cover_what_the_shipped_cmd_runs() -> None:
    """The pinned Lumenveil-Setup.cmd (from inside the verified zip) runs lamp_extract.exe
    twice: once with `--out lampdata.bin` (lamps) and once with `--liquids liquiddata.bin
    --maps 0,1,530,571` (water). Both share `--data <client Data> --locale <probed> --background`
    with cwd the game folder; the locale is probed from Data/???? folders holding
    `locale-<same>.MPQ` at setup time, so the `.cmd` line's paths and locale reach the
    catalog as the `{data}`/`{dir}`/`{locale}` placeholders the engine fills in per run.
    This test fails when the pinned runs stop being the two runs the .cmd runs."""
    pack = _lumenveil_of("wow-wotlk")
    assert pack.setup is not None
    assert len(pack.setup.runs) == 2
    lamps, water = pack.setup.runs
    assert "--out" in lamps and "{dir}/lampdata.bin" in lamps
    assert "--liquids" in water and "{dir}/liquiddata.bin" in water
    assert "--maps" in water and "0,1,530,571" in water
    for run in pack.setup.runs:
        assert "--data" in run and "{data}" in run
        assert "--locale" in run and "{locale}" in run
        assert "--background" in run


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
    # T809 W1: the new optional pack fields.
    "platforms not a list": (
        _set("packs.3.platforms", "windows"),
        "Input should be a valid tuple",
    ),
    "platforms naming no platform": (
        _set("packs.3.platforms", ["wine"]),
        "Input should be",
    ),
    "setup exe empty": (
        _set(
            "packs.3.setup",
            {
                "run": "",
                "runs": [["--background"]],
                "writes": ["lampdata.bin"],
            },
        ),
        "at least 1 character",
    ),
    "setup naming a script": (
        _both(
            _set("packs.3.install.0.member", "Lumenveil/Lumenveil-Setup.cmd"),
            _set("packs.3.install.0.to", "Lumenveil/Lumenveil-Setup.cmd"),
            _set(
                "packs.3.setup",
                {
                    "run": "Lumenveil/Lumenveil-Setup.cmd",
                    "runs": [["--background"]],
                    "writes": ["lampdata.bin"],
                },
            ),
        ),
        "run directly",
    ),
    "setup run outside the zip": (
        _set(
            "packs.3.setup",
            {
                "run": "../lamp_extract.exe",
                "runs": [["--background"]],
                "writes": ["lampdata.bin"],
            },
        ),
        "relative POSIX path",
    ),
    "setup run naming no member": (
        _set(
            "packs.3.setup",
            {
                "run": "Lumenveil/who-is-this.exe",
                "runs": [["--background"]],
                "writes": ["lampdata.bin"],
            },
        ),
        "not an installed member",
    ),
    "setup with no runs": (
        _set(
            "packs.3.setup",
            {"run": "Lumenveil/lamp_extract.exe", "runs": [], "writes": ["lampdata.bin"]},
        ),
        "at least 1 item",
    ),
    "setup with an empty run": (
        _set(
            "packs.3.setup",
            {"run": "Lumenveil/lamp_extract.exe", "runs": [[]], "writes": ["lampdata.bin"]},
        ),
        "names no argv",
    ),
    "setup with an empty run word": (
        _set(
            "packs.3.setup",
            {"run": "Lumenveil/lamp_extract.exe", "runs": [[""]], "writes": ["lampdata.bin"]},
        ),
        "no empty words",
    ),
    "setup naming an unknown placeholder": (
        _set(
            "packs.3.setup",
            {
                "run": "Lumenveil/lamp_extract.exe",
                "runs": [["--out", "{bogus}"]],
                "writes": ["lampdata.bin"],
            },
        ),
        "{bogus}",
    ),
    "setup with no writes": (
        _set(
            "packs.3.setup",
            {
                "run": "Lumenveil/lamp_extract.exe",
                "runs": [["--background"]],
                "writes": [],
            },
        ),
        "at least 1 item",
    ),
    "setup with a negative timeout": (
        _set(
            "packs.3.setup",
            {
                "run": "Lumenveil/lamp_extract.exe",
                "runs": [["--background"]],
                "writes": ["lampdata.bin"],
                "timeout_seconds": -1,
            },
        ),
        "greater than 0",
    ),
    "setup write climbing out": (
        _set(
            "packs.3.setup",
            {
                "run": "Lumenveil/lamp_extract.exe",
                "runs": [["--background"]],
                "writes": ["../lampdata.bin"],
            },
        ),
        "relative POSIX path",
    ),
    "two packs sharing one setup output": (
        _set(
            "packs.0.setup",
            {
                "run": "patch-Y.MPQ",
                "runs": [["--background"]],
                "writes": ["Data/patch-F.MPQ"],
            },
        ),
        "setup writes",
    ),
    "a pack removing another pack's setup output": (
        _set("packs.0.remove_when_off", ["data/PATCH-f.mpq"]),
        "removes",
    ),
    "redirect host with a scheme": (
        _set("packs.3.redirect_hosts", ["https://objects.githubusercontent.com"]),
        "bare hostnames",
    ),
}


def _lumenveil_like_pack() -> dict[str, Any]:
    """A pack that uses every new T809 field once, shaped like the planned Lumenveil entry."""
    return {
        "id": "lumenveil",
        "label": "Lumenveil",
        "source": {
            "kind": "url",
            "url": "https://github.com/lumenveil/Lumenveil/releases/download/v0.1.0/Lumenveil-0.1.0.zip",
        },
        "sha256": "2" * 64,
        "install": [
            {"member": "Lumenveil/lamp_extract.exe", "to": "Lumenveil/lamp_extract.exe"},
            {"member": "version.dll", "to": "version.dll"},
        ],
        "optional": True,
        "default": False,
        "platforms": ["windows"],
        "redirect_hosts": [
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        ],
        "setup": {
            "run": "Lumenveil/lamp_extract.exe",
            "runs": [["--batch"]],
            "writes": ["lampdata.bin", "liquiddata.bin"],
            "timeout_seconds": 900,
        },
        "credit": "Lumenveil, GPL-2.0, github.com/lumenveil/Lumenveil",
    }


def test_a_pack_with_the_new_fields_parses_and_names_its_redirect_hosts() -> None:
    """The Lumenveil shape loads, keeps its values, and widens `hosts()` by the redirect hosts."""
    data = _client()
    data["packs"].append(_lumenveil_like_pack())
    client = Client.model_validate(data)
    pack = next(item for item in client.packs if item.id == "lumenveil")
    assert pack.platforms == ("windows",)
    assert pack.redirect_hosts == (
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    )
    assert pack.setup is not None
    assert pack.setup.run == "Lumenveil/lamp_extract.exe"
    assert pack.setup.runs == (("--batch",),)
    assert pack.setup.writes == ("lampdata.bin", "liquiddata.bin")
    assert pack.setup.timeout_seconds == 900
    assert pack.credit.startswith("Lumenveil")
    assert "release-assets.githubusercontent.com" in client.hosts()
    assert "objects.githubusercontent.com" in client.hosts()
    assert "github.com" in client.hosts()


def test_redirect_hosts_compare_case_insensitively() -> None:
    """Hostnames are DNS names: the catalog stores them lowered, matching `hosts()` of urls."""
    data = _client()
    data["packs"].append(_lumenveil_like_pack())
    data["packs"][-1]["redirect_hosts"] = ["Objects.GitHubUserContent.Com"]
    pack = next(item for item in Client.model_validate(data).packs if item.id == "lumenveil")
    assert pack.redirect_hosts == ("objects.githubusercontent.com",)


def test_new_fields_default_to_absent_so_every_existing_pack_behaves_as_now() -> None:
    """Absent = today's behaviour: every platform, no redirect hosts, no setup, no credit."""
    client = Client.model_validate(_client())
    pack = client.packs[3]
    assert pack.platforms == ()
    assert pack.redirect_hosts == ()
    assert pack.setup is None
    assert pack.credit == ""


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
    assert ClientPack.model_fields["platforms"].default == ()
    assert ClientPack.model_fields["redirect_hosts"].default == ()
    assert ClientPack.model_fields["setup"].default is None
    assert ClientPack.model_fields["credit"].default == ""
    assert PackSetup.model_fields["timeout_seconds"].default == 900
    assert PackSetup(run="a.exe", runs=[["--background"]], writes=["b.bin"]).runs == (
        ("--background",),
    )
