"""T656: a press that moves TortoiseBots carries its changed defaults into the live conf.

An installed Tortoise server's `etc/aiplayerbot.conf` was made once, from the image's
template at the install's TortoiseBots commit, and nothing rewrites it afterwards: T597's
live update kept it byte for byte. So a default the module changes never reaches an
existing server. TortoiseBots cb90e735 (#663, in release v2026-10-10) did exactly that
for the fix of its world stalls (Sagiroth/TortoiseBots#642): `AiPlayerbot.
PoolBudgetWhenTickOverMs` 150 -> 0 in `ai/playerbot/aiplayerbot.conf.dist.in` (the code
default moved with it), so every server installed before it keeps the old gate after the
update. Read at both commits, 83d88fdc and fb0b2eb5, the active defaults that changed are
that key and the four the install itself writes (the pool size and autologin/autocreate).

The rule: for each key whose default differs between the two commits' `.conf.dist`
(read from git, never guessed), a live value still equal to the old default is set to the
new one; a value the player changed is kept; a key the catalog's own conf table writes is
Yu'lon's, never moved by this; a key the live file does not set is left to the code. With
the servers down, before the new build first starts, after a backup; a rollback puts the
file back; a Return moving the other way reverses it by the same rule.

Every test drives the real `update_to_latest()` on an installed Tortoise folder, with git's
trees at `Recorder.blobs` (the `tree_bytes` seam).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support_native import FakeSnapshot, Recorder
from tests.test_return_newer_migrations import BOTS, BOTS_PIN, _ready, _return
from tests.test_update_rollback_database import TORTOISE, _at, _old_build_comes_back
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    NEW,
    OLD,
    _cmangos,
    _gated,
    tbc_engine,
)
from yulon import tuning
from yulon.catalog import native
from yulon.catalog.families.cmangos import ETC_DIR
from yulon.catalog.installer import InstallerError, InstallOptions

DIST = "ai/playerbot/aiplayerbot.conf.dist.in"
"""Where TortoiseBots keeps the template its build turns into `etc/aiplayerbot.conf`."""

BEFORE_663 = b"""# Random bot count
AiPlayerbot.MinRandomBots = 0
AiPlayerbot.MaxRandomBots = 0
AiPlayerbot.PoolTickBudgetUs = 10000
AiPlayerbot.PoolBudgetWhenTickOverMs = 150
AiPlayerbot.CombatTickBudgetUs = 15000
"""
"""The lines of the `.dist` at 83d88fdc that matter here, as that commit spells them."""

AFTER_663 = b"""# Random bot count. Default 500 is sized for an average PC.
AiPlayerbot.MinRandomBots = 500
AiPlayerbot.MaxRandomBots = 500
AiPlayerbot.PoolTickBudgetUs = 10000
AiPlayerbot.PoolBudgetWhenTickOverMs = 0
AiPlayerbot.CombatTickBudgetUs = 15000
AiPlayerbot.TargetWorldTickMs = 50
"""
"""The same lines at fb0b2eb5: one default moved, one key added, two pool sizes moved."""

LIVE = (
    "# written at install\r\n"
    "AiPlayerbot.Enabled = 1\r\n"
    "AiPlayerbot.MinRandomBots = {minimum}\r\n"
    "AiPlayerbot.PoolTickBudgetUs = 10000\r\n"
    "AiPlayerbot.PoolBudgetWhenTickOverMs   = {gate}\r\n"
    "# the end\r\n"
)
"""An installed server's conf: CRLF, odd spacing, Yu'lon's pool size, a line after the key."""


def _live(server_dir: Path, *, gate: str = "150", minimum: str = "500") -> Path:
    path = server_dir / ETC_DIR / "aiplayerbot.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(LIVE.format(gate=gate, minimum=minimum).encode("utf-8"))
    return path


def _dists(
    rec: Recorder, server_dir: Path, *, running: bytes | None, target: bytes | None, target_rev: str
) -> None:
    bots = server_dir / BOTS.dest
    if running is not None:
        rec.blobs[(bots, OLD, DIST)] = running
    if target is not None:
        rec.blobs[(bots, target_rev, DIST)] = target


def _update(tmp_path: Path, **overrides: object) -> tuple[Recorder, Path, native.StagedInstaller]:
    """An installed Tortoise on `OLD` whose upstream (and newest release) is `NEW`."""
    rec, server_dir, _made = _cmangos(tmp_path, TORTOISE)
    made = tbc_engine(
        rec, entry=TORTOISE, **{"world_running": lambda container: False, **overrides}
    )
    made._snapshot = FakeSnapshot(rec)  # type: ignore[attr-defined]
    return rec, server_dir, made


def _press(made: native.StagedInstaller, server_dir: Path) -> tuple[list[str], Exception | None]:
    said: list[str] = []
    try:
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)
    except InstallerError as exc:
        return said, exc
    return said, None


