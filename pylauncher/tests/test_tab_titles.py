"""Tests for the controller tab titles.

The defect was a tab strip that could not be read: `server_dir.name` was the
whole title and the installer suggests the same leaf folder to everyone, so two
installs on two disks looked identical. These pin both halves of the fix - that
colliding paths become distinguishable, and that a path which needs no help is
not turned into a path dump to get there.
"""

from __future__ import annotations

import os
from pathlib import Path

from yulon.ui.tab_titles import controller_tab_titles, folder_labels


def test_a_lone_install_is_still_just_its_folder() -> None:
    """Disambiguation is paid for only when there is something to disambiguate."""
    assert folder_labels([Path("/home/dad/servers/DadsMmoLab")]) == ["DadsMmoLab"]


def test_two_installs_with_the_same_leaf_name_are_told_apart_by_their_parents() -> None:
    labels = folder_labels([Path("/mnt/ssd/DadsMmoLab"), Path("/mnt/hdd/DadsMmoLab")])
    assert labels == [os.sep.join(("ssd", "DadsMmoLab")), os.sep.join(("hdd", "DadsMmoLab"))]


def test_only_the_colliding_pair_grows() -> None:
    """A third install that already reads uniquely keeps its one-folder title."""
    labels = folder_labels(
        [Path("/mnt/ssd/DadsMmoLab"), Path("/mnt/hdd/DadsMmoLab"), Path("/mnt/ssd/tortoise")]
    )
    assert labels[2] == "tortoise"


def test_paths_that_agree_two_deep_go_a_third() -> None:
    """The parent repeats as readily as the leaf; the rule is "shortest tail that differs"."""
    labels = folder_labels([Path("/a/games/DadsMmoLab"), Path("/b/games/DadsMmoLab")])
    assert labels[0] != labels[1]
    assert labels[0].endswith(os.sep.join(("a", "games", "DadsMmoLab")))


def test_a_label_never_grows_past_the_path_it_describes() -> None:
    """One path being a tail of the other exhausts the shorter one, which then spells itself out.

    Without the length guard this is the loop that does not terminate.
    """
    labels = folder_labels([Path("/games/DadsMmoLab"), Path("/mnt/d/games/DadsMmoLab")])
    assert labels[0] == str(Path("/games/DadsMmoLab"))
    assert labels[0] != labels[1]


def test_the_game_name_still_leads_the_title() -> None:
    titles = controller_tab_titles(
        [("WoW WotLK", Path("/mnt/ssd/DadsMmoLab")), ("WoW WotLK", Path("/mnt/hdd/DadsMmoLab"))]
    )
    assert titles[0].startswith("WoW WotLK — ")
    assert titles[0] != titles[1]


def test_one_folder_two_games_is_not_a_collision() -> None:
    """Tabs are keyed by (game, dir), so the same folder can legitimately carry two.

    The folder label is identical for both, and it is the game name that has to
    do the telling apart - which it does, so nothing should be widened here.
    """
    titles = controller_tab_titles(
        [("WoW WotLK", Path("/srv/DadsMmoLab")), ("WoW TBC", Path("/srv/DadsMmoLab"))]
    )
    assert titles == ["WoW WotLK — DadsMmoLab", "WoW TBC — DadsMmoLab"]


# -- T192: the sidebar's own titles -- the short game name, a folder only when needed --


def test_a_short_game_name_drops_the_shared_prefix_and_nothing_else() -> None:
    from yulon.ui.sidebar import short_game_name

    assert [
        short_game_name(n)
        for n in ("WoW WotLK", "WoW TBC", "WoW Vanilla", "WoW Tortoise", "Centurion")
    ] == ["WotLK", "TBC", "Vanilla", "Tortoise", "Centurion"]


def test_one_server_is_titled_with_its_short_game_name_alone() -> None:
    """A3/B5/C9: "WoW WotLK — DadsMmoLab" was 235px on a 64px rail and drew as "WoW…"."""
    from yulon.ui.tab_titles import sidebar_titles

    assert sidebar_titles([("WoW WotLK", Path("/srv/DadsMmoLab"))]) == ["WotLK"]


def test_two_games_in_one_folder_need_no_folder_to_tell_them_apart() -> None:
    from yulon.ui.tab_titles import sidebar_titles

    titles = sidebar_titles(
        [("WoW WotLK", Path("/srv/DadsMmoLab")), ("WoW TBC", Path("/srv/DadsMmoLab"))]
    )
    assert titles == ["WotLK", "TBC"]


def test_two_servers_of_one_game_carry_their_folders_on_a_second_line() -> None:
    """Only the pair that shares a game grows, and only by the folders that differ."""
    from yulon.ui.tab_titles import sidebar_titles

    titles = sidebar_titles(
        [
            ("WoW WotLK", Path("/mnt/on-the-ssd/DadsMmoLab")),
            ("WoW TBC", Path("/mnt/on-the-ssd/DadsMmoLab")),
            ("WoW WotLK", Path("/mnt/on-the-spinner/DadsMmoLab")),
        ]
    )
    first, tbc, second = titles
    assert first.startswith("WotLK\n") and "on-the-ssd" in first, first
    assert second.startswith("WotLK\n") and "on-the-spinner" in second, second
    assert tbc == "TBC"
    assert "/mnt" not in first and "/mnt" not in second, "the shared tail is not repeated"


def test_an_ampersand_in_a_folder_is_shown_never_read_as_a_shortcut(qapp: object) -> None:
    """T188 B7, kept on the new titles: they are tab text, so "&" arrives escaped."""
    from PySide6.QtGui import QKeySequence

    from yulon.ui.tab_titles import sidebar_titles

    titles = sidebar_titles(
        [
            ("WoW WotLK", Path("/srv/Raids & Dungeons")),
            ("WoW WotLK", Path("/srv/Quests & Loot")),
        ]
    )
    for title in titles:
        assert QKeySequence.mnemonic(title).isEmpty(), title
    assert "Raids & Dungeons" in titles[0].replace("&&", "&")
