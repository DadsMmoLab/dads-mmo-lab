"""Tests for the Modules tab's panel and its pure row builder (T42).

Two halves in one file because they are two halves of one surface: the builder
answers "what does this install have, and what does each row owe?" with no Qt
at all, and the panel answers "what does a person see and press?".  The builder
tests take no `qapp` fixture on purpose -- a row list that needs a
`QApplication` to be computed is a row list nothing but the GUI can check.
"""

from __future__ import annotations

from pathlib import Path

from yulon.manifest import ClientFile, Manifest, ManifestType, Patch, Prompt, Source
from yulon.ui.widgets import modules_panel as mp


def _m(
    item_id: str,
    kind: ManifestType = "module",
    *,
    name: str | None = None,
    **extra: object,
) -> Manifest:
    """A minimal valid manifest of `kind`, with whatever the test cares about.

    Synthetic rather than read out of `manifests/wow-wotlk/`, because every one
    of these tests is about a RELATIONSHIP -- installed against catalogued,
    family against clone folder, one module requiring another -- and a shipped
    catalog that changes shape would silently stop exercising the relationship
    while the test stayed green.
    """
    source: Source | None = Source(repo=f"acme/{item_id}")
    if kind == "keg":
        source = Source(repo=f"acme/{item_id}", sparse_path=item_id)
    if kind == "mod":
        source = None
    return Manifest(
        id=item_id,
        name=name or item_id.replace("-", " ").title(),
        type=kind,
        game="wow-wotlk",
        description=f"{item_id} does a thing",
        source=source,
        **extra,  # type: ignore[arg-type]
    )


def _rows(
    manifests: list[Manifest],
    installed: dict[str, frozenset[str]] | None = None,
    session: mp.SessionState | None = None,
    client_dir: Path | None = None,
) -> tuple[mp.ModuleRow, ...]:
    return mp.build_module_rows(
        manifests,
        installed or {},
        session or mp.SessionState(),
        client_dir,
    )


def _ids(rows: tuple[mp.ModuleRow, ...], family: str) -> list[str]:
    return [r.id for r in rows if r.family == family]


def _row(rows: tuple[mp.ModuleRow, ...], item_id: str) -> mp.ModuleRow:
    found = [r for r in rows if r.id == item_id]
    assert len(found) == 1, [r.id for r in rows]
    return found[0]


def _labels(row: mp.ModuleRow) -> list[str]:
    return [c.label for c in row.chips]


# ------------------------------------------------------------ order and families


def test_installed_rows_come_first_inside_each_family() -> None:
    """The whole point of T42's first line: what you HAVE is above what you could have.

    Mutation: return the manifests in catalog order alone (drop the
    `not row.installed` sort key) and `mod-b` leads its family again.
    """
    catalog = [_m("mod-a"), _m("mod-b"), _m("mod-c")]
    rows = _rows(catalog, {"module": frozenset({"mod-c"})})

    assert _ids(rows, "module") == ["mod-c", "mod-a", "mod-b"]


def test_catalog_order_is_kept_inside_each_half() -> None:
    """Installed first, but neither half is otherwise re-ordered.

    Mutation: sort each half by id and the two installed rows come back
    `mod-a, mod-c` instead of the catalog's own `mod-c, mod-a`.
    """
    catalog = [_m("mod-c"), _m("mod-a"), _m("mod-b"), _m("mod-d")]
    rows = _rows(catalog, {"module": frozenset({"mod-c", "mod-a"})})

    assert _ids(rows, "module") == ["mod-c", "mod-a", "mod-b", "mod-d"]


def test_the_four_families_come_in_family_files_order() -> None:
    """The cards are read top to bottom in the store's own family order.

    Mutation: iterate `set(FAMILY_FILES)` instead of the mapping and the order
    stops being module, ale, mod, keg.
    """
    catalog = [_m("k1", "keg"), _m("s1", "mod"), _m("a1", "ale"), _m("m1")]
    rows = _rows(catalog)

    assert [r.family for r in rows] == list(mp.FAMILY_FILES)


