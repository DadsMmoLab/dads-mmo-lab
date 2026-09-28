"""The database password that has to outlive the folder it was kept in (8.9a).

**The defect this exists for.** `purge.Uninstaller.run(keep_characters=True)`
keeps `<project>_db-data` and then deletes the server folder. On every
`generated` entry — `wow-tbc`, `wow-vanilla`, `wow-tortoise` — the password that
database was created with lives in `.db_password` INSIDE that folder, and
nowhere else. The volume name is a digest of the absolute path, so a reinstall
to the same folder comes back to the same volume with a freshly minted password
and stops at `CmangosInstaller._db_password`, which refuses to write a new
secret next to a database that exists. "Keep my characters" was therefore true
in the letter and false in the substance: what survived was a database nothing
could open. Found by the 8.9b lane reading 8.9a's code, 2026-09-08, before the
gate could find it the expensive way.

**The decision.** The secret is copied into Yu'lon's own config directory
BEFORE the folder is removed, and the reinstall looks there when the file is
gone. Three properties decided the shape:

* *It has to survive the delete.* Only somewhere outside the folder does, and
  the config dir is the one place this app already keeps per-install facts it
  must not lose (`credentials/`, `logs/`).
* *It has to be found again by a reinstall to the same folder, with nothing to
  go on but the folder.* So the key is `<game>-<install_id>`, the same
  path-derived pair `channel_setup.credential_path()` uses — `install_id()` is
  a hash of the absolute path, which is exactly what makes the reinstall land
  back on the kept volume in the first place. Same folder, same game, same key.
* *It must never claim more than it knows.* The file records the VOLUME the
  password opens, and the install stage that would otherwise refuse checks that
  name before it trusts the value. A copy kept for one volume can then never be
  used to argue about another.

**What it is not.** It is not a password manager and it is not a backup: it
holds one secret per install, written by the one action that would otherwise
destroy it. Nothing removes it — a purge that removes the volume leaves the
copy behind (`purge.LEFT_BEHIND` says so in the dialog), and that is harmless:
a fresh volume of the same name is INITIALISED with whatever password the
install resolves, so recalling the old one initialises the new database with it
rather than locking anything out.

**A refusal is the other half of the design.** If a ticked purge cannot read
the password it would have to keep, it removes nothing and says so
(`purge.Uninstaller.run`). Keeping a volume whose key is already lost is the
lie this module exists to stop, and doing it politely would not make it true.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from yulon import platform, winacl
from yulon.log import get_logger

if TYPE_CHECKING:  # pragma: no cover - a type, not a dependency
    from yulon.catalog.catalog import Install

logger = get_logger(__name__)

DIR_NAME = "db-secrets"
"""Where the copies live under `platform.config_dir()`, beside `credentials/`."""

SECRET_MODE = 0o600
"""Owner-only, set by `os.open`'s mode rather than by a later `chmod`.

Same reasoning as `channel_setup.CREDENTIAL_MODE`: a chmod afterwards is a
second step and the window before it is exactly when the file is world-readable.
On Windows the mode argument is ignored, which is why the test asserts the flags
the file was CREATED with rather than reading the mode back; there the file is
private by `db-secrets/`'s own owner-only DACL instead (`winacl`, T151).
"""


@dataclass(frozen=True)
class AtRisk:
    """What a ticked purge would destroy by deleting the folder.

    Three states in one object, and the empty one is a real answer: a `fixed`
    entry keeps its password in `catalog.json`, so deleting the folder costs it
    nothing and there is nothing to keep. `problem` non-empty is the refusal —
    the same shape `PurgePlan.refusal` and `logsnap.Snapshot.problem` use, so a
    caller reads one field rather than deciding which of two half-answers to
    believe.
    """

    password: str = ""
    problem: str = ""


@dataclass(frozen=True)
class Kept:
    """A copy that was made, read back: the secret and the volume it opens."""

    password: str
    volume: str


def at_risk(install: Install, server_dir: Path) -> AtRisk:
    """The password that dies with `server_dir`, or why it cannot be read.

    `install.db_password()` is the reader, rather than a second one written
    here: it is already the one place that knows a generated plan's file is
    read with `.strip()` and that a blank file is not a password. What this
    function adds is the distinction that reader deliberately does not make —
    "there is nothing at risk" (fixed) against "there is, and it is not
    knowable from here" (generated, and the file will not read).
    """
    plan = install.password
    if plan.mode != "generated" or not plan.file:
        return AtRisk()
    password = install.db_password(server_dir)
    if password:
        return AtRisk(password=password)
    path = server_dir / plan.file
    return AtRisk(
        problem=(
            f"{path} could not be read"
            if path.exists()
            else f"{path} is not there, so this install's password is not knowable from here"
        )
    )


def secret_path(game: str, install_id: str, *, config_dir: Path | None = None) -> Path:
    """Where this install's kept password lives: `db-secrets/<game>-<install id>.json`."""
    root = config_dir if config_dir is not None else platform.config_dir()
    return root / DIR_NAME / f"{game}-{install_id}.json"


def remember(
    game: str,
    install_id: str,
    *,
    password: str,
    volume: str,
    config_dir: Path | None = None,
) -> Path:
    """Keep `password` where a reinstall to this folder will find it. Raises `OSError`.

    Deliberately not swallowing its own failure: the one caller is an uninstall
    that has not yet removed anything, and a copy that was not made has to stop
    that press rather than be logged past.
    """
    path = secret_path(game, install_id, config_dir=config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Before the file is created, so on Windows it is born under the folder's
    # owner-only DACL rather than the profile's; a refusal is a warning (T151).
    winacl.secure_folder(path.parent)
    payload = json.dumps({"volume": volume, "password": password}, indent=2)
    # `O_TRUNC` and not `O_EXCL`: a second uninstall of the same folder has to
    # be able to land on top of the first one's copy.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_MODE)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload + "\n")
    logger.info(f"kept {game}'s database password for {volume} at {path}")
    return path


def recall(game: str, install_id: str, *, config_dir: Path | None = None) -> Kept | None:
    """The copy kept for this install, or `None` when there is not a usable one.

    Never raises. A missing, hand-edited or truncated file means the reinstall
    goes on to mint a password exactly as it did before this module existed —
    and the stage that would lock the user out of an existing database refuses
    on its own evidence, not on this one's silence.
    """
    path = secret_path(game, install_id, config_dir=config_dir)
    # A copy kept before T151 gets its folder's owner-only DACL on this read.
    winacl.secure_folder(path.parent)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        password = str(raw["password"])
        volume = str(raw["volume"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug(f"no kept database password at {path}: {type(exc).__name__}")
        return None
    if not password or not volume:
        logger.info(f"the kept database password at {path} is blank; ignoring it")
        return None
    return Kept(password=password, volume=volume)
