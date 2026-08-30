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
