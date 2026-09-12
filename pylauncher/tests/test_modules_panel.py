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


# --------------------------------------------------------------------- the panel


def _panel(rows: tuple[mp.ModuleRow, ...]) -> mp.ModulesPanel:
    panel = mp.ModulesPanel()
    panel.set_rows(rows)
    return panel


def _catalog_rows(
    installed: dict[str, frozenset[str]] | None = None,
    catalog: list[Manifest] | None = None,
    session: mp.SessionState | None = None,
) -> tuple[mp.ModuleRow, ...]:
    return _rows(
        catalog or [_m("mod-a"), _m("mod-b"), _m("a1", "ale"), _m("a2", "ale")],
        installed,
        session,
    )


def test_a_family_with_something_installed_starts_collapsed(qapp: object) -> None:
    """The card you open the tab on shows what you HAVE; the rest is one press away.

    Mutation: open every family and the WotLK card's 20-odd uninstalled rows
    push the installed ones off the screen again.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))

    assert panel.available_open("module") is False
    assert panel.available_open("ale") is True, "a family with nothing installed opens"


def test_a_toggle_press_flips_the_family_and_set_rows_keeps_it(qapp: object) -> None:
    """Both halves of the rule, because the second is the one a reload destroys.

    Mutation: recompute the open/closed state inside `set_rows()` and the
    section a user opened snaps shut the next time anything reloads the tab.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))
    panel.available_toggle("module").click()
    assert panel.available_open("module") is True

    panel.set_rows(_catalog_rows({"module": frozenset({"mod-a"})}))
    assert panel.available_open("module") is True

    panel.available_toggle("module").click()
    assert panel.available_open("module") is False


def test_a_rows_install_button_emits_that_rows_id(qapp: object) -> None:
    """One handler, one id — the tab no longer has a "selected" button to get wrong.

    Mutation: emit the panel's `selected_id()` instead of the row's own id and a
    press installs whatever was highlighted rather than what was pressed.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))
    seen: list[str] = []
    panel.install_pressed.connect(seen.append)
    removed: list[str] = []
    panel.remove_pressed.connect(removed.append)

    panel.select("mod-a")
    panel.row("mod-b").install_button.click()
    panel.row("mod-a").remove_button.click()

    assert seen == ["mod-b"]
    assert removed == ["mod-a"]


def test_an_installed_row_has_no_install_button_and_the_reverse(qapp: object) -> None:
    """One button per row, and it is the one that would do something.

    Mutation: build both buttons on every row and an installed module offers an
    Install press that re-clones over itself.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))

    assert panel.row("mod-a").install_button is None
    assert panel.row("mod-a").remove_button is not None
    assert panel.row("mod-b").install_button is not None
    assert panel.row("mod-b").remove_button is None


def test_an_uncatalogued_row_has_no_buttons_at_all(qapp: object) -> None:
    """T41's rows carry no manifest, so there are no steps to press.

    Mutation: build a Remove button for `catalogued=False` and the press reaches
    an applier with nothing to hand it.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-homemade"})}))

    row = panel.row("mod-homemade")
    assert row.install_button is None and row.remove_button is None
    assert row.data.catalogued is False


def test_a_row_another_installed_module_needs_cannot_be_removed(qapp: object) -> None:
    """`removable=False` is drawn, not just recorded.

    Mutation: ignore `ModuleRow.removable` and the Remove button is live on a
    module something else is compiled against.
    """
    base, user = _m("mod-base", name="Base"), _m("mod-user", name="User", requires=("mod-base",))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-base", "mod-user"})}, [base, user]))

    button = panel.row("mod-base").remove_button
    assert button is not None and button.isEnabled() is False
    assert "User" in button.toolTip()
    assert panel.row("mod-user").remove_button.isEnabled() is True


def test_set_enabled_actions_false_disables_every_row_button(qapp: object) -> None:
    """The busy lock, and the `store is None or applier is None` gate, in one call.

    Mutation: only disable the first card's buttons (or only the installed
    half) and a press during a rebuild reaches the applier.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"}), "ale": frozenset({"a1"})}))
    buttons = [
        b
        for row_id in ("mod-a", "mod-b", "a1", "a2")
        for b in (panel.row(row_id).install_button, panel.row(row_id).remove_button)
        if b is not None
    ]
    assert len(buttons) == 4

    panel.set_enabled_actions(False)
    assert [b.isEnabled() for b in buttons] == [False] * 4

    panel.set_enabled_actions(True)
    # Back on -- except the one the row itself forbids, which is not the busy
    # lock's to hand back.
    assert [b.isEnabled() for b in buttons] == [True] * 4