def test_every_family_has_a_title() -> None:
    """A card with no title is a card nobody can name. Mutation: drop one key."""
    assert set(mp.FAMILY_TITLES) == set(mp.FAMILY_FILES)
    assert mp.FAMILY_TITLES["module"] == "C++ modules"
    assert mp.FAMILY_TITLES["keg"] == "Kegs"


# ------------------------------------------- T41's four accounting cases, re-seeded


def test_the_rows_say_which_modules_are_installed() -> None:
    """T41, 2026-09-12: "None of modules detected lol", on an install that had some.

    Re-seeded from `test_the_modules_list_says_which_modules_are_installed`:
    the mark on the row's text is now `ModuleRow.installed`, which the panel
    draws as the `Installed` badge.

    Mutation: return `installed=True` for every row and the second assertion
    (exactly one) fails; return `False` for every row and the first does.
    """
    catalog = [_m("mod-transmog"), _m("mod-aoe-loot")]
    rows = _rows(catalog, {"module": frozenset({"mod-transmog"})})

    assert [r.id for r in rows if r.installed] == ["mod-transmog"]
    assert _row(rows, "mod-transmog").catalogued is True
    assert _row(rows, "mod-aoe-loot").installed is False


def test_a_module_on_disk_the_catalog_never_heard_of_still_gets_a_row() -> None:
    """It is installed, whatever the catalog thinks.

    Re-seeded from the T41 test of the same name. The row now carries
    `catalogued=False` and `NOT_IN_CATALOG` as its description rather than the
    sentence being glued into a list line.

    Mutation: skip ids that are in no manifest and the row disappears.
    """
    rows = _rows([_m("mod-transmog")], {"module": frozenset({"mod-something-homemade"})})

    mine = _row(rows, "mod-something-homemade")
    assert mine.installed is True
    assert mine.catalogued is False
    assert mine.description == mp.NOT_IN_CATALOG
    assert mine.family == "module"
    # It is appended to its family, after the catalogued rows of that family.
    assert _ids(rows, "module") == ["mod-transmog", "mod-something-homemade"]


def test_a_game_with_no_installed_reading_marks_nothing() -> None:
    """The three CMaNGOS games have no modules folder; every row reads Not installed.

    Re-seeded from `test_a_game_with_no_installed_modules_seam_lists_the_catalog
    _unchanged`.

    Mutation: default a missing family to "everything is installed" and every
    row flips.
    """
    rows = _rows([_m("mod-transmog"), _m("a1", "ale")], {})

    assert rows, "the catalog still lists"
    assert not any(r.installed for r in rows)


def test_a_family_is_marked_from_its_own_clone_folder_not_from_modules() -> None:
    """Review, 2026-09-12: `apply.CLONE_DIRS` gives each family a different folder.

    Re-seeded from the T41 test of the same name. `mod-ale` is a MODULE
    manifest; the clone is in `ale_scripts/`, so the module row must stay
    unmarked and an uncatalogued ALE row must appear for the clone.

    The two families are CROSSED on purpose: the module folder holds the ale
    manifest's id and the ale folder holds the module manifest's id. Neither
    catalogued row may be marked, and each clone gets an uncatalogued row in the
    family whose folder it was really found in.

    Mutation: read `installed.get("module")` for every family and the ale row
    `paragon` is marked from a clone that is not in `ale_scripts/` at all.
    """
    rows = _rows(
        [_m("mod-ale"), _m("paragon", "ale")],
        {"module": frozenset({"paragon"}), "ale": frozenset({"mod-ale"})},
    )

    catalogued = [(r.id, r.family, r.installed) for r in rows if r.catalogued]
    assert catalogued == [("mod-ale", "module", False), ("paragon", "ale", False)], catalogued
    unknown = [(r.id, r.family) for r in rows if not r.catalogued]
    assert unknown == [("paragon", "module"), ("mod-ale", "ale")], unknown


