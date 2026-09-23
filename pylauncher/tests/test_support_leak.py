"""THE test of T93: no planted secret survives into any byte or any NAME of what leaves.

Eight secrets, each in the shape it really takes -- two generated `<prefix>-<16 hex>`
database passwords, a custom one a user typed into a conf's `DatabaseInfo`
line, another in Tortoise's dotted `LoginDatabase.Info` spelling, a SOAP
credential minted by the channel, a kept copy from an uninstall, and one each
of a kept copy and a live `.db_password` that are NOT in the generated shape --
are planted in every source a support file reads: the app log and its rotation, a
run log (its name and its lines), a worldserver snapshot (its name and its
lines), the fake `docker logs` of the containers, a container read that fails,
a docker that raises, each install's confs, and the home folder in all of them.
The zip is built through the public `bundle.save`, as the Save button does, and
the Logs tab is driven over the same folder, as a player would.

Controls keep it honest: every secret is first proved to BE in the raw files (a
test that planted nothing would pass), every folder of the zip is proved to hold
what was planted there (a zip that included nothing would pass), and the secrets
are built at runtime, never written here (`test_no_secrets_in_evidence`). Both
the whole value and its last ten characters are searched, so a secret masked
only at its start still fails. Most of the lines carry the secrets bare, with no
`DatabaseInfo` field or `password =` key around them, so only the passwords
`gather_known` found can catch the custom, dotted and SOAP ones. The two
unshaped stores' values (`kept-unshaped`, `live-unshaped`) are in no conf and do
not match the generated shape, so only READING `db-secrets/` and `.db_password`
can catch them: the shaped `kept`, `generated` and `tortoise` would be masked
by the pattern even if those stores were never opened (T93 final review).
"""

from __future__ import annotations

import json
import secrets
import zipfile
from dataclasses import dataclass
from pathlib import Path

from yulon import channel_setup, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog
from yulon.state import KnownInstall
from yulon.support import bundle, runlog
from yulon.support.sources import InstallFacts, LiveLog, sources_for_app
from yulon.ui.logs_view import READING, LogsView
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()
WSL_DISTRO = "Ubuntu-24.04"
QT_VERSION = "6.11.2"


@dataclass(frozen=True)
class _Planted:
    installs: tuple[KnownInstall, ...]
    secrets: dict[str, str]
    homes: tuple[str, ...]
    """Every spelling of the home folder that was planted."""
    seams: bundle.Seams
    raw_roots: tuple[Path, ...]
    """Every folder a planted file is in, for the control."""


def _needles(value: str) -> set[str]:
    """The whole secret and its last ten characters: a mask of its start alone still fails."""
    return {value, value[-10:]}