def test_set_enabled_actions_true_does_not_unlock_an_unremovable_row(qapp: object) -> None:
    """A job finishing must not hand back a control the ROW never armed.

    Mutation: `setEnabled(enabled)` for every button and a rebuild finishing
    makes a depended-on module removable.
    """
    base, user = _m("mod-base", name="Base"), _m("mod-user", name="User", requires=("mod-base",))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-base", "mod-user"})}, [base, user]))

    panel.set_enabled_actions(False)
    panel.set_enabled_actions(True)

    assert panel.row("mod-base").remove_button.isEnabled() is False


def test_the_selection_survives_a_rebuild_and_a_gone_id_does_not(qapp: object) -> None:
    """`set_rows()` is called after every install; losing the selection loses the place.

    Mutation: clear `_selected` in `set_rows()` unconditionally and the row a
    user was reading is deselected by a reload they did not ask for.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))
    panel.select("mod-b")
    assert panel.selected_id() == "mod-b"

    panel.set_rows(_catalog_rows({"module": frozenset({"mod-a", "mod-b"})}))
    assert panel.selected_id() == "mod-b"

    panel.set_rows(_catalog_rows(catalog=[_m("mod-a")]))
    assert panel.selected_id() is None


def test_a_click_anywhere_on_a_row_selects_it(qapp: object) -> None:
    """Not only on the button: the whole row is the target (the mockup's own rule).

    Mutation: connect the selection to the buttons alone and a click on the
    description selects nothing, which is what the context menu then acts on.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))
    panel.resize(600, 800)
    panel.show()
    chosen: list[str] = []
    panel.row_selected.connect(chosen.append)

    QTest.mouseClick(panel.row("mod-b").description_label, Qt.MouseButton.LeftButton)

    assert panel.selected_id() == "mod-b"
    assert chosen == ["mod-b"]
    panel.hide()


def test_an_owed_chip_press_names_its_row_and_its_label(qapp: object) -> None:
    """An owed chip is a press; the view turns the pair into the chip's own detail.

    Mutation: emit only the id and the view cannot tell which of a row's three
    owed chips was pressed.
    """
    session = mp.SessionState(rebuild_owed=frozenset({"mod-a"}))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))
    pressed: list[tuple[str, str]] = []
    panel.chip_pressed.connect(lambda mid, label: pressed.append((mid, label)))

    chips = panel.row("mod-a").chip_buttons
    assert len(chips) == 1
    chips[0].click()

    assert pressed == [("mod-a", mp.CHIP_REBUILD_PENDING)]


def test_a_fact_chip_is_not_a_press_and_carries_its_detail_as_a_tooltip(qapp: object) -> None:
    """Nothing can be done about a fact, so nothing happens when it is pressed.

    Mutation: connect every chip to `chip_pressed` and pressing "asks a
    question" writes a sentence into the report that answers nothing.
    """
    asks = _m(
        "mod-ah-bot",
        prompts=(Prompt(key="bot_guid", question="Which GUID?"),),
        patches=(Patch(file="x.conf", find="a", replace="{bot_guid}"),),
    )
    panel = _panel(_catalog_rows(catalog=[asks]))
    pressed: list[tuple[str, str]] = []
    panel.chip_pressed.connect(lambda mid, label: pressed.append((mid, label)))

    chips = panel.row("mod-ah-bot").chip_buttons
    assert [c.text() for c in chips] == [mp.CHIP_ASKS_A_QUESTION]
    assert "Which GUID?" in chips[0].toolTip()
    chips[0].click()

    assert pressed == []


def test_a_panel_with_no_rows_says_so_instead_of_showing_nothing(qapp: object) -> None:
    """The three CMaNGOS games have no manifest store at all.

    Mutation: return early on an empty sequence and the tab is a blank box.
    """
    panel = _panel(())

    assert panel.empty_label.isVisibleTo(panel)
    assert panel.empty_label.text() == mp.NO_MODULES_NOTE

    panel.set_rows(_catalog_rows())
    assert not panel.empty_label.isVisibleTo(panel)


