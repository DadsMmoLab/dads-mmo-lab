"""T835 W5: pin the Conquest of Azeroth client side for Windows (tests only).

The client side was written Windows-safe already; these tests pin that so a
later change cannot break it unseen: the Windows launch command
(`Ascension.exe`, the HD args, no Wine/Proton in the env), the ready-to-play
hard-link plan on a case-insensitive disk, the `safe_rel` refusals, and the
sharing-violation sentence. Tests only: if one ever shows the product is wrong
on Windows, it is marked `xfail(strict=True)` instead of fixing product code.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from yulon import client_names, coa_client, hd_patches, play_client, play_launch, steam
from yulon.catalog.catalog import load_catalog
from yulon.coa_client import ManifestError

COA = load_catalog().get("wow-coa")
OFF = ("-hd", "0")


def _server(tmp_path: Path) -> Path:
    server = tmp_path / "servers" / "coa"
    server.mkdir(parents=True, exist_ok=True)
    return server


def _coa_client(root: Path) -> Path:
    """A fake CoA client folder as Windows ships it: `Ascension.exe` in its own case."""
    client = root / "Ascension"
    (client / "Data" / "enUS").mkdir(parents=True)
    (client / "Ascension.exe").write_bytes(b"MZ the game")
    (client / "DivxDecoder.dll").write_bytes(b"dll")
    (client / "Data" / "common.MPQ").write_bytes(b"MPQ shared" * 100)
    (client / "Data" / "patch-WB1.MPQ.DISABLED").write_bytes(b"MPQ disabled")
    return client


def _launch(play: Path, tmp_path: Path, args: tuple[str, ...]) -> play_launch.LaunchSpec:
    return play_launch.launch_spec(
        play,
        game=COA.id,
        server_dir=_server(tmp_path),
        os_name="windows",
        home=tmp_path / "home",
        config_dir=tmp_path / "config",
        exe_names=COA.client.exe_names,
        wine_dll_overrides=COA.client.wine_dll_overrides,
        args=args,
    )


# -- the Windows launch command --------------------------------------------------


def test_windows_launch_with_hd_off_runs_ascension_with_hd_0_and_no_env(
    tmp_path: Path,
) -> None:
    play = _coa_client(tmp_path)
    server = _server(tmp_path)
    hd_patches.save(server, False)
    args = hd_patches.launch_args(COA.client, server)
    assert args == OFF
    spec = _launch(play, tmp_path, args)
    assert spec.argv == (str(play / "Ascension.exe"), "-hd", "0")
    assert dict(spec.env) == {}
    assert spec.cwd == play


def test_windows_launch_with_hd_on_runs_ascension_bare_and_no_env(tmp_path: Path) -> None:
    play = _coa_client(tmp_path)
    server = _server(tmp_path)
    assert hd_patches.launch_args(COA.client, server) == ()
    spec = _launch(play, tmp_path, ())
    assert spec.argv == (str(play / "Ascension.exe"),)
    assert dict(spec.env) == {}
    assert spec.cwd == play


# -- the ready-to-play hard-link plan --------------------------------------------


def test_exe_asked_in_another_case_resolves_to_the_one_file(tmp_path: Path) -> None:
    """`ascension.exe` finds the shipped `Ascension.exe`: one file, not two.

    The folder is folded into a dict keyed by casefolded path -- the Windows
    disk, modelled without needing one -- so a second spelling can never be a
    second file here.
    """
    client = _coa_client(tmp_path)
    found = steam.client_executable(client, names=("ascension.exe",))
    assert found.is_file()
    assert found == client / "Ascension.exe"
    twin = client / "ascension.exe"
    assert not twin.exists() or os.path.samefile(twin, client / "Ascension.exe")
    disk: dict[str, Path] = {}
    for path in client.rglob("*"):
        if path.is_file():
            disk.setdefault(path.relative_to(client).as_posix().casefold(), path)
    assert disk["ascension.exe"] == found


def test_hard_link_plan_links_disabled_mpq_and_dll_and_copies_the_exe(
    tmp_path: Path,
) -> None:
    orig = _coa_client(tmp_path)
    target = tmp_path / "Ascension (Yu'lon – Conquest of Azeroth)"
    build = play_client.plan(orig, target, server_dir=_server(tmp_path))
    assert Path("Data/patch-WB1.MPQ.DISABLED") in build.linked
    assert Path("Data/common.MPQ") in build.linked
    assert Path("DivxDecoder.dll") in build.linked
    assert Path("Ascension.exe") in build.copied
    folded = [rel.as_posix().casefold() for rel in (*build.linked, *build.copied)]
    assert len(set(folded)) == len(folded)

    linked: dict[str, Path] = {}

    def recording_link(src: Path, dst: Path) -> None:
        linked[src.relative_to(orig).as_posix().casefold()] = dst
        os.link(src, dst)

    marker = play_client.create(
        orig,
        target,
        game=COA.id,
        server_dir=_server(tmp_path),
        allow_full_copy=False,
        link=recording_link,
        reflink=lambda src, dst: False,
    )
    assert marker.full_copy is False
    assert set(linked) >= {
        "data/patch-wb1.mpq.disabled",
        "data/common.mpq",
        "divxdecoder.dll",
    }
    assert "ascension.exe" not in linked
    assert os.path.samefile(
        orig / "Data/patch-WB1.MPQ.DISABLED", target / "Data/patch-WB1.MPQ.DISABLED"
    )
    assert os.path.samefile(orig / "DivxDecoder.dll", target / "DivxDecoder.dll")
    assert (target / "Ascension.exe").read_bytes() == (orig / "Ascension.exe").read_bytes()
    assert not os.path.samefile(orig / "Ascension.exe", target / "Ascension.exe")
    assert client_names.find(target, "ascension.exe") == target / "Ascension.exe"


def test_cross_volume_target_needs_consent_never_a_silent_copy(tmp_path: Path) -> None:
    orig = _coa_client(tmp_path)
    target = tmp_path / "Ascension (Yu'lon – Conquest of Azeroth)"
    partial = target.with_name(target.name + ".yulon-partial")

    def cross_volume(src: Path, dst: Path) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    with pytest.raises(play_client.PlayClientError, match="another drive") as caught:
        play_client.create(
            orig,
            target,
            game=COA.id,
            server_dir=_server(tmp_path),
            allow_full_copy=False,
            link=cross_volume,
            reflink=lambda src, dst: False,
        )
    assert "full copy" in str(caught.value)
    assert not target.exists() and not partial.exists()
    marker = play_client.create(
        orig,
        target,
        game=COA.id,
        server_dir=_server(tmp_path),
        allow_full_copy=True,
        link=cross_volume,
        reflink=lambda src, dst: False,
    )
    assert marker.full_copy is True
    assert (target / "Data/patch-WB1.MPQ.DISABLED").read_bytes() == (
        orig / "Data/patch-WB1.MPQ.DISABLED"
    ).read_bytes()
    assert not os.path.samefile(
        orig / "Data/patch-WB1.MPQ.DISABLED", target / "Data/patch-WB1.MPQ.DISABLED"
    )


# -- safe_rel --------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "CON.txt",
        "con",
        "NUL.json",
        "COM1.dat",
        "a.",
        "a ",
        "A\\b",
        "x:y",
        "../up",
        "/abs",
        ".yulon-part",
    ],
)
def test_safe_rel_refuses_windows_hostile_paths(raw: str) -> None:
    with pytest.raises(ManifestError):
        coa_client.safe_rel(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "Data/patch-WB1.MPQ",
        "Data/Content/TransmogrificationItemData.json",
        "Ascension.exe",
    ],
)
def test_safe_rel_accepts_plain_client_paths(raw: str) -> None:
    assert coa_client.safe_rel(raw).as_posix() == raw


# -- the sharing violation -------------------------------------------------------


def test_sharing_violation_on_win32_says_close_the_game_and_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare PermissionError on win32 is the game holding the file (`_in_use`)."""
    from tests.test_coa_client import _client, _site, _update

    client = _client(tmp_path, {"Ascension.exe": b"old"})
    site = _site({"Ascension.exe": b"new"})
    real_replace = os.replace

    def busy(src: object, dst: object) -> None:
        if str(src).endswith("Ascension.exe"):
            raise PermissionError(13, "Permission denied")
        real_replace(src, dst)

    monkeypatch.setattr(coa_client.sys, "platform", "win32")
    monkeypatch.setattr(os, "replace", busy)
    outcome = _update(client, site, replace_edited=True)
    assert len(outcome.failed) == 1
    assert outcome.failed[0][0] == "Ascension.exe"
    assert "in use" in outcome.failed[0][1]
    assert "Close the game" in outcome.failed[0][1]
    assert (client / "Ascension.exe").read_bytes() == b"old"