def _plant(tmp_path: Path) -> _Planted:
    config = platform.config_dir()
    home = str(Path.home())
    homes = tuple(sorted({home, home.replace("\\", "/"), home.replace("/", "\\")}))
    planted = {
        "generated": "tbc-" + secrets.token_hex(8),
        "tortoise": "tortoise-" + secrets.token_hex(8),
        "custom": "Custom" + secrets.token_hex(5) + "Pw",
        "dotted": "Dotted" + secrets.token_hex(5) + "Pw",
        "soap": channel_setup.generate_password(),
        "kept": "vanilla-" + secrets.token_hex(8),
        "kept-unshaped": "Kept" + secrets.token_hex(5) + "Pw",
        "live-unshaped": "Live" + secrets.token_hex(5) + "Pw",
    }
    # Bare values: no `password =` key and no `DatabaseInfo` field around them.
    every = " ".join(f"{label}: {value}" for label, value in planted.items())
    every += " home: " + " ".join(f"{spelling}/wow" for spelling in homes)

    tbc_dir = tmp_path / "srv-tbc"
    tbc_id = composegen.install_id(tbc_dir)
    (tbc_dir / "etc").mkdir(parents=True)
    (tbc_dir / ".db_password").write_text(planted["generated"] + "\n", encoding="utf-8")
    (tbc_dir / "etc" / "mangosd.conf").write_text(
        f'LoginDatabaseInfo = "tbc-db;3306;mangos;{planted["generated"]};tbcrealmd"\r\n'
        f'WorldDatabaseInfo = "tbc-db;3306;mangos;{planted["custom"]};tbcmangos"\r\n'
        "SOAP.Enabled = 1\r\n"
        f"# note {every}\r\n",
        encoding="utf-8",
        newline="",
    )
    tortoise_dir = tmp_path / "srv-tortoise"
    (tortoise_dir / "etc").mkdir(parents=True)
    (tortoise_dir / ".db_password").write_text(planted["tortoise"] + "\n", encoding="utf-8")
    (tortoise_dir / "etc" / "realmd.conf").write_text(
        f'LoginDatabase.Info = "tortoise-db;3306;mangos;{planted["dotted"]};realmd"\n'
        f"# note {every}\n",
        encoding="utf-8",
    )

    # A third install: its `.db_password` is in no conf, so only reading the file knows it.
    vanilla_dir = tmp_path / "srv-vanilla"
    vanilla_dir.mkdir()
    (vanilla_dir / ".db_password").write_text(planted["live-unshaped"] + "\n", encoding="utf-8")

    (config / "credentials").mkdir(parents=True)
    (config / "credentials" / f"wow-tbc-{tbc_id}.json").write_text(
        json.dumps(
            {
                "account": "OWNER",
                "password": planted["soap"],
                "namespace": "urn:MaNGOS",
                "host": "localhost",
                "port": 7878,
            }
        ),
        encoding="utf-8",
    )
    (config / "db-secrets").mkdir()
    (config / "db-secrets" / "wow-vanilla-0badc0de.json").write_text(
        json.dumps({"volume": "v", "password": planted["kept"]}), encoding="utf-8"
    )
    (config / "db-secrets" / "wow-tortoise-0dd0beef.json").write_text(
        json.dumps({"volume": "v", "password": planted["kept-unshaped"]}), encoding="utf-8"
    )

    (config / "yulon.log").write_text(
        f"2026-09-22 10:00:00 INFO [yulon] APPLOG {every}\n", encoding="utf-8"
    )
    (config / "yulon.log.1").write_text(f"ROTATED {every}\r\n", encoding="utf-8", newline="")
    runs = runlog.runs_dir(config)
    runs.mkdir(parents=True)
    (runs / "install-wow-tbc-20260922T101010Z.log").write_text(
        f"[10:10:10] RUNLOG {every}\n", encoding="utf-8"
    )
    # A NAME that carries a secret only the known values can catch.
    (runs / f"rebuild-{planted['soap']}-20260922T111111Z.log").write_text(
        f"[11:11:11] RUNLOG {every}\n", encoding="utf-8"
    )
    (runlog.logs_dir(config) / f"wow-tbc-{tbc_id}-20260922T101010Z.log").write_text(
        "SNAPSHOT Cannot connect to world database "
        f"tbc-db;3306;mangos;{planted['custom']};tbcmangos\n{every}\n",
        encoding="utf-8",
    )
    (runlog.logs_dir(config) / f"wow-tortoise-{planted['dotted']}-20260922T121212Z.log").write_text(
        f"SNAPSHOT {every}\n", encoding="utf-8"
    )

    def live_logs(install: InstallFacts, silent: set[str | None]) -> list[LiveLog]:
        if install.wsl_distro == WSL_DISTRO:
            raise RuntimeError(f"the daemon in {WSL_DISTRO} said {every}")
        return [
            LiveLog("tbc-mangosd", f"LIVE world {every}\n"),
            LiveLog("tbc-realmd", f"LIVE auth {every}\n"),
            LiveLog("tbc-db", None, f"timed out after 20 s ({every})"),
        ]

    def docker_version(distro: str | None) -> str | None:
        if distro is None:
            raise RuntimeError(f"cannot connect: {every}")
        return f"27.0.1 {every}"

    installs = (
        KnownInstall(game="wow-tbc", server_dir=tbc_dir),
        KnownInstall(game="wow-tortoise", server_dir=tortoise_dir, wsl_distro=WSL_DISTRO),
        KnownInstall(game="wow-vanilla", server_dir=vanilla_dir),
    )
    return _Planted(
        installs=installs,
        secrets=planted,
        homes=homes,
        seams=bundle.Seams(live_logs=live_logs, docker_version=docker_version),
        raw_roots=(config, tbc_dir, tortoise_dir, vanilla_dir),
    )


