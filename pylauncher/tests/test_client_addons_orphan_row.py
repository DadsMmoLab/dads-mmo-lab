"""T613 PR-3: a recorded add-on whose copy Yu'lon no longer holds offers no Install."""

from __future__ import annotations

from yulon.manifest import Manifest, parse_manifest
from yulon.ui.widgets import modules_panel


def _recorded(kind: str, **origin: object) -> Manifest:
    return parse_manifest(
        {
            "id": "pfui",
            "name": "pfUI",
            "type": "mod",
            "game": "wow-wotlk",
            "description": "Client add-on.",
            "source": (
                {"repo": "https://github.com/shagu/pfUI", "follow": "branch"}
                if kind == "link"
                else None
            ),
            "origin": {"kind": kind, "added": "2026-10-09", "addon": True, **origin},
            "build": {"rebuild": False, "restart": False},
            "client": [{"src": ".", "dest": "addons", "name": "pfUI"}],
        }
    )


def _row(manifest: Manifest, *, here: bool) -> modules_panel.ModuleRow:
    installed = {"mod": frozenset({"pfui"})} if here else {}
    rows = modules_panel.build_module_rows(
        [manifest], installed, modules_panel.SessionState(), None
    )
    return next(r for r in rows if r.id == "pfui")


def test_a_folder_add_on_whose_copy_is_gone_offers_no_install_and_says_why() -> None:
    row = _row(_recorded("folder", path="/home/p/Downloads/pfUI"), here=False)
    assert row.installable is False
    assert row.install_reason is not None
    assert "/home/p/Downloads/pfUI" in row.install_reason
    assert "Game add-ons you bring" in row.install_reason and "Remove" in row.install_reason


def test_a_zip_add_on_whose_copy_is_gone_says_where_it_came_from() -> None:
    row = _row(
        _recorded("archive", url="https://github.com/x/y/archive/master.zip", sha256="a" * 64),
        here=False,
    )
    assert row.installable is False
    assert "https://github.com/x/y/archive/master.zip" in (row.install_reason or "")


def test_a_link_add_on_can_be_cloned_again_so_its_install_stays_open() -> None:
    row = _row(_recorded("link"), here=False)
    assert row.installable is True and row.install_reason is None


def test_an_installed_folder_add_on_is_not_locked() -> None:
    row = _row(_recorded("folder", path="/x/pfUI"), here=True)
    assert row.installable is True


def test_a_folder_module_that_is_not_an_add_on_record_is_left_to_the_old_rules() -> None:
    manifest = parse_manifest(
        {
            "id": "pfui",
            "name": "pfUI",
            "type": "mod",
            "game": "wow-tortoise",
            "description": "A custom module.",
            "origin": {"kind": "folder", "path": "/x/mod", "added": "2026-10-09"},
            "build": {"rebuild": False, "restart": False},
            "client": [{"src": ".", "dest": "addons", "name": "pfUI"}],
        }
    )
    assert _row(manifest, here=False).installable is True