def test_the_installed_header_counts_that_family_only(qapp: object) -> None:
    """The count is the reading; a count off by a family is worse than no count.

    Mutation: count every installed row rather than the family's own and the
    ALE card claims the modules too.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"}), "ale": frozenset({"a1", "a2"})}))

    assert "(1)" in panel.installed_header("module").text()
    assert "(2)" in panel.installed_header("ale").text()
    assert "1" in panel.available_toggle("module").text()
    assert panel.available_toggle("ale") is None, "no uninstalled ale rows, so no toggle"


def test_the_panel_reports_rows_in_the_order_it_was_handed_them(qapp: object) -> None:
    """`rows()` is the order `set_rows()` was HANDED — the builder's answer, unaltered.

    It is NOT the drawn order, and round 2 corrected this docstring for saying
    so: what a person sees is `drawn_rows()`, because `_FamilyCard.fill()` puts
    the installed half in its own box above the available one whatever order it
    was handed. The two are asserted by different tests on purpose.

    Written after a view test agreed with T42's "installed first" line by
    accident: the cards used to be filled by building the installed half first,
    so `rows()` came back installed-first whatever `build_module_rows()` had
    decided, and deleting the builder's sort left that test green. The rows here
    are handed over in an order the builder would never produce, so only a panel
    that preserves what it was given can pass.

    Mutation: fill the card with `[self._make(row) for row in family if
    row.installed]` first and this comes back `["b", "a"]`.
    """
    handed = (
        mp.ModuleRow("a", "module", "A", "", None, False, True, (), (), True, None),
        mp.ModuleRow("b", "module", "B", "", None, True, True, (), (), True, None),
    )
    panel = _panel(handed)

    assert [r.data.id for r in panel.rows()] == ["a", "b"]


# ------------------------------------------------- round 2: one id, two families


def test_an_id_in_two_families_is_installed_only_where_its_own_folder_says_so() -> None:
    """`installed` is keyed by FAMILY; a row keyed by id alone reads the wrong folder.

    Round 2, Codex: `installed_ids` was `dict[str, Manifest]`, so a `mod` and a
    `module` sharing an id collapsed into one entry and BOTH catalog rows read
    installed when only one clone was on disk. `modules/` and
    `sql_scripts/clones/` are different directories, so the two answers are
    genuinely different.

    Not a collision the shipped catalog has today -- nothing enforces that an id
    is unique across families, and nothing has ever needed it to be, which is
    exactly why this went unnoticed.

    Mutation: key `installed_ids` by `manifest.id` and both rows read installed.
    """
    rows = _rows(
        [_m("twin"), _m("twin", "mod")],
        {"module": frozenset({"twin"}), "mod": frozenset()},
    )

    assert [(r.family, r.installed) for r in rows] == [("module", True), ("mod", False)]


def test_both_families_of_a_shared_id_contribute_their_own_requires() -> None:
    """The dependency graph must not lose a manifest to a name clash.

    Round 2, Codex: the graph was built from `installed_ids.values()`, a dict
    keyed by id, so of two installed manifests sharing an id only the
    last-loaded one contributed its `requires` -- and a base module the other
    one depends on came back `removable=True`.

    `Manifest.requires` names an id and never a family, so a required id is
    matched by id here too. That is the schema's own precision, not a shortcut:
    both rows of a shared id are therefore held by anything that requires it,
    which is the conservative direction.

    Mutation: iterate `installed_ids.values()` (id-keyed) instead of the list of
    installed manifests and `mod-base` is removable again.
    """
    base = _m("mod-base", name="Base")
    twin_module = _m("twin", name="Twin Module", requires=("mod-base",))
    twin_mod = _m("twin", "mod", name="Twin Mod")
    rows = _rows(
        [base, twin_module, twin_mod],
        {"module": frozenset({"mod-base", "twin"}), "mod": frozenset({"twin"})},
    )

    row = _row(rows, "mod-base")
    assert row.removable is False
    assert row.remove_reason is not None and "Twin Module" in row.remove_reason


# --------------------------------------------- round 2: the owed chips and reality


def test_the_sql_chip_is_absent_when_the_report_listed_no_files() -> None:
    """An empty value is "this module owes nothing", not "owes something unnamed".

    `_pending_sql_names()` only ever stores a non-empty tuple, so an empty one
    can reach here only as stale state -- and a chip a press cannot explain is
    worse than no chip.

    Mutation: `if item_id in session.sql_owed:` instead of reading the value and
    the empty entry grows a chip whose detail names nothing.
    """
    session = mp.SessionState(sql_owed={"mod-a": ()})
    rows = _rows([_m("mod-a")], {"module": frozenset({"mod-a"})}, session)

    assert _labels(_row(rows, "mod-a")) == []


def test_an_id_in_two_families_gets_two_addressable_rows(qapp: object) -> None:
    """Round 2, Codex: `_rows` was id-keyed, so the second family overwrote the first.

    Both widgets were still drawn, so the user saw two rows and the panel knew
    one: `rows()` lost one, `row(id)` answered with the last built, and
    `select(id)` highlighted the wrong one.

    Mutation: key `_rows` by `data.id` and `rows()` comes back with one entry.
    """
    handed = _rows(
        [_m("twin"), _m("twin", "mod")],
        {"module": frozenset({"twin"}), "mod": frozenset()},
    )
    panel = _panel(handed)

    assert [(r.data.family, r.data.installed) for r in panel.rows()] == [
        ("module", True),
        ("mod", False),
    ]
    # The bare-id lookups the public shape is built on resolve in `FAMILY_FILES`
    # order, which is the module family here.
    assert panel.row("twin").data.family == "module"


def test_clicking_the_second_family_of_a_shared_id_selects_that_row(qapp: object) -> None:
    """A click selects the row that was CLICKED, not whatever `select(id)` resolves to.

    Round 2: selection went through an id lookup, so clicking the `mod` row
    highlighted the `module` row of the same id. The click now carries the
    widget, so the resolution rule is only ever used by callers that have
    nothing but an id.

    Mutation: connect `RowWidget.clicked` to `self.select` (the id route) and
    the `mod` row cannot be selected at all.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    handed = _rows([_m("twin"), _m("twin", "mod")], {"module": frozenset({"twin"})})
    panel = _panel(handed)
    panel.resize(600, 800)
    panel.show()
    second = [r for r in panel.rows() if r.data.family == "mod"][0]

    QTest.mouseClick(second.description_label, Qt.MouseButton.LeftButton)

    assert panel.selected_row() is second
    assert panel.selected_id() == "twin"
    panel.hide()


