"""The shared reader of a client add-on source (T613 PR-1): which add-ons, under which names.

`addon_layout.find_addons()` is handed a folder (a staged zip, a copied folder or
a clone) and the Interface number of the client the add-ons go into, and answers
with the add-ons found or one plain sentence saying why not. Every refusal here
is pinned by wording only its own rule produces, with a fixture that trips that
rule alone (`nine-ways-a-test-proves-nothing`, mechanism 1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import addon_layout
from yulon.addon_layout import Found, Refusal, band, find_addons, read_interface
from yulon.catalog.catalog import load_catalog


def _toc(folder: Path, stem: str, interface: str | None = "11200", *, extra: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"## Title: {stem}"]
    if interface is not None:
        lines.insert(0, f"## Interface: {interface}")
    toc = folder / f"{stem}.toc"
    toc.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    (folder / f"{stem}.lua").write_text("-- code\n", encoding="utf-8")
    return toc


def _found(result: Found | Refusal) -> Found:
    assert isinstance(result, Found), getattr(result, "sentence", result)
    return result


def _refused(result: Found | Refusal) -> str:
    assert isinstance(result, Refusal), result
    assert result.sentence.endswith(" Nothing was changed."), result.sentence
    return result.sentence


def _names(result: Found | Refusal) -> list[str]:
    return [addon.name for addon in _found(result).addons]


# --- 1. the band: [major * 10000, the client's own number] -------------------------------


@pytest.mark.parametrize(
    ("client", "low", "high"),
    [(30300, 30000, 30300), (20400, 20000, 20400), (11200, 10000, 11200)],
)
def test_the_band_runs_from_the_major_versions_first_number_to_the_clients_own(
    client: int, low: int, high: int
) -> None:
    assert band(client) == (low, high)


@pytest.mark.parametrize(
    ("client", "number"),
    [
        (11200, 11200),
        (11200, 10000),
        (20400, 20400),
        (20400, 20000),
        (30300, 30300),
        (30300, 30000),
    ],
)
def test_a_number_inside_the_band_is_installed(tmp_path: Path, client: int, number: int) -> None:
    _toc(tmp_path / "src" / "Probe", "Probe", str(number))

    assert _names(find_addons(tmp_path / "src", interface=client, shipped={})) == ["Probe"]


@pytest.mark.parametrize(
    ("client", "number", "made_for"),
    [
        (11200, 11300, "WoW Classic"),
        (11200, 11503, "WoW Classic Era"),
        (20400, 20501, "Burning Crusade Classic"),
        (20400, 20500, "Burning Crusade Classic"),
        (30300, 30400, "Wrath of the Lich King Classic"),
        (11200, 20400, "The Burning Crusade"),
        (20400, 30300, "Wrath of the Lich King"),
        (30300, 40400, "Cataclysm"),
    ],
)
def test_a_number_above_the_band_is_a_later_game_and_is_refused(
    tmp_path: Path, client: int, number: int, made_for: str
) -> None:
    """11503 on 1.12 is the case T596a's "20000 or more" ceiling let through."""
    _toc(tmp_path / "src" / "Probe", "Probe", str(number))

    said = _refused(find_addons(tmp_path / "src", interface=client, shipped={}))

    assert said.startswith(f"Probe is made for {made_for} (Interface {number}); ")
    assert f"(Interface {client})" in said


@pytest.mark.parametrize(("client", "number"), [(11200, 9999), (30300, 20400), (30300, 29999)])
def test_a_number_below_the_band_is_an_older_game_and_is_refused(
    tmp_path: Path, client: int, number: int
) -> None:
    _toc(tmp_path / "src" / "Probe", "Probe", str(number))

    said = _refused(find_addons(tmp_path / "src", interface=client, shipped={}))

    assert said.startswith(f"Probe is made for an older game (Interface {number}); ")
    assert "does not work in it" in said


def test_an_older_patch_of_the_same_game_is_installed_with_an_out_of_date_note(
    tmp_path: Path,
) -> None:
    """Owner Q4: 3.2 on 3.3.5a installs, and the player is told about the tick box."""
    _toc(tmp_path / "src" / "Probe", "Probe", "30200")

    found = _found(find_addons(tmp_path / "src", interface=30300, shipped={}))

    assert [a.name for a in found.addons] == ["Probe"]
    assert any("out of date" in note and "Probe" in note for note in found.notes)


