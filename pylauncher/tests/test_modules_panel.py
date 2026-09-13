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


# ------------------------------------------------------------- section hints (T44)


def test_every_family_has_a_hint_that_says_what_that_family_costs() -> None:
    """The mockup's copy beside `Installed (N)`, per family (T44 item 3).

    Not decoration: the four families differ in exactly one thing a user has to
    know before pressing Install -- whether the change reaches the running
    server by itself. A C++ module does not (it is compiled in), an ALE script
    does at the next restart, a mod is SQL and conf, a keg also touches the
    game client. The assertion is on that word, not on the prose around it.

    Mutation: drop the `keg` key and `set(FAMILY_HINTS) == set(FAMILY_FILES)`
    fails; drop "compil" from the `module` hint and the one family that owes a
    rebuild stops saying so.
    """
    assert set(mp.FAMILY_HINTS) == set(mp.FAMILY_FILES)
    assert "compil" in mp.FAMILY_HINTS["module"]
    assert "no rebuild" in mp.FAMILY_HINTS["ale"]
    assert "client" in mp.FAMILY_HINTS["keg"]
    assert "SQL" in mp.FAMILY_HINTS["mod"]


def test_a_family_card_draws_its_hint_beside_the_installed_header(qapp: object) -> None:
    """A mapping nothing renders is a mapping that is not on the tab.

    Mutation: build the label and never add it to the card's layout ->
    `family_hint()` answers `None`. The first version of this test asserted
    `isVisibleTo()` alone and SURVIVED that mutation (measured): a QLabel
    parented to the card but laid out nowhere is still visible-to it and still
    carries its text, so the reading has to come off the layout.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))

    hint = panel.family_hint("module")
    assert hint is not None
    assert hint.text() == mp.FAMILY_HINTS["module"]
    assert hint.isVisibleTo(panel)


# ------------------------------------------------------------------ badges (T44)


def test_an_installed_module_whose_sql_is_waiting_says_so_in_its_badge() -> None:
    """`Cloned, SQL not applied` is a real state, and `Installed` hides it (T44 item 5).

    It is the state T43's probe produced on a real install: the folder is there,
    the worldserver will compile it, and the rows it needs are NOT in the
    database. A badge reading `Installed` over that is the reading T41 was
    reported for, one step further along.

    Mutation: drop the `sql_owed` clause from `_badge_for()` and the row goes
    back to `Installed` while the SQL chip beside it still says otherwise.
    """
    session = mp.SessionState(sql_owed={"mod-a": ("a.sql",)})
    rows = _rows([_m("mod-a"), _m("mod-b")], {"module": frozenset({"mod-a", "mod-b"})}, session)

    assert _row(rows, "mod-a").badge == mp.BADGE_SQL_NOT_APPLIED
    assert _row(rows, "mod-b").badge == mp.BADGE_INSTALLED


def test_a_manifest_for_another_game_is_drawn_greyed_and_offers_no_install() -> None:
    """A row this install cannot use is SHOWN, and shown as unusable (T44 item 5).

    `Not installed` on it would be an invitation: the button beside it would
    clone somebody else's game's module into this server directory.

    Mutation: ignore the `game` argument and the row comes back `Not installed`
    with `for_this_game` True, which is what puts an Install button on it.
    """
    mine = _m("mod-a")
    theirs = Manifest(
        id="mod-tbc",
        name="Mod Tbc",
        type="module",
        game="wow-tbc",
        description="another game's module",
        source=Source(repo="acme/mod-tbc"),
    )
    rows = mp.build_module_rows([mine, theirs], {}, mp.SessionState(), None, game="wow-wotlk")

    assert _row(rows, "mod-tbc").badge == mp.BADGE_NOT_FOR_THIS_GAME
    assert _row(rows, "mod-tbc").for_this_game is False
    assert _row(rows, "mod-a").for_this_game is True


def test_no_game_given_means_every_manifest_belongs_here() -> None:
    """The argument is optional, and absent must never mean "none of them fit".

    Every existing caller and every T42 test builds rows without it, and a
    default that answered `Not for this game` would grey the whole catalog.

    Mutation: default `game` to `""` and compare with `==` and every row in
    this file's other 40-odd tests turns grey.
    """
    rows = _rows([_m("mod-a")])

    assert _row(rows, "mod-a").for_this_game is True
    assert _row(rows, "mod-a").badge == mp.BADGE_NOT_INSTALLED


def test_an_uncatalogued_clone_is_installed_and_belongs_here() -> None:
    """T41's own rows have no manifest, so they have no `game` to disagree with.

    Mutation: build them with `for_this_game=False` and a module the user
    cloned by hand is greyed as another game's.
    """
    rows = mp.build_module_rows(
        [], {"module": frozenset({"mod-hand"})}, mp.SessionState(), None, game="wow-wotlk"
    )

    assert _row(rows, "mod-hand").for_this_game is True
    assert _row(rows, "mod-hand").badge == mp.BADGE_INSTALLED


def test_the_row_widget_draws_the_badge_the_builder_decided(qapp: object) -> None:
    """The widget renders the decision; it does not take it (T42's split).

    Mutation: go back to `BADGE_INSTALLED if data.installed else
    BADGE_NOT_INSTALLED` in `RowWidget` and both new badges vanish from the
    screen while the builder's tests stay green.
    """
    session = mp.SessionState(sql_owed={"mod-a": ("a.sql",)})
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))

    assert panel.row("mod-a").badge_label.text() == mp.BADGE_SQL_NOT_APPLIED


def test_a_row_for_another_game_gets_no_install_button(qapp: object) -> None:
    """The greying is not cosmetic: there must be nothing to press.

    Mutation: keep `if data.catalogued and not data.installed` alone and the
    row offers an Install that clones another game's module into this server.
    """
    theirs = Manifest(
        id="mod-tbc",
        name="Mod Tbc",
        type="module",
        game="wow-tbc",
        description="another game's module",
        source=Source(repo="acme/mod-tbc"),
    )
    rows = mp.build_module_rows(
        [_m("mod-a"), theirs], {}, mp.SessionState(), None, game="wow-wotlk"
    )
    panel = _panel(rows)

    assert panel.row("mod-a").install_button is not None
    assert panel.row("mod-tbc").install_button is None
    assert panel.row("mod-tbc").remove_button is None


# ---------------------------------------------------------- the subpanel (T44)


def test_each_owed_chip_names_the_action_that_answers_it_and_a_fact_names_none() -> None:
    """An owed chip is a job, and the job has one button (T44 item 4).

    The action is a KEY and not a label: the view routes on it, and a button
    whose text the tab reworded would otherwise stop reaching its slot.

    Mutation: give the `asks a question` chip an action and a row offers a
    button for something no press can change.
    """
    asks = _m(
        "mod-ask",
        prompts=(Prompt(key="g", question="Which GUID?"),),
        patches=(Patch(file="x.conf", find="a", replace="{g}"),),
    )
    session = mp.SessionState(
        rebuild_owed=frozenset({"mod-a"}),
        sql_owed={"mod-a": ("a.sql",)},
    )
    rows = _rows([_m("mod-a"), asks], {"module": frozenset({"mod-a"})}, session)

    owed = {c.label: c.action for c in _row(rows, "mod-a").chips if c.kind == "owed"}
    assert owed[mp.CHIP_REBUILD_PENDING] == "rebuild"
    assert owed[mp.CHIP_SQL_PENDING] == "sql"
    assert all(c.action is None for c in _row(rows, "mod-ask").chips if c.kind == "fact")


def test_every_chip_action_has_a_button_label() -> None:
    """A key with no label is a button that renders empty. Mutation: drop one key."""
    assert set(mp.CHIP_ACTION_LABELS) == {"rebuild", "sql", "update"}
    assert mp.CHIP_ACTION_LABELS["sql"] == "Apply module SQL"


def test_an_owed_chip_press_opens_a_subpanel_under_its_own_row(qapp: object) -> None:
    """The chip's sentence, where the row is, with the button that answers it.

    Mutation: keep only the report line and `detail_visible()` stays False --
    which is the tab as T42 shipped it.
    """
    session = mp.SessionState(rebuild_owed=frozenset({"mod-a"}))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))
    row = panel.row("mod-a")
    row.show()

    assert row.detail_visible() is False
    row.chip_buttons[0].click()

    assert row.detail_visible() is True
    assert row.detail_label.text() == row.data.chips[0].detail
    assert row.detail_button is not None
    assert row.detail_button.text() == mp.CHIP_ACTION_LABELS["rebuild"]
    row.hide()


def test_a_second_press_on_the_same_chip_closes_the_subpanel(qapp: object) -> None:
    """A row that can only ever grow is a row that eats the card.

    Mutation: always `setVisible(True)` and the subpanel never shuts again.
    """
    session = mp.SessionState(rebuild_owed=frozenset({"mod-a"}))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))
    row = panel.row("mod-a")
    row.show()

    row.chip_buttons[0].click()
    assert row.detail_visible() is True
    row.chip_buttons[0].click()
    assert row.detail_visible() is False
    row.hide()


def test_a_chip_press_still_writes_the_report_line(qapp: object) -> None:
    """T44 keeps it: the report is what a user copies into a bug report.

    Mutation: replace the `pressed_chip` emit with the expansion and the one
    copyable surface on this tab goes silent.
    """
    session = mp.SessionState(rebuild_owed=frozenset({"mod-a"}))
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))
    pressed: list[tuple[str, str]] = []
    panel.chip_pressed.connect(lambda mid, label: pressed.append((mid, label)))

    panel.row("mod-a").chip_buttons[0].click()

    assert pressed == [("mod-a", mp.CHIP_REBUILD_PENDING)]


def test_the_subpanels_button_emits_the_rows_id_and_the_action_key(qapp: object) -> None:
    """The press the subpanel exists for, carried as (id, key) for the view to route.

    Mutation: emit the button's TEXT instead of the key and the view's mapping
    misses every action the day one of these labels is reworded.
    """
    session = mp.SessionState(sql_owed={"mod-a": ("a.sql",)})
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}, session=session))
    acted: list[tuple[str, str]] = []
    panel.chip_action_pressed.connect(lambda mid, key: acted.append((mid, key)))
    row = panel.row("mod-a")

    row.chip_buttons[0].click()
    assert row.detail_button is not None
    row.detail_button.click()

    assert acted == [("mod-a", "sql")]


def test_a_fact_chip_opens_nothing(qapp: object) -> None:
    """A fact chip is not a press, and T44 does not make it one.

    Mutation: expand on every chip and `needs the client folder` opens a
    subpanel with no button in it.
    """
    needs = _m("mod-client", client=(ClientFile(src="a", dest="addons"),))
    panel = _panel(_catalog_rows(catalog=[needs]))
    row = panel.row("mod-client")
    row.show()

    assert [c.text() for c in row.chip_buttons] == [mp.CHIP_NEEDS_CLIENT_FOLDER]
    row.chip_buttons[0].click()

    assert row.detail_visible() is False
    row.hide()


# ------------------------------------------------------- the version line (T44)


class _Reader:
    """A `head_version` seam that counts how many times each path was asked."""

    def __init__(self, answers: dict[str, str | None] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[Path] = []

    def __call__(self, path: Path) -> str | None:
        self.asked.append(path)
        return self.answers.get(path.name)


def test_a_version_is_read_once_and_then_remembered() -> None:
    """The whole point of item 1: a `git log` per row per reload is what T41 refused.

    Mutation: drop the `_known` write in `fill()` and the reader is asked again
    on every paint, which is the cost this class exists to remove.
    """
    reader = _Reader({"mod-a": "7c02b1d · 2026-09-01"})
    cache = mp.VersionCache(reader)
    root = Path("/srv")

    assert cache.fill(root, "module", "mod-a") == "7c02b1d · 2026-09-01"
    assert cache.fill(root, "module", "mod-a") == "7c02b1d · 2026-09-01"

    assert reader.asked == [root / "modules" / "mod-a"]


def test_a_clone_that_cannot_say_is_remembered_as_not_saying() -> None:
    """`None` is cached too, or every paint retries a subprocess for every such row.

    A `modules/` folder also holds `CMakeLists.txt` and friends, and a user can
    copy a module in with no `.git` in it (`apply.ModuleUpdate.is_checkout`).
    Those rows answer `None` forever and must cost one read, not one per paint.

    Mutation: cache only truthy answers and the reader is asked twice.
    """
    reader = _Reader({})
    cache = mp.VersionCache(reader)
    root = Path("/srv")

    assert cache.fill(root, "module", "mod-hand") is None
    assert cache.fill(root, "module", "mod-hand") is None

    assert len(reader.asked) == 1
    assert cache.has(root, "module", "mod-hand") is True, "read-and-said-nothing is not unread"


def test_known_never_reads_so_the_first_paint_cannot_block() -> None:
    """The rule the builder is called under: draw what is known, read nothing.

    Mutation: make `known()` call `fill()` and `build_module_rows()` pays a
    subprocess per installed row on every reload -- the exact thing item 1
    forbids, and it would still be GREEN on every other test in this file.
    """
    reader = _Reader({"mod-a": "7c02b1d · 2026-09-01"})
    cache = mp.VersionCache(reader)
    root = Path("/srv")

    assert cache.known(root, "module", "mod-a") is None
    assert reader.asked == []

    cache.fill(root, "module", "mod-a")
    assert cache.known(root, "module", "mod-a") == "7c02b1d · 2026-09-01"


def test_forget_drops_one_module_in_every_family_and_clear_drops_all() -> None:
    """Invalidation: an install, a remove or an update changes exactly one clone.

    `forget()` matches by BARE ID across every family, because that is what an
    `ApplyReport.item_id` is -- there is no family on it (T42 round 2's rule,
    and `_forget_what_is_no_longer_installed()`'s).

    Mutation: make `forget()` a no-op and a module updated in place goes on
    showing the sha it had before the pull, which is a wrong sha rather than
    no sha.
    """
    reader = _Reader({"bmah": "aaaaaaa · 2026-01-01"})
    cache = mp.VersionCache(reader)
    root = Path("/srv")
    cache.fill(root, "module", "bmah")
    cache.fill(root, "keg", "bmah")
    cache.fill(root, "module", "mod-a")
    asked = len(reader.asked)

    cache.forget("bmah")

    assert cache.has(root, "module", "bmah") is False
    assert cache.has(root, "keg", "bmah") is False
    assert cache.has(root, "module", "mod-a") is True, "a neighbour's entry is not dropped"
    cache.fill(root, "module", "bmah")
    assert len(reader.asked) == asked + 1

    cache.clear()
    assert cache.has(root, "module", "bmah") is False
    assert cache.has(root, "module", "mod-a") is False


def test_two_families_that_share_an_id_are_two_clones_and_two_entries() -> None:
    """T42 round 2's collision, one surface further along.

    `ale` and `keg` share `ale_scripts/`, but `module` and `keg` do not: a
    `bmah` module and a `bmah` keg are different folders with different shas,
    and one cache entry would show one folder's sha on the other's row.

    Mutation: key on `(server_dir, item_id)` and the keg row reads the
    module's sha.
    """
    reader = _Reader({"bmah": "aaaaaaa · 2026-01-01"})
    cache = mp.VersionCache(reader)
    root = Path("/srv")

    cache.fill(root, "module", "bmah")
    cache.fill(root, "keg", "bmah")

    assert reader.asked == [root / "modules" / "bmah", root / "ale_scripts" / "bmah"]


def test_a_different_server_dir_is_a_different_entry() -> None:
    """The key the ticket names, and it is not decoration: two installs, two shas.

    Mutation: drop `server_dir` from the key and switching installs shows the
    other one's version until something invalidates the cache.
    """
    reader = _Reader({"mod-a": "7c02b1d · 2026-09-01"})
    cache = mp.VersionCache(reader)

    cache.fill(Path("/srv/one"), "module", "mod-a")

    assert cache.known(Path("/srv/two"), "module", "mod-a") is None


def test_the_builder_carries_the_version_it_was_handed_and_nothing_else() -> None:
    """Only INSTALLED rows, and only what is already known (item 1's two rules).

    Mutation: call the version lookup for every row and a catalog of 41 rows
    reads 41 clone folders, 20 of which are not there.
    """
    rows = mp.build_module_rows(
        [_m("mod-a"), _m("mod-b")],
        {"module": frozenset({"mod-a"})},
        mp.SessionState(),
        None,
        # `mod-b` is NOT installed and a version is offered for it anyway --
        # which is the shape a stale cache entry has after a remove. An
        # uninstalled row has no clone, so it must show nothing.
        versions={
            ("module", "mod-a"): "7c02b1d · 2026-09-01",
            ("module", "mod-b"): "ffffff0 · 2020-01-01",
        },
    )

    assert _row(rows, "mod-a").version == "7c02b1d · 2026-09-01"
    assert _row(rows, "mod-b").version is None, "an uninstalled row has no clone to be AT"


def test_a_row_with_no_version_yet_shows_nothing_where_the_version_goes(qapp: object) -> None:
    """A row that renders late is fine; a placeholder that is not a sha is not.

    Mutation: show `"—"` (or the id, or "unknown") and a user reads it as
    something git said.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))

    assert panel.row("mod-a").version_label.text() == ""


def test_set_version_fills_the_row_in_place_without_rebuilding_it(qapp: object) -> None:
    """The late fill must not be a reload: a reload loses the selection and the toggles.

    Mutation: have `set_version()` call `set_rows()` again and the identity
    assertion fails -- which is the version line taking the user's place in the
    list away from them once per module.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))
    before = panel.row("mod-a")

    panel.set_version("module", "mod-a", "7c02b1d · 2026-09-01")

    assert panel.row("mod-a") is before
    assert before.version_label.text() == "7c02b1d · 2026-09-01"


def test_set_version_on_a_row_that_is_gone_is_ignored(qapp: object) -> None:
    """The fill is asynchronous, so a row can be removed between the read and the write.

    Mutation: index `self._rows[key]` directly and a remove during a fill
    raises a KeyError out of a timer callback.
    """
    panel = _panel(_catalog_rows({"module": frozenset({"mod-a"})}))

    panel.set_version("module", "mod-gone", "7c02b1d · 2026-09-01")  # must not raise