def test_a_chip_press_selects_its_row_too(qapp: object) -> None:
    """Round 2: chips consumed their own clicks, so a chip press left the row unselected.

    A chip is about one row, so pressing it is a statement about that row, the
    same way the Install and Remove presses are (`_row_install` selects first).
    The GitHub link is deliberately NOT routed here: it opens a browser and
    belongs to nothing on this tab.

    Mutation: drop the `select` from the chip's handler and `selected_id()`
    stays `None` after the press.
    """
    session = mp.SessionState(rebuild_owed=frozenset({"mod-a"}))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))

    panel.row("mod-a").chip_buttons[0].click()

    assert panel.selected_id() == "mod-a"


def test_the_cards_draw_the_installed_half_above_the_available_half(qapp: object) -> None:
    """Read off the LAYOUTS, which is the only place the drawn order really is.

    Round 2, Codex: two tests claimed "drawn order" while reading `rows()`,
    which is `set_rows()`'s insertion order. The split into an installed box and
    an available box is `_FamilyCard.fill()`'s, and this is what asserts it.

    The rows are handed over in an order the builder would never produce
    (available first), because when they arrive already sorted the two
    guarantees are indistinguishable -- `card.fill(widgets, [])` was seen to
    leave this green while the builder's own sort was carrying it.

    Mutation: `card.fill()` puts into the installed box everything it is handed,
    or the two boxes are added to the card in the other order.
    """
    handed = (
        mp.ModuleRow("a", "module", "A", "", None, False, True, (), (), True, None),
        mp.ModuleRow("b", "module", "B", "", None, True, True, (), (), True, None),
    )
    panel = _panel(handed)

    assert [r.data.id for r in panel.rows()] == ["a", "b"], "handed order"
    assert [r.data.id for r in panel.drawn_rows()] == ["b", "a"], "drawn order"


def test_a_family_that_disappears_and_comes_back_keeps_its_toggle(qapp: object) -> None:
    """A reload that finds no rows for a family must not forget what the user opened.

    A family can vanish from a reload -- a store that fails to load one, a game
    whose catalog gains one later -- and the open/closed state is the user's.

    Mutation: clear `self._open` in `set_rows()` and the reopened section snaps
    shut when the family comes back.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))
    panel.available_toggle("module").click()
    assert panel.available_open("module") is True

    panel.set_rows(_rows([_m("a1", "ale")]))
    assert panel.available_toggle("module") is None, "the family is gone"

    panel.set_rows(_catalog_rows({"module": frozenset({"mod-a"})}))
    assert panel.available_open("module") is True


def test_a_family_that_gains_its_first_installed_row_stays_open(qapp: object) -> None:
    """The collapse rule is applied ONCE per family, which is the ticket's own wording.

    An install is the moment a user is reading that card, and shutting the
    section they are looking at because the count changed would be the tab
    moving under them.

    Mutation: recompute the rule whenever the family's installed count changes
    and the card collapses on the press that made it non-empty.
    """
    panel = _panel(_catalog_rows({"module": frozenset()}))
    assert panel.available_open("module") is True

    panel.set_rows(_catalog_rows({"module": frozenset({"mod-a"})}))

    assert panel.available_open("module") is True