def test_a_clone_matched_in_one_family_is_not_listed_again_as_unknown_in_another() -> None:
    """Measured live, 2026-09-12: `bmah` appeared twice.

    Re-seeded from the T41 test of the same name. `CLONE_DIRS` puts ale and keg
    in one folder, so a keg's clone is read into BOTH families' sets; the keg
    manifest matches it and the ale copy must not become a second row.

    Mutation: account for uncatalogued names per FAMILY instead of per clone
    FOLDER (drop `_clone_dir_of`) and `bmah` is listed twice again.
    """
    rows = _rows(
        [_m("bmah", "keg")],
        {"ale": frozenset({"bmah"}), "keg": frozenset({"bmah"})},
    )

    bmah = [r for r in rows if r.id == "bmah"]
    assert len(bmah) == 1, bmah
    assert bmah[0].family == "keg" and bmah[0].catalogued is True


def test_one_uncatalogued_clone_in_a_shared_folder_gets_one_row_not_two() -> None:
    """The other half of the shared folder: nobody's manifest matches it at all.

    `ale` and `keg` read the same directory, so an unknown clone in it arrives
    in both sets. It is one thing on disk and must be one row.

    Mutation: drop the `known.add(name)` inside the uncatalogued loop and the
    keg card grows a duplicate of the ale card's row.
    """
    rows = _rows([], {"ale": frozenset({"who-is-this"}), "keg": frozenset({"who-is-this"})})

    assert [(r.id, r.family) for r in rows] == [("who-is-this", "ale")]


# ------------------------------------------------------------------- the six chips


def test_the_rebuild_pending_chip_is_on_exactly_the_owed_module() -> None:
    """Mutation: ignore `session.rebuild_owed` and no row carries the chip."""
    catalog = [_m("mod-a"), _m("mod-b")]
    owed = mp.SessionState(rebuild_owed=frozenset({"mod-a"}))
    rows = _rows(catalog, {"module": frozenset({"mod-a", "mod-b"})}, owed)

    assert mp.CHIP_REBUILD_PENDING in _labels(_row(rows, "mod-a"))
    assert mp.CHIP_REBUILD_PENDING not in _labels(_row(rows, "mod-b"))
    chip = next(c for c in _row(rows, "mod-a").chips if c.label == mp.CHIP_REBUILD_PENDING)
    assert chip.kind == "owed"


def test_the_sql_pending_chip_names_the_files_it_is_waiting_on() -> None:
    """The detail is what a press writes into the report, so it must carry the files.

    Mutation: set the detail to the label and the file name is gone from it.
    """
    session = mp.SessionState(sql_owed={"mod-a": ("data/sql/db-world/one.sql",)})
    rows = _rows([_m("mod-a")], {"module": frozenset({"mod-a"})}, session)

    chip = next(c for c in _row(rows, "mod-a").chips if c.label == mp.CHIP_SQL_PENDING)
    assert chip.kind == "owed"
    assert "data/sql/db-world/one.sql" in chip.detail


def test_the_update_chip_counts_and_is_absent_at_zero() -> None:
    """`ModuleUpdate.behind` of 0 is "up to date", not "an update".

    Mutation: use `>= 0` instead of `> 0` and the up-to-date module grows a
    "0 behind" chip.
    """
    session = mp.SessionState(behind={"mod-a": 3, "mod-b": 0})
    rows = _rows([_m("mod-a"), _m("mod-b")], {"module": frozenset({"mod-a", "mod-b"})}, session)

    assert mp.chip_update_label(3) in _labels(_row(rows, "mod-a"))
    assert "3" in mp.chip_update_label(3)
    assert not [c for c in _row(rows, "mod-b").chips if c.label.startswith("Update available")]


def test_the_asks_a_question_chip_is_only_on_an_uninstalled_manifest_with_no_default() -> None:
    """The two AH-bot manifests are the shipped shape of this: a prompt with no default.

    It is a fact about INSTALLING, so an installed row must not carry it.

    Mutation: drop the `default is None` clause and every manifest with any
    prompt at all carries the chip.
    """
    asks = _m(
        "mod-ah-bot",
        prompts=(Prompt(key="bot_guid", question="Which GUID?"),),
        patches=(Patch(file="x.conf", find="a", replace="{bot_guid}"),),
    )
    defaulted = _m(
        "mod-quiet",
        prompts=(Prompt(key="level", question="Which level?", default="80"),),
        patches=(Patch(file="y.conf", find="a", replace="{level}"),),
    )
    rows = _rows([asks, defaulted])
    assert mp.CHIP_ASKS_A_QUESTION in _labels(_row(rows, "mod-ah-bot"))
    assert mp.CHIP_ASKS_A_QUESTION not in _labels(_row(rows, "mod-quiet"))

    installed = _rows([asks], {"module": frozenset({"mod-ah-bot"})})
    assert mp.CHIP_ASKS_A_QUESTION not in _labels(_row(installed, "mod-ah-bot"))