def test_the_clients_own_number_has_no_out_of_date_note(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "Probe", "Probe", "30300")

    found = _found(find_addons(tmp_path / "src", interface=30300, shipped={}))

    assert not any("out of date" in note for note in found.notes)


def test_a_toc_with_no_interface_line_is_installed_with_a_note(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "Probe", "Probe", None)

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert [a.name for a in found.addons] == ["Probe"]
    assert found.addons[0].interface is None
    assert any("no ## Interface line" in note for note in found.notes)


# --- 2. the first number, not the smallest ----------------------------------------------


def test_the_first_number_on_the_interface_line_decides_not_the_smallest(tmp_path: Path) -> None:
    """An old client reads only the first number; T596a took `min()`."""
    _toc(tmp_path / "src" / "Probe", "Probe", "30300, 11200")

    said = _refused(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert "(Interface 30300)" in said


def test_the_first_number_decides_not_the_largest(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "Probe", "Probe", "11200, 30300")

    assert _names(find_addons(tmp_path / "src", interface=11200, shipped={})) == ["Probe"]


def test_read_interface_takes_the_first_integer(tmp_path: Path) -> None:
    toc = tmp_path / "X.toc"
    toc.write_text("## Title: X\n## Interface: 20400 30300\n## Interface: 11200\n")

    assert read_interface(toc) == 20400


def test_an_interface_dash_flavour_line_is_not_the_interface(tmp_path: Path) -> None:
    """`## Interface-Classic:` is a newer client's per-flavour line, not what 1.12 reads."""
    toc = tmp_path / "X.toc"
    toc.write_text("## Interface-Classic: 11503\n## Interface: 11200\n")

    assert read_interface(toc) == 11200


# --- 3. a byte-order mark ----------------------------------------------------------------


def test_a_bom_in_front_of_the_interface_line_does_not_hide_it(tmp_path: Path) -> None:
    folder = tmp_path / "src" / "Probe"
    folder.mkdir(parents=True)
    (folder / "Probe.toc").write_bytes(b"\xef\xbb\xbf## Interface: 30300\r\n## Title: Probe\r\n")

    assert read_interface(folder / "Probe.toc") == 30300
    said = _refused(find_addons(tmp_path / "src", interface=11200, shipped={}))
    assert "(Interface 30300)" in said


# --- 4. the name is the toc's stem --------------------------------------------------------


def test_a_github_archive_folder_installs_under_the_tocs_name(tmp_path: Path) -> None:
    """`pfUI-master/pfUI.toc`, what a GitHub archive zip gives, is the add-on `pfUI`."""
    _toc(tmp_path / "src" / "pfUI-master", "pfUI")

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert [(a.name, a.src, a.toc) for a in found.addons] == [
        ("pfUI", "pfUI-master", "pfUI-master/pfUI.toc")
    ]


def test_a_child_folder_beside_others_is_named_after_its_toc_too(tmp_path: Path) -> None:
    """No unwrap here (two folders): the child rule alone must take the toc's name."""
    _toc(tmp_path / "src" / "pfUI-master", "pfUI")
    _toc(tmp_path / "src" / "Other", "Other")

    assert _names(find_addons(tmp_path / "src", interface=11200, shipped={})) == ["Other", "pfUI"]


def test_the_tocs_case_is_kept(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "mobstats", "MobStats")

    assert _names(find_addons(tmp_path / "src", interface=11200, shipped={})) == ["MobStats"]


def test_a_toc_at_the_root_makes_the_root_the_addon(tmp_path: Path) -> None:
    _toc(tmp_path / "MobStats-main", "MobStats")

    found = _found(find_addons(tmp_path / "MobStats-main", interface=11200, shipped={}))

    assert [(a.name, a.src) for a in found.addons] == [("MobStats", ".")]


def test_a_single_wrapping_folder_is_looked_inside(tmp_path: Path) -> None:
    """`X-1.2/AddOns/X/X.toc`: the wrapper is unwrapped, then the add-on folder is read."""
    _toc(tmp_path / "src" / "Pack-1.2" / "AddOns" / "X", "X")
    (tmp_path / "src" / ".DS_Store").write_bytes(b"")

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert [(a.name, a.src) for a in found.addons] == [("X", "Pack-1.2/AddOns/X")]


@pytest.mark.parametrize("parent", ["addon", "addons", "AddOns", "Interface/AddOns"])
def test_addons_under_an_addon_folder_are_found(tmp_path: Path, parent: str) -> None:
    _toc(tmp_path / "src" / parent / "Probe", "Probe")
    (tmp_path / "src" / "README.md").write_text("readme\n")

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert [(a.name, a.src) for a in found.addons] == [("Probe", f"{parent}/Probe")]


# --- 5. variant tocs ----------------------------------------------------------------------


def _pfui(root: Path) -> None:
    folder = root / "pfUI-master"
    _toc(folder, "pfUI", "11200")
    (folder / "pfUI-tbc.toc").write_text("## Interface: 20400\n## Title: pfUI\n")


def test_a_variant_toc_is_dropped_when_the_main_toc_fits(tmp_path: Path) -> None:
    _pfui(tmp_path / "src")

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert [(a.name, a.toc) for a in found.addons] == [("pfUI", "pfUI-master/pfUI.toc")]


def test_a_variant_toc_is_picked_when_the_main_does_not_fit_and_it_does(tmp_path: Path) -> None:
    _pfui(tmp_path / "src")

    found = _found(find_addons(tmp_path / "src", interface=20400, shipped={}))

    assert [(a.name, a.toc, a.interface) for a in found.addons] == [
        ("pfUI-tbc", "pfUI-master/pfUI-tbc.toc", 20400)
    ]


def test_no_toc_fitting_refuses_on_the_main_tocs_number(tmp_path: Path) -> None:
    _pfui(tmp_path / "src")

    said = _refused(find_addons(tmp_path / "src", interface=30300, shipped={}))

    assert said.startswith("pfUI is made for an older game (Interface 11200)")


def test_several_main_tocs_are_refused_and_listed(tmp_path: Path) -> None:
    folder = tmp_path / "src" / "Pack"
    _toc(folder, "Alpha")
    _toc(folder, "Beta")

    said = _refused(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert said.startswith("Pack has several .toc files (Alpha.toc, Beta.toc), and Yu'lon cannot")


def test_a_folder_holding_several_tocs_loads_the_one_of_its_own_name(tmp_path: Path) -> None:
    """The client loads `<Folder>/<Folder>.toc`, so that one is not ambiguous."""
    folder = tmp_path / "src" / "Alpha"
    _toc(folder, "Alpha")
    _toc(folder, "Beta")

    assert _names(find_addons(tmp_path / "src", interface=11200, shipped={})) == ["Alpha"]


# --- 6. several add-ons in one source ---------------------------------------------------


def test_two_addons_in_one_source_are_both_found(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "Bagnon", "Bagnon")
    _toc(tmp_path / "src" / "Bagnon_Config", "Bagnon_Config")

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert [(a.name, a.src) for a in found.addons] == [
        ("Bagnon", "Bagnon"),
        ("Bagnon_Config", "Bagnon_Config"),
    ]


def test_two_addons_of_one_name_in_one_source_are_refused(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "pfUI", "pfUI")
    _toc(tmp_path / "src" / "AddOns" / "pfui-old", "PFUI")

    said = _refused(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert "holds two add-ons named pfUI" in said


# --- 9. names Yu'lon already installs, and the game's own -------------------------------


@pytest.mark.parametrize(
    ("shipped_name", "found_as", "item"),
    [
        ("TortoiseBotsManager", "tortoisebotsmanager", "TortoiseBots Manager"),
        ("TortoiseGMManager", "TORTOISEGMMANAGER", "TortoiseGM Manager"),
        ("BattlePass", "battlepass", "BattlePass"),
        ("multiclass", "MultiClass", "WoW Unbound's add-ons"),
    ],
)
def test_a_name_yulon_already_installs_is_refused_whatever_its_case(
    tmp_path: Path, shipped_name: str, found_as: str, item: str
) -> None:
    _toc(tmp_path / "src" / found_as, found_as)

    said = _refused(find_addons(tmp_path / "src", interface=11200, shipped={shipped_name: item}))

    assert said.startswith(
        f"{found_as} is the name of an add-on Yu'lon already installs for this server ({item})."
    )


@pytest.mark.parametrize("name", ["Blizzard_AuctionUI", "blizzard_auctionui", "BLIZZARD_x"])
def test_one_of_the_games_own_names_is_refused_whatever_its_case(tmp_path: Path, name: str) -> None:
    _toc(tmp_path / "src" / name, name)

    said = _refused(find_addons(tmp_path / "src", interface=11200, shipped={}))

    assert said.startswith(f"{name} is one of the game's own add-on names")


def test_a_name_that_only_starts_like_blizzard_is_not_the_games_own(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "BlizzardStuff", "BlizzardStuff")

    assert _names(find_addons(tmp_path / "src", interface=11200, shipped={})) == ["BlizzardStuff"]


# --- notes: dependencies and .pkgmeta ---------------------------------------------------


def test_a_dependency_missing_from_the_source_is_noted(tmp_path: Path) -> None:
    _toc(
        tmp_path / "src" / "Probe",
        "Probe",
        extra="## Dependencies: Ace3, Blizzard_AuctionUI\n## RequiredDeps: LibStub\n",
    )

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    notes = " ".join(found.notes)
    assert "Probe needs Ace3, LibStub" in notes
    assert "Blizzard_AuctionUI" not in notes


def test_a_dependency_in_the_source_or_in_the_client_is_not_noted(tmp_path: Path) -> None:
    _toc(tmp_path / "src" / "Probe", "Probe", extra="## Dependencies: ace3, LibStub\n")
    _toc(tmp_path / "src" / "Ace3", "Ace3")

    found = _found(
        find_addons(tmp_path / "src", interface=11200, shipped={}, installed=("libstub",))
    )

    assert not any("needs" in note for note in found.notes)


def test_pkgmeta_externals_that_are_missing_are_warned_about(tmp_path: Path) -> None:
    root = tmp_path / "src" / "Probe-main"
    _toc(root, "Probe")
    (root / "Libs" / "LibStub").mkdir(parents=True)
    (root / ".pkgmeta").write_text(
        "package-as: Probe\n"
        "externals:\n"
        "  Libs/LibStub: https://repos.wowace.com/wow/libstub/trunk\n"
        "  Libs/AceAddon-3.0:\n"
        "    url: https://repos.wowace.com/wow/ace3/trunk/AceAddon-3.0\n"
        "ignore:\n"
        "  - README.md\n"
    )

    found = _found(find_addons(tmp_path / "src", interface=11200, shipped={}))

    warned = [note for note in found.notes if ".pkgmeta" in note]
    assert len(warned) == 1
    assert "Libs/AceAddon-3.0" in warned[0] and "Libs/LibStub" not in warned[0]
    assert "release zip" in warned[0]


# --- no add-on at all ---------------------------------------------------------------------


def test_a_source_with_no_toc_says_what_it_holds(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    for name in ("a.lua", "b.lua", "c.lua", "d.lua", "e.lua", "f.lua"):
        (root / name).write_text("--\n")

    said = _refused(find_addons(root, interface=11200, shipped={}, label="the zip"))

    assert said.startswith("Yu'lon found no add-on in the zip: an add-on is a folder with a .toc")
    assert "It holds: a.lua, b.lua, c.lua, d.lua, e.lua and 1 more." in said


# --- 3 (catalog). each game's client Interface number -------------------------------------


@pytest.mark.parametrize(
    ("game", "number"),
    [
        ("wow-wotlk", 30300),
        ("wow-unbound", 30300),
        ("wow-centurion", 30300),
        ("wow-tbc", 20400),
        ("wow-vanilla", 11200),
        ("wow-tortoise", 11200),
    ],
)
def test_each_game_names_its_clients_addon_interface(game: str, number: int) -> None:
    assert load_catalog().get(game).client.addon_interface == number


def test_every_game_names_an_addon_interface_the_reader_can_word() -> None:
    """A game added later without the number, or with one the sentences cannot name, fails here."""
    for entry in load_catalog().games:
        number = entry.client.addon_interface
        assert number is not None, entry.id
        assert number in addon_layout.CLIENT_NAMES, (entry.id, number)
