"""T825: the Server tab's "Update client" beside Play (`yulon.ui.client_update`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_coa_client import HOSTS, OBJECTS, _client, _site
from yulon import coa_client
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.ui import client_update
from yulon.ui.client_update import Choice, ClientUpdateControl
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()


def _coa(manifest_url: str) -> CatalogEntry:
    entry = CATALOG.get("wow-coa")
    update = entry.client.update
    assert update is not None
    client = entry.client.model_copy(
        update={"update": update.model_copy(update={"manifest_url": manifest_url})}
    )
    return entry.model_copy(update={"client": client})


def _control(
    tmp_path: Path,
    site: Any,
    files: dict[str, bytes],
    *,
    answer: Choice = Choice.KEEP,
    client: Path | None = None,
) -> tuple[ClientUpdateControl, Path, list[tuple[str, bool]]]:
    from tests.test_coa_client import MANIFEST_URL

    entry = _coa(MANIFEST_URL)
    entry = entry.model_copy(
        update={
            "client": entry.client.model_copy(
                update={
                    "update": entry.client.update.model_copy(  # type: ignore[union-attr]
                        update={"objects_url": OBJECTS}
                    )
                }
            )
        }
    )
    assert entry.client.hosts() == HOSTS
    if client is None:
        client = _client(tmp_path, files)
    asked: list[tuple[str, bool]] = []

    def ask(_parent: object, _title: str, text: str, has_edited: bool) -> Choice:
        asked.append((text, has_edited))
        return answer

    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    control = ClientUpdateControl(
        entry, tmp_path / "server", lambda: client, run_inline, parent, ask=ask, opener=site
    )
    control._keep_parent = parent  # type: ignore[attr-defined]
    return control, client, asked


def test_the_check_says_unknown_for_a_folder_yulon_never_updated(
    qapp: object, tmp_path: Path
) -> None:
    control, _client_dir, _ = _control(tmp_path, _site({"a.exe": b"x"}), {"a.exe": b"x"})
    control.check()
    assert "Client version unknown" in control.label.text()
    assert control.button.isEnabled()


def test_a_failed_check_is_a_sentence_never_a_crash(qapp: object, tmp_path: Path) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise OSError("no route to host")

    control, _client_dir, _ = _control(tmp_path, broken, {"a.exe": b"x"})
    control.check()
    assert control.label.text() == coa_client.CHECK_FAILED
    assert control.button.isEnabled()


def test_update_asks_with_the_summary_then_brings_the_client_up_to_date(
    qapp: object, tmp_path: Path
) -> None:
    site = _site({"a.exe": b"new", "Data/b.MPQ": b"same"})
    control, client, asked = _control(tmp_path, site, {"a.exe": b"old", "Data/b.MPQ": b"same"})
    control.update_pressed()
    assert len(asked) == 1
    assert asked[0][0].startswith("1 file to download")
    assert asked[0][1] is False
    assert (client / "a.exe").read_bytes() == b"new"
    assert "up to date" in control.label.text()
    assert not control.running and control.button.isEnabled()
    control.check()
    assert "up to date" in control.label.text()


def test_cancel_in_the_question_changes_nothing(qapp: object, tmp_path: Path) -> None:
    site = _site({"a.exe": b"new"})
    control, client, _ = _control(tmp_path, site, {"a.exe": b"old"}, answer=Choice.CANCEL)
    control.update_pressed()
    assert (client / "a.exe").read_bytes() == b"old"
    assert not (client / coa_client.STATE_FILE).exists()
    assert "nothing was changed" in control.label.text()


def test_edited_files_offer_replace_and_replace_replaces_them(qapp: object, tmp_path: Path) -> None:
    files = {"Data/b.MPQ": b"v1"}
    control, client, asked = _control(tmp_path, _site(files, "v1"), files)
    control.update_pressed()  # nothing to download: records v1 without asking
    assert asked == []
    (client / "Data/b.MPQ").write_bytes(b"mine")
    control2, _same, asked2 = _control(
        tmp_path, _site({"Data/b.MPQ": b"v2"}, "v2"), {}, answer=Choice.REPLACE, client=client
    )
    control2.update_pressed()
    assert asked2 and asked2[0][1] is True and "you changed" in asked2[0][0]
    assert (client / "Data/b.MPQ").read_bytes() == b"v2"


def test_the_map_data_sentence_follows_a_client_update(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coa_client, "map_data_sentence", lambda *_a: "Extract it again, please.")
    control, _client_dir, _ = _control(tmp_path, _site({"a.exe": b"new"}), {"a.exe": b"old"})
    control.update_pressed()
    assert control.label.text().endswith("Extract it again, please.")


def test_the_unpatched_question_cancels(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest answers every unpatched dialog with No: that must read as Cancel."""
    from PySide6.QtWidgets import QWidget

    parent = QWidget()
    assert client_update.ask_update(parent, "t", "x", True) is Choice.CANCEL


@pytest.mark.parametrize("game", ["wow-coa", "wow-wotlk"])
def test_update_client_sits_beside_play_only_for_an_entry_that_publishes_its_client(
    game: str, qapp: object, tmp_path: Path
) -> None:
    from yulon.ui.controller_view import ControllerServices, ControllerView

    client = tmp_path / "client"
    (client / "Data").mkdir(parents=True)
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / game, client)
    services.set_play_client_dir = lambda _p: None
    view = ControllerView(CATALOG.get(game), services, status_poll_ms=0, job_runner=run_inline)
    if game == "wow-coa":
        assert view.client_update is not None
        assert view.client_update.button.text() == client_update.UPDATE_CLIENT_LABEL
        # The check at open ran (the conftest refuses the real site): a sentence, no crash.
        assert view.client_update.label.text() == coa_client.CHECK_FAILED
        assert view.play_button is not None and view.play_button.isEnabled()
    else:
        assert view.client_update is None