def _writes(monkeypatch: pytest.MonkeyPatch, rec: Recorder) -> None:
    """`tuning.write`, recorded in the call log so its place among the servers' calls shows."""
    real = tuning.write

    def write(path: Path, edits: dict[str, str], **kwargs: object) -> Path:
        rec.calls.append(f"conf-write:{path.name}:{','.join(sorted(edits))}")
        return real(path, edits, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tuning, "write", write)


def test_the_catalog_says_whose_template_the_bots_conf_follows() -> None:
    native_block = TORTOISE.install.native
    assert native_block is not None and native_block.cmangos is not None
    files = native_block.cmangos.conf.files
    follows = files["aiplayerbot.conf"].defaults_follow
    assert follows is not None
    assert (follows.repo, follows.path) == ("Sagiroth/TortoiseBots", DIST)
    assert follows.repo in {source.repo for source in TORTOISE.emulator.sources}


def test_an_update_past_663_sets_the_old_tick_gate_to_the_new_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir)
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    said, raised = _press(made, server_dir)
    assert raised is None, raised
    # Only the value moved: CRLF, the spacing and every other line are kept.
    assert path.read_bytes() == LIVE.format(gate="0", minimum="500").encode("utf-8")
    backups = tuning.backups_of(path)
    assert len(backups) == 1
    assert backups[0].read_bytes() == LIVE.format(gate="150", minimum="500").encode("utf-8")
    lines = [line for line in said if "PoolBudgetWhenTickOverMs" in line]
    assert len(lines) == 1, said
    assert "AiPlayerbot.PoolBudgetWhenTickOverMs 150 -> 0" in lines[0], lines[0]
    assert "aiplayerbot.conf" in lines[0]
    assert backups[0].name in lines[0]


def test_the_conf_is_changed_with_the_servers_down_before_the_new_build_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    _live(server_dir)
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    _said, raised = _press(made, server_dir)
    assert raised is None, raised
    stopped = _at(rec.calls, "stop_servers")
    wrote = _at(
        rec.calls, "conf-write:aiplayerbot.conf:AiPlayerbot.PoolBudgetWhenTickOverMs", stopped
    )
    _at(rec.calls, "recreate", wrote)


def test_a_value_the_player_changed_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir, gate="200")
    before = path.read_bytes()
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    said, raised = _press(made, server_dir)
    assert raised is None, raised
    assert path.read_bytes() == before
    assert tuning.backups_of(path) == ()
    assert not any(call.startswith("conf-write:") for call in rec.calls)
    assert not any("PoolBudgetWhenTickOverMs" in line for line in said)