def test_the_client_folder_chip_appears_only_while_no_folder_is_set() -> None:
    """T36's folder: a manifest with `client` files cannot install without one.

    Mutation: ignore `client_dir` and the chip stays up after the folder is set.
    """
    keg = _m("bmah", "keg", client=(ClientFile(src="BlackMarketUI", dest="addons"),))
    plain = _m("mod-a")

    none_set = _rows([keg, plain])
    assert mp.CHIP_NEEDS_CLIENT_FOLDER in _labels(_row(none_set, "bmah"))
    assert mp.CHIP_NEEDS_CLIENT_FOLDER not in _labels(_row(none_set, "mod-a"))

    with_folder = _rows([keg], client_dir=Path("/clients/wotlk"))
    assert mp.CHIP_NEEDS_CLIENT_FOLDER not in _labels(_row(with_folder, "bmah"))


def test_required_by_names_only_installed_dependants_and_locks_remove() -> None:
    """A module another INSTALLED module needs must not be removable behind its back.

    Both halves in one test because they are one rule: the chip says who, and
    `removable=False` is what the chip is FOR.

    Mutation: count dependants from the catalog rather than from what is
    installed, and `mod-base` is locked by a module nobody ever installed.
    """
    base = _m("mod-base", name="Base")
    user = _m("mod-user", name="The User", requires=("mod-base",))
    wanter = _m("mod-wanter", name="Never Installed", requires=("mod-base",))

    rows = _rows([base, user, wanter], {"module": frozenset({"mod-base", "mod-user"})})
    row = _row(rows, "mod-base")

    assert mp.chip_required_by_label(("The User",)) in _labels(row)
    assert "Never Installed" not in " ".join(_labels(row))
    assert row.removable is False
    assert row.remove_reason is not None and "The User" in row.remove_reason
    # And the dependant itself is nobody's dependency.
    assert _row(rows, "mod-user").removable is True


def test_an_installed_module_nothing_needs_is_removable_and_carries_no_chips() -> None:
    """The ordinary installed row: a badge and nothing else.

    Mutation: make `removable` default to False and every ordinary row locks.
    """
    rows = _rows([_m("mod-a")], {"module": frozenset({"mod-a"})})

    row = _row(rows, "mod-a")
    assert row.chips == ()
    assert row.removable is True and row.remove_reason is None


def test_a_row_carries_its_github_link_and_its_conf_paths() -> None:
    """The row skeleton's other two fields, taken from the manifest and not invented.

    Mutation: build the URL from `source.repo` directly and a slug row shows
    `acme/mod-a` where a link belongs.
    """
    from yulon.manifest import ConfFile

    rows = _rows(
        [
            _m("mod-a", conf=(ConfFile(file="env/dist/etc/modules/a.conf"),)),
            _m("s1", "mod"),
        ]
    )

    assert _row(rows, "mod-a").url == "https://github.com/acme/mod-a.git"
    assert _row(rows, "mod-a").paths == ("env/dist/etc/modules/a.conf",)
    assert _row(rows, "s1").url is None
    assert _row(rows, "s1").paths == ()


def test_an_uncatalogued_row_still_takes_the_owed_chips() -> None:
    """The session knows things about an id whether or not a manifest does.

    Mutation: build uncatalogued rows with `chips=()` and a hand-cloned module
    that owes a rebuild says nothing about it.
    """
    session = mp.SessionState(rebuild_owed=frozenset({"mod-homemade"}))
    rows = _rows([], {"module": frozenset({"mod-homemade"})}, session)

    assert mp.CHIP_REBUILD_PENDING in _labels(_row(rows, "mod-homemade"))