def _assert_planted(planted: _Planted) -> None:
    """Control: every secret and the home folder really are in the files, raw."""
    files = [path for root in planted.raw_roots for path in root.rglob("*") if path.is_file()]
    raw = b"".join(path.read_bytes() for path in files)
    names = " ".join(path.name for path in files)
    for label, value in planted.secrets.items():
        assert value.encode() in raw, f"control: {label} was never planted"
    for label in ("soap", "dotted"):
        assert planted.secrets[label] in names, f"control: no file NAME carries {label}"
    for spelling in planted.homes:
        assert spelling.encode() in raw, "control: the home folder was never planted"


def test_no_planted_secret_survives_into_any_byte_or_name_of_the_zip(tmp_path: Path) -> None:
    planted = _plant(tmp_path)
    _assert_planted(planted)
    sources = sources_for_app(planted.installs, CATALOG, qt_version=QT_VERSION)

    dest = tmp_path / "out" / "support.zip"
    dest.parent.mkdir()
    # No `home=`: the Save button's route, which masks `Path.home()`.
    report = bundle.save(dest, sources, seams=planted.seams)

    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        contents = {name: archive.read(name) for name in names}

    # Controls: every source is IN the zip, redacted but not emptied.
    for folder, marker, count in (
        ("app/", b"APPLOG", 1),
        ("app/", b"ROTATED", 1),
        ("runs/", b"RUNLOG", 2),
        ("snapshots/", b"SNAPSHOT", 2),
        ("live/", b"LIVE", 4),
        ("conf/", b"# note", 2),
    ):
        holding = [n for n in names if n.startswith(folder) and marker in contents[n]]
        assert len(holding) == count, f"control: {count} x {marker!r} in {folder}, got {holding}"
        assert all(b"***" in contents[n] for n in holding), f"control: nothing masked in {holding}"
    assert {"MANIFEST.txt", "system-info.txt"} <= set(names)
    manifest = contents["MANIFEST.txt"]
    assert b"timed out after 20 s" in manifest, "control: the failed container read is missing"
    assert b"docker not reachable (RuntimeError" in manifest, "control: the raising seam"
    info = contents["system-info.txt"]
    assert b"Docker on this machine: not reachable (RuntimeError)" in info
    assert f"Docker in WSL distro {WSL_DISTRO}: 27.0.1".encode() in info
    assert b"Servers Yu'lon knows about: 3" in info

    shown = repr(report)
    for label, value in planted.secrets.items():
        for needle in _needles(value):
            for name in names:
                assert needle not in name, f"{label} in the member NAME {name!r}"
                assert needle.encode() not in contents[name], f"{label} in {name}"
            assert needle not in shown, f"{label} in the report the tab shows"
    # `report.path` is where the player chose to save it: his own, and not in the zip.
    said = repr((report.included, report.skipped, report.dropped))
    for spelling in planted.homes:
        for name in names:
            assert spelling not in name, f"the home folder in the member NAME {name!r}"
            assert spelling.encode() not in contents[name], f"the home folder in {name}"
        assert spelling not in said, "the home folder in the report the tab shows"


def test_nothing_the_logs_tab_shows_or_copies_carries_a_planted_secret(
    qapp: object, tmp_path: Path
) -> None:
    planted = _plant(tmp_path)
    _assert_planted(planted)
    copied: list[str] = []
    view = LogsView(
        lambda: planted.installs,
        CATALOG,
        jobs=run_inline,
        clipboard=copied.append,
        bundle_seams=planted.seams,
    )
    view.refresh()
    labels = [view.source_picker.itemText(i) for i in range(view.source_picker.count())]
    # Control: the app log and its rotation, both runs and both snapshots are offered.
    assert len(labels) == 6, labels

    for index, label in enumerate(labels):
        view.source_picker.setCurrentIndex(index)
        view.refresh()
        assert view.source_picker.currentText() == label
        text = view.shown_text()
        assert text != READING and "***" in text, f"control: {label} was not read: {text!r}"
        view.copy_last_lines()
        assert len(copied) == index + 1, f"control: nothing copied from {label}"
        for secret, value in planted.secrets.items():
            for needle in _needles(value):
                assert needle not in label, f"{secret} in the picker's label {label!r}"
        for where, seen in (("the viewer", text), ("the clipboard", copied[-1])):
            for secret, value in planted.secrets.items():
                for needle in _needles(value):
                    assert needle not in seen, f"{secret} in {where} for {label}"
            for spelling in planted.homes:
                assert spelling not in seen, f"the home folder in {where} for {label}"