def test_a_key_the_install_writes_is_never_moved_by_upstreams_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MinRandomBots` is in the catalog's conf table: a 0 there is the player's, kept."""
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir, minimum="0")
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    _said, raised = _press(made, server_dir)
    assert raised is None, raised
    assert tuning.conf_value(path.read_text(encoding="utf-8"), "AiPlayerbot.MinRandomBots") == "0"
    assert (
        tuning.conf_value(path.read_text(encoding="utf-8"), "AiPlayerbot.PoolBudgetWhenTickOverMs")
        == "0"
    )
    assert [c for c in rec.calls if c.startswith("conf-write:")] == [
        "conf-write:aiplayerbot.conf:AiPlayerbot.PoolBudgetWhenTickOverMs"
    ]


def test_a_rolled_back_update_puts_the_conf_back_before_the_old_build_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made = _update(tmp_path, wait_ready=_old_build_comes_back())
    _writes(monkeypatch, rec)
    path = _live(server_dir)
    before = path.read_bytes()
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    restored: list[int] = []
    real_restore = tuning.restore

    def restore(from_backup: Path, target: Path) -> None:
        restored.append(len(rec.calls))
        rec.calls.append(f"conf-restore:{target.name}")
        real_restore(from_backup, target)

    monkeypatch.setattr(tuning, "restore", restore)
    _said, raised = _press(made, server_dir)
    assert raised is not None
    assert path.read_bytes() == before
    start_new = _at(rec.calls, "recreate", _at(rec.calls, "conf-write:"))
    back = _at(rec.calls, "conf-restore:aiplayerbot.conf", start_new)
    _at(rec.calls, "recreate", back)
    assert any("aiplayerbot.conf is back as it was" in line for line in _said), _said


def test_a_return_that_moves_back_past_663_puts_the_old_default_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule the other way: the running commit's default 0, the target's 150."""
    rec, server_dir, made = _ready(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir, gate="0")
    _dists(rec, server_dir, running=AFTER_663, target=BEFORE_663, target_rev=BOTS_PIN)
    said, raised = _return(made, server_dir)
    assert raised is None, raised
    assert (
        tuning.conf_value(path.read_text(encoding="utf-8"), "AiPlayerbot.PoolBudgetWhenTickOverMs")
        == "150"
    )
    assert any("PoolBudgetWhenTickOverMs" in line for line in said), said


def test_a_template_git_cannot_read_leaves_the_conf_alone_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir)
    before = path.read_bytes()
    _dists(rec, server_dir, running=BEFORE_663, target=None, target_rev=NEW)
    rec.blobs[(server_dir / BOTS.dest, NEW, DIST)] = None  # type: ignore[assignment]
    said, raised = _press(made, server_dir)
    assert raised is None, raised
    assert path.read_bytes() == before
    assert any("aiplayerbot.conf" in line and "as it is" in line for line in said), said


def test_a_key_set_twice_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Which copy the server reads is the parser's business; Yu'lon does not guess it."""
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir)
    with path.open("ab") as handle:
        handle.write(b"AiPlayerbot.PoolBudgetWhenTickOverMs = 150\r\n")
    before = path.read_bytes()
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    _said, raised = _press(made, server_dir)
    assert raised is None, raised
    assert path.read_bytes() == before
    assert not any(call.startswith("conf-write:") for call in rec.calls)


def test_a_default_cmake_fills_in_is_never_written_into_the_live_conf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `.dist.in` value like `@CONF_DIR@` is what the build substitutes, not a value."""
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir)
    with path.open("ab") as handle:
        handle.write(b"AiPlayerbot.ConfDir = etc\r\n")
    _dists(
        rec,
        server_dir,
        running=BEFORE_663 + b"AiPlayerbot.ConfDir = etc\n",
        target=AFTER_663 + b"AiPlayerbot.ConfDir = @CONF_DIR@\n",
        target_rev=NEW,
    )
    _said, raised = _press(made, server_dir)
    assert raised is None, raised
    text = path.read_text(encoding="utf-8")
    assert tuning.conf_value(text, "AiPlayerbot.ConfDir") == "etc"
    assert tuning.conf_value(text, "AiPlayerbot.PoolBudgetWhenTickOverMs") == "0"


def test_a_live_conf_without_the_key_is_not_given_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent key is the code's default, which moved with the template: nothing appended."""
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    path = _live(server_dir)
    text = path.read_bytes().decode("utf-8")
    path.write_bytes(
        "".join(
            line
            for line in text.splitlines(keepends=True)
            if "PoolBudgetWhenTickOverMs" not in line
        ).encode("utf-8")
    )
    before = path.read_bytes()
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    said, raised = _press(made, server_dir)
    assert raised is None, raised
    assert path.read_bytes() == before
    assert tuning.backups_of(path) == ()
    assert not any("PoolBudgetWhenTickOverMs" in line for line in said)


def _world_work(rec: Recorder) -> native.ServersDownWork:
    """T531's world catch-up as the family would hand it over, recorded."""

    def forward(ctx: object) -> Iterator[str]:
        rec.calls.append("world-forward")
        yield "world forward"

    def back(ctx: object) -> Iterator[str]:
        rec.calls.append("world-back")
        yield "world back"

    return native.ServersDownWork(
        prepare=lambda: iter(()), forward=forward, back=back, finishes_start_refusal=False
    )


def test_the_conf_is_carried_when_the_world_also_has_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both works run: the world's first, then the conf; neither replaces the other."""
    rec, server_dir, made = _update(tmp_path)
    _writes(monkeypatch, rec)
    monkeypatch.setattr(made, "_world_catch_up_work", lambda catch_up: _world_work(rec))
    path = _live(server_dir)
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    _said, raised = _press(made, server_dir)
    assert raised is None, raised
    world = _at(rec.calls, "world-forward", _at(rec.calls, "stop_servers"))
    wrote = _at(rec.calls, "conf-write:aiplayerbot.conf", world)
    _at(rec.calls, "recreate", wrote)
    assert (
        tuning.conf_value(path.read_text(encoding="utf-8"), "AiPlayerbot.PoolBudgetWhenTickOverMs")
        == "0"
    )


def test_a_rollback_with_world_work_puts_the_conf_back_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made = _update(tmp_path, wait_ready=_old_build_comes_back())
    _writes(monkeypatch, rec)
    monkeypatch.setattr(made, "_world_catch_up_work", lambda catch_up: _world_work(rec))
    real_restore = tuning.restore

    def restore(from_backup: Path, target: Path) -> None:
        rec.calls.append(f"conf-restore:{target.name}")
        real_restore(from_backup, target)

    monkeypatch.setattr(tuning, "restore", restore)
    path = _live(server_dir)
    before = path.read_bytes()
    _dists(rec, server_dir, running=BEFORE_663, target=AFTER_663, target_rev=NEW)
    _said, raised = _press(made, server_dir)
    assert raised is not None
    assert path.read_bytes() == before
    back = _at(rec.calls, "conf-restore:aiplayerbot.conf", _at(rec.calls, "recreate"))
    _at(rec.calls, "world-back", back)
