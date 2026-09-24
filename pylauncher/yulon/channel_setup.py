"""The app's own SOAP account: mint it, prove it, then keep it (Phase 8.2a).

Pure and seam-injected, so the whole decision tree is testable with no server
and no database. What is impure — creating the row, writing the credential file,
enabling the listener — is layered on top and hands its answers back in here.

## Everything below was learned once already

`rust-main:crates/dml-wow/src/soap_autosetup.rs` has been through this, and its
three hard-won rules are kept with their reasons:

**A create that succeeded followed by a verify that failed must not reopen the
latch.** Otherwise the next poll creates a SECOND account and the one after that
a third — one row per tick into the user's auth database, forever. That is why
this is a state machine and not a function: `Pending` carries the credential
forward and says re-verify, never re-create. Design C's write ledger flagged the
same file as the hazard (`soap_autosetup.rs:116-120`) and named this as the
defence.

**Sixteen characters is a ceiling, not a preference.** AzerothCore validates
`{4,16}` before it writes anything, so a "stronger" 32-character password is
refused on every fresh install — at the one moment the user has nothing to
retype.

**Rejection sampling, not `byte % 70`.** 256 is not a multiple of the alphabet's
70 symbols (`256 = 3*70 + 46`), so plain modulo hands the first 46 symbols a
fourth chance the other 24 never get. The bias is invisible in any output a
person would look at, which is exactly why it belongs in code rather than in a
review.

## What this module adds to that

The account's **name is derived from the install id**, so two installs of one
game on one machine get different accounts and the same install always gets the
same one. That is what makes "create" idempotent: a second run finds the row it
made last time instead of writing another.

And only `Verified` can produce credentials. The architecture's rule for this
module — never persist before a round trip has answered — is expressed in the
types rather than in a comment: the earlier states have no such method, so a
caller cannot write a credential file it has not proved.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from yulon import commands, platform, soap
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, ConfPatch
from yulon.catalog.families import conf
from yulon.log import get_logger

logger = get_logger(__name__)

PASSWORD_LENGTH = 16
"""AzerothCore's own ceiling for an account password, not a preference here."""

PASSWORD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_@#%+=!-"
"""Exactly the characters account creation accepts: 26 + 26 + 10 + 8 = 70."""

MAX_VERIFY_TRIES = 3
"""Attempts a created-but-unverified account gets before the user is asked.

Verify can fail after a successful create for one interesting reason — the
credential is well formed and the server rejects it — and one boring one: the
world went away between the two calls. Three tries tells them apart without
spinning.
"""

ACCOUNT_PREFIX = "YULON_"


def now_utc() -> str:
    """The moment a round trip answered, in words a person reads once.

    Minutes, not seconds: the question this answers is "was that recently or
    was that in March", and a false precision invites the reader to compare two
    values that were never measured against the same clock.
    """
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def generate_password(fill: Callable[[int], bytes] = secrets.token_bytes, size: int = 64) -> str:
    """A fresh password, unbiased over `PASSWORD_ALPHABET`.

    `fill` is a seam so the discard rule can be proved with a scripted byte
    stream; production passes `secrets.token_bytes`.
    """
    alphabet = PASSWORD_ALPHABET
    n = len(alphabet)
    limit = (256 // n) * n  # 210: the largest multiple of n inside a byte
    out: list[str] = []
    while len(out) < PASSWORD_LENGTH:
        for byte in fill(size):
            if byte < limit:
                out.append(alphabet[byte % n])
                if len(out) == PASSWORD_LENGTH:
                    break
    return "".join(out)


def account_name(install_id: str) -> str:
    """This install's account name — stable for it, different for its neighbour."""
    return (ACCOUNT_PREFIX + install_id).upper()


@dataclass(frozen=True)
class Idle:
    """Nothing has been created yet."""

    def created(self, account: str, password: str) -> Pending:
        """Record a row that now exists, un-proved."""
        return Pending(account=account, password=password, tries=0)


@dataclass(frozen=True)
class Pending:
    """The row exists and the round trip has not answered yet.

    Deliberately has no `created()`: once a row exists, the only ways forward
    are to prove it or to give up. That is the latch.
    """

    account: str
    password: str = field(repr=False)
    tries: int = 0

    def verify_failed(self) -> Pending | GaveUp:
        """One more try, or hand over to the user — never another account."""
        tries = self.tries + 1
        if tries >= MAX_VERIFY_TRIES:
            return GaveUp(
                account=self.account,
                reason=(
                    f"the account {self.account} exists but three round trips did not prove it, "
                    "so nothing was saved and it is over to you"
                ),
            )
        return Pending(account=self.account, password=self.password, tries=tries)

    def verified(self, *, now: Callable[[], str] = now_utc) -> Verified:
        """Proved, and when. The clock is a seam so the value can be asserted."""
        return Verified(account=self.account, password=self.password, at=now())


@dataclass(frozen=True)
class Verified:
    """The account exists AND a real round trip has answered through it."""

    account: str
    password: str = field(repr=False)
    at: str | None = None
    """When that round trip answered, as `now_utc()` writes it.

    Optional only because a credential written before this field existed has no
    time in it, and refusing to read such a file would break the channel of
    every install that already has one. A missing time reads as unknown, never
    as now -- inventing the current moment for a file of unknown age is the one
    answer that would be actively misleading.
    """

    def credentials(self, *, host: str, port: int, namespace: str) -> soap.Endpoint:
        """The endpoint to persist. Only reachable from here, by design.

        `namespace` is passed rather than defaulted for the reason `envelope()`
        gives: it is not the same on both families, and a file that records the
        wrong one misleads everything that reads the file rather than the entry.
        """
        return soap.Endpoint(
            host=host,
            port=port,
            account=self.account,
            password=self.password,
            namespace=namespace,
        )


@dataclass(frozen=True)
class Refused:
    """A credential exists and the server says no to it.

    Distinct from `GaveUp` because it is repairable and `GaveUp` is not: the
    account is known, so the way out is to reset its password, never to create
    another. Distinct from `Idle` for the same reason -- an `Idle` install
    would create.
    """

    account: str
    password: str = field(repr=False)
    reason: str


@dataclass(frozen=True)
class GaveUp:
    """This run will not try again; the reason is for a person to read."""

    account: str
    reason: str


State = Idle | Pending | Verified | Refused | GaveUp


# -- the enable press --------------------------------------------------------


HOST_PORT_VAR = "DOCKER_SOAP_EXTERNAL_PORT"
"""The `.env` key the base compose reads for SOAP's whole host binding.

Named in `docker-compose.yml` beside the mapping itself: the value carries the
address AND the port, because a literal `127.0.0.1:` prefix on the mapping would
render `127.0.0.1:127.0.0.1:7878:7878` once this key is set.
"""

RELEASED_HOST_PORT = "127.0.0.1:0"
"""What a rolled-back channel claims: loopback, and whatever port is free."""

BACKUP_SUFFIX = ".before-channel"
"""The override as it was before the first press, kept beside it."""


class EnableRefused(RuntimeError):
    """The press declined. The message is written for a person to act on."""


@dataclass(frozen=True)
class Enabled:
    """What the press did, and whether it had anything to do."""

    path: Path
    changed: bool


def install_bind_label(server_dir: Path) -> str:
    """The `:z` (or nothing) the install put on this folder's host binds, asked again now.

    The install's own decision and its own inputs -- `platform.bind_label()` fed
    `selinux_enforcing()` and `filesystem_type()`, as `stage_generate_compose()`
    feeds it -- so the press re-renders the override with the label the install
    wrote rather than with none (T102). Both are looked up on the call, not
    bound at import, so a test that patches `platform` reaches the route every
    `InstallChannel` the app builds takes. The filesystem is asked only when
    SELinux enforces, as `git.ContainerGit` asks it: off SELinux the answer
    cannot matter and `stat` is a subprocess.
    """
    enforcing = platform.selinux_enforcing()
    return platform.bind_label(
        enforcing=enforcing,
        fs_type=platform.filesystem_type(server_dir) if enforcing is True else None,
    )


def enable(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    templates_root: Path,
    world_running: bool,
    db_password: str | None = None,
    bind_label: str | None = None,
) -> Enabled:
    """Write this install's channel on, and only while the world is down.

    **Requiring the world stopped is the whole design of this step**, and it
    replaced a probe. The version before it checked the port was free and then
    wrote: check-then-act, with a window between the probe and the bind, and a
    bind that fails for reasons a probe cannot see. On the CMaNGOS trees a
    failed SOAP bind is `exit(-1)` with no character saves.

    Declining to run while there is a world to lose removes the hazard rather
    than warning about it, and it is smaller than the guard it replaced: the
    configuration is written while the server is down, and the user's ordinary
    Start brings it up through `docker.start_staged()`, which Phase 7 proved.

    Only the override is rewritten. The SOAP environment lives in that block
    rather than in the install's own so that Phase 7.1's byte-identical
    compose fixtures keep asserting what they assert.

    It is rewritten WITH the install's bind label (`bind_label`, asked of the
    host through `install_bind_label()` when not given). Until T102 it was
    rendered with none, and on an enforcing SELinux host the press turned the
    install's `./modules:/azerothcore/modules:z` into a bind with no label --
    measured on `yulon-fedora` (Fedora 44, 2026-09-24). The world still read
    the folder while the install's relabel was on it, and crash-looped on
    `Permission denied` once the folder lost that label; with the `:z` kept,
    the daemon relabelled the same folder at the next Start and it came up.
    """
    operations = entry.operations
    if operations is None:
        raise EnableRefused(
            f"{entry.id} has no measured command channel yet, so there is nothing to turn on"
        )
    if world_running:
        raise EnableRefused(
            "the server has to be stopped before the command channel can be turned on. Stop it, "
            "press this again, then start it as usual — the setting is read when the world "
            "starts, and writing it under a running world risks the world."
        )

    # The conf half first, and deliberately: a tree that reads no environment is
    # not switched on by the override at all, and the override's only job there
    # is publishing the port. Doing it first means a refusal -- a conf that is
    # not where the entry says -- happens before anything has been written.
    conf_changed = _write_the_conf(entry, server_dir)

    target = server_dir / composegen.OVERRIDE_FILE
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    # Written once, by the FIRST press. A second press would otherwise back up
    # the channel's own configuration, and a rollback would then restore a file
    # with the channel still in it.
    if not backup.exists():
        backup.write_text(before, encoding="utf-8", newline="\n")
    # The claim is written even though it equals the compose default, because a
    # default cannot be given back: the base file publishes SOAP at
    # `127.0.0.1:7878` through `${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}`,
    # so until this key exists the port is held by every install whether or not
    # it has a channel, and `roll_back()` would have nothing to change.
    composegen.write_dotenv(server_dir, {HOST_PORT_VAR: f"127.0.0.1:{operations.port}"})
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=templates_root,
        world_env=_world_env(entry, operations.enable_env),
        db_password=db_password,
        bind_label=install_bind_label(server_dir) if bind_label is None else bind_label,
    )
    if plan.override == before:
        logger.info(f"{entry.id}'s command channel was already switched on in {target.name}")
        return Enabled(path=target, changed=conf_changed)
    target.write_text(plan.override, encoding="utf-8", newline="\n")
    logger.info(f"wrote {entry.id}'s command channel into {target}")
    return Enabled(path=target, changed=True)


def _write_the_conf(entry: CatalogEntry, server_dir: Path) -> bool:
    """Patch the conf keys this tree's channel needs. `False` when it has none.

    The CMaNGOS lineage reads its settings from the file and nowhere else, so
    this is that family's whole enable. It goes through `families/conf.patch()`
    -- the same writer the install stage already uses on this same file for six
    other keys -- rather than a second implementation of `Key = value`.

    Backed up once, by the FIRST press, for the reason the override's backup is:
    a second backup would capture the channel's own configuration, and a
    rollback would then restore a file with the channel still in it.

    A conf that is not where the entry says is a refusal, never a file this
    creates. mangosd reads exactly one conf, and a stub with three SOAP keys in
    it would bring the world up with every other setting at its compiled
    default -- including the database it would then not find.
    """
    operations = entry.operations
    if operations is None or operations.enable_conf is None:
        return False
    target = server_dir / operations.enable_conf.file
    if not target.is_file():
        raise EnableRefused(
            f"{entry.name}'s command channel is switched on in {operations.enable_conf.file}, "
            f"and there is no such file under {server_dir}. Nothing was written."
        )
    before = target.read_text(encoding="utf-8")
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_text(before, encoding="utf-8", newline="\n")
    after = conf.patch(before, ConfPatch(keys=dict(operations.enable_conf.keys)), tokens={})
    if after == before:
        logger.info(f"{entry.id}'s channel keys were already in {target.name}")
        return False
    target.write_text(after, encoding="utf-8", newline="\n")
    logger.info(f"wrote {entry.id}'s command channel into {target}")
    return True


def _restore_the_conf(entry: CatalogEntry, server_dir: Path) -> bool:
    """Put the channel's OWN keys back where the press found them. `False` if none.

    An inverse patch and not a restore, which is the whole point (adversarial
    review, 2026-09-07). The backup is written by the first press and lives
    until a rollback consumes it, so it can be arbitrarily old -- that argument
    is already written down for the compose override, which is why
    `roll_back(expected=...)` exists there. Putting seventy kilobytes of
    somebody's settings back from a copy of unknown age, in order to undo four
    keys, throws away every edit made in between.

    So each key this app wrote is set back to the value the backup has for it,
    and a key the backup did not have at all is removed. Every other line is
    left exactly as it is.

    The backup is NOT deleted here: `roll_back()` unlinks it once the whole
    undo has succeeded, for the reason its own comment gives about the
    override's copy.
    """
    operations = entry.operations
    if operations is None or operations.enable_conf is None:
        return False
    target = server_dir / operations.enable_conf.file
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.is_file() or not target.is_file():
        return False
    was = _conf_values(backup.read_text(encoding="utf-8"), operations.enable_conf.keys)
    now = target.read_text(encoding="utf-8")
    put_back = {key: value for key, value in was.items() if value is not None}
    drop = [key for key, value in was.items() if value is None]
    after = conf.patch(now, ConfPatch(keys=put_back), tokens={}) if put_back else now
    if drop:
        after = _without_keys(after, drop)
    if after != now:
        target.write_text(after, encoding="utf-8", newline="\n")
    logger.info(f"put {entry.id}'s own keys in {target.name} back where the press found them")
    return True


def _conf_values(text: str, keys: Mapping[str, str]) -> dict[str, str | None]:
    """What this file said about each key: its value, or `None` if it said nothing.

    `None` is the case that makes the difference between an inverse patch and a
    guess: a key the install never had must be REMOVED on the way back, not set
    to some default this module invented.
    """
    found: dict[str, str | None] = dict.fromkeys(keys)
    for line in text.splitlines():
        stripped = line.strip()
        for key in keys:
            if stripped.startswith(key) and stripped[len(key) :].lstrip().startswith("="):
                found[key] = stripped.split("=", 1)[1].strip()
    return found


def _without_keys(text: str, keys: list[str]) -> str:
    """`text` with every line setting one of `keys` removed, and nothing else touched."""
    kept = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if any(
            stripped.startswith(key) and stripped[len(key) :].lstrip().startswith("=")
            for key in keys
        ):
            continue
        kept.append(line)
    return "".join(kept)


def roll_back(entry: CatalogEntry, server_dir: Path, *, expected: str | None = None) -> bool:
    """Undo the press: the configuration this install had, and the port back.

    Two halves, and the second is the one the box names. Restoring the
    environment alone leaves the install exactly as unstartable as it was: an
    occupied 7878 stops the CONTAINER being created, because Docker refuses to
    publish a host port something else already holds, and that happens before
    the worldserver reads a single setting. So the claim goes back to
    `127.0.0.1:0` -- any free port the daemon likes -- which is the honest
    statement of what a rolled-back channel owns: a port nobody can reach it on.

    Returns False, writing nothing, when there is no press to undo. Restoring
    "the state before" out of a backup that does not exist would put an empty
    override on top of a good one.

    `expected` is what the press wrote. Given it, the override is restored only
    if it still says exactly that -- otherwise the file has been changed since,
    and the port is released without touching it.
    """
    # The conf first: on a tree that reads no environment it IS the channel, and
    # a rollback that released the port while leaving `SOAP.Enabled = 1` behind
    # would bring the world up binding a port it had just been told to give up.
    conf_back = _restore_the_conf(entry, server_dir)

    target = server_dir / composegen.OVERRIDE_FILE
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.is_file():
        logger.info(f"nothing to roll back for {entry.id}: no {backup.name}")
        return conf_back
    now = target.read_text(encoding="utf-8") if target.is_file() else ""
    if expected is not None and now != expected:
        # Somebody -- a person, or the settings surface that owns this file --
        # has changed the override since the press. Putting the backup on top
        # of it would throw those changes away, and the review that found this
        # is right that the backup can be arbitrarily old: it is written once,
        # by the FIRST press, and lives until a rollback consumes it. The port
        # is still released, because the port claim IS this feature's and
        # giving it back is what makes the server start.
        logger.warning(
            f"{entry.id}'s override has changed since the press; releasing the port but "
            f"leaving {target.name} alone"
        )
        composegen.write_dotenv(server_dir, {HOST_PORT_VAR: RELEASED_HOST_PORT})
        return True
    target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    # The `.env` before the unlink, and the unlink last. An interruption then
    # leaves the backup on disk with the override already restored, and running
    # this again is a no-op that finishes the job -- whereas deleting the only
    # copy first and failing on `.env` would leave a restored override paired
    # with the port it cannot have and nothing to retry from.
    composegen.write_dotenv(server_dir, {HOST_PORT_VAR: RELEASED_HOST_PORT})
    backup.unlink()
    # And the conf's copy, in the same breath and for the same reason: until
    # every artifact is back, the copies are the only way to retry. Deleting
    # this one inside `_restore_the_conf()` left a failure on `.env` or the
    # override with nothing to retry FROM (adversarial review, 2026-09-07).
    _forget_the_conf_backup(entry, server_dir)
    logger.info(f"rolled {entry.id}'s command channel back and released its host port")
    return True


def _forget_the_conf_backup(entry: CatalogEntry, server_dir: Path) -> None:
    """Drop the conf's copy, once the whole rollback has succeeded."""
    operations = entry.operations
    if operations is None or operations.enable_conf is None:
        return
    target = server_dir / operations.enable_conf.file
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if backup.is_file():
        backup.unlink()


def blames_the_host_port(message: str, port: int) -> bool:
    """Does this start failure name the channel's published port?

    Two things have to be true, and neither alone is enough: the daemon says it
    could not bind, AND the port it names is ours. Matched as a whole token, so
    7878 is not found inside 78780.

    **The wording is per-daemon and was measured, not remembered.** This was
    written against Docker's older sentence --

        Bind for 127.0.0.1:7878 failed: port is already allocated

    -- and the daemon on the gate box said something with none of those words
    in it:

        failed to bind host port 127.0.0.1:7878/tcp: address already in use

    so the predicate answered no, the rollback never ran, and the gate failed
    on the clause it existed to prove. What both sentences share is the verb,
    which is what is matched now. Requiring "already in use" or "already
    allocated" as well would be a third wording to get wrong, and a bind that
    fails for some OTHER reason is still a bind of our port that failed.

    **Both facts must be on the same LINE.** Compose prints one line per
    service and this message is the whole of its output, so a bind failure for
    the database and a mention of the SOAP port somewhere else would otherwise
    read as our port failing to bind -- and roll the channel back for something
    that had nothing to do with it (adversarial review, 2026-09-07). `listen`
    is accepted beside `bind` because the userland proxy's own wording is
    "listen tcp ...".
    """
    for raw in message.replace("\\n", "\n").splitlines():
        low = raw.lower()
        if "bind" not in low and "listen" not in low:
            continue
        if re.search(rf"[:\s]{port}(?![0-9])", raw):
            return True
    return False


def _world_env(entry: CatalogEntry, extra: Mapping[str, str]) -> dict[str, str]:
    """What the install already had, plus what the channel needs.

    Merged rather than replaced: the bot population lives in this same block,
    and an install that lost it would be a different server from the one the
    user built.
    """
    native = entry.install.native
    entry_env = native.azerothcore.world_env if native is not None and native.azerothcore else {}
    return {**composegen.DEFAULT_WORLD_ENV, **entry_env, **extra}


# -- the credential file -----------------------------------------------------


CREDENTIAL_MODE = 0o600
"""Owner-only, and set by `os.open`'s mode rather than by a later `chmod`.

A chmod afterwards is a second step, and the window before it is exactly when
the file is world-readable. On Windows the mode argument is ignored, which is
why the test asserts the flags the file was CREATED with rather than reading the
mode back — a read-back test would pass there for the wrong reason.
"""


def credential_path(game: str, install_id: str, *, config_dir: Path | None = None) -> Path:
    """Where this install's channel credential lives.

    Named for the game AND the install, so two installs of one game keep two
    credentials rather than one that overwrites the other.
    """
    root = config_dir if config_dir is not None else platform.config_dir()
    return root / "credentials" / f"{game}-{install_id}.json"


def save_credential(
    verified: Verified,
    *,
    game: str,
    install_id: str,
    host: str,
    port: int,
    namespace: str,
    config_dir: Path | None = None,
) -> Path:
    """Write the credential for an account whose round trip has answered.

    `namespace` is required, not defaulted: this file records what PROVED the
    credential, so the caller has to say what proved it. A default here would be
    one tree's answer written into every other tree's file, which is the same
    inheritance `Operations.namespace` refuses at the other end.

    Takes a `Verified` and nothing else, which is how "never persist before the
    round trip answered" is enforced: the earlier states cannot be passed here.
    """
    path = credential_path(game, install_id, config_dir=config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "account": verified.account,
            "password": verified.password,
            "namespace": namespace,
            "host": host,
            "port": port,
            "verified_at": verified.at,
        },
        indent=2,
    )
    # `O_TRUNC` rather than `O_EXCL`: a rotated password has to be able to land
    # on top of the old one, and refusing that would strand an install whose
    # credential changed.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, CREDENTIAL_MODE)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload + "\n")
    logger.info(f"saved the command-channel credential for {game} to {path}")
    return path


def load_credential(
    game: str, install_id: str, *, config_dir: Path | None = None
) -> soap.Endpoint | None:
    """This install's saved endpoint, or `None` if there is not a usable one.

    Never raises. A stale or hand-edited file is a reason to set the channel up
    again, not a reason the app cannot open.
    """
    path = credential_path(game, install_id, config_dir=config_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return soap.Endpoint(
            host=str(raw["host"]),
            port=int(raw["port"]),
            account=str(raw["account"]),
            password=str(raw["password"]),
            # An empty string where the file never recorded one, which is a
            # DIFFERENT fact from recording `urn:AC`: a credential written
            # before 8.2c belongs to whatever tree it belongs to, and reading
            # it as AzerothCore's answer would be the inheritance this field
            # exists to stop. `live_channel()` fills it from the entry.
            namespace=str(raw.get("namespace", "")),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.info(f"no usable credential at {path}: {type(exc).__name__}")
        return None


# -- create, verify, persist -------------------------------------------------


def ensure(
    *,
    account: str,
    password: str,
    create: Callable[[str, str, int], object],
    channel: object,
    game: str,
    install_id: str,
    host: str,
    port: int,
    namespace: str,
    config_dir: Path | None = None,
    state: State | None = None,
    gm_level: int = 3,
) -> State:
    """Move the setup one step: create if it must, verify, and persist if verified.

    One step per call rather than a loop, because the interesting failures are
    between the steps and a loop hides them. The state it returns is what the
    caller keeps and hands back next time — which is what stops a failed verify
    turning into a second account.

    `create` is the install's own account seam (the SRP6 row path this app
    already has), and it is called only from `Idle`.
    """
    current = state if state is not None else Idle()
    # `Refused` is here for the same reason `Verified` and `GaveUp` are, and for
    # one more: it carries an account that EXISTS, so the try-again branch below
    # would call methods on it that only an un-created state has. The repair
    # path is what a refused credential is for.
    if isinstance(current, Verified | Refused | GaveUp):
        return current
    if isinstance(current, Idle):
        create(account, password, gm_level)
        current = current.created(account, password)

    answer = channel.send(commands.SERVER_INFO)  # type: ignore[attr-defined]
    if getattr(answer, "denied", False):
        # The server was asked and SAID NO, which is a different fact from "it
        # has not answered yet" and has a different way out. Found by 8.2c's
        # gate on m910q: TBC's world takes minutes to load, so the three tries
        # after a Start can all miss it and the setup gives up with nothing
        # saved -- correctly. The run after that is the trap. With no credential
        # on disk it starts from `Idle`, generates a NEW password and calls
        # `create`, which by design keeps the password of an account that
        # already exists; every round trip from then on is a 401, and a 401
        # counted as silence gives up again, on every run, with no way out but
        # hand-written SQL. A rejection means the account is KNOWN, which is
        # exactly what the repair path is for.
        logger.info(f"the command channel for {game} was rejected; it can be repaired")
        return Refused(
            account=current.account,
            password=current.password,
            reason=(
                "the server did not accept the password this app generated for its own "
                "account. The account exists, so its password can be reset for it."
            ),
        )
    if getattr(answer, "outcome", "") != "yes":
        logger.info(f"the command channel for {game} did not answer yet; not saving anything")
        return current.verify_failed()

    verified = current.verified()
    save_credential(
        verified,
        game=game,
        install_id=install_id,
        host=host,
        port=port,
        namespace=namespace,
        config_dir=config_dir,
    )
    return verified


def refused(state: Verified | Refused, *, reason: str) -> Refused:
    """Downgrade a credential the server has rejected.

    Only a definite rejection may come here. A transport failure means the
    server was not asked, and turning "I could not reach it" into "your
    credential is wrong" would offer the user a repair for a problem that is
    somebody else's -- `channel.Answer.indeterminate` is what tells them apart,
    and the caller is what reads it.
    """
    return Refused(account=state.account, password=state.password, reason=reason)


def repair(
    *,
    state: Refused,
    create: Callable[[str, str, int], object],
    reset: Callable[[str, str], object],
    channel_for: Callable[[str], object],
    game: str,
    install_id: str,
    host: str,
    port: int,
    namespace: str,
    config_dir: Path | None = None,
    gm_level: int = 3,
    now: Callable[[], str] = now_utc,
) -> State:
    """Give the account this install already has a password that works.

    `create` is taken and never called. It is here so the type of this function
    says what it does not do, and so the test that proves it can hand in a seam
    that raises: a repair that quietly minted a second account would look
    identical from the outside -- the channel would work -- while leaving
    another GM-level-3 row in the user's auth database every time a credential
    went stale.

    A round trip still decides. The password is already changed in the database
    by the time it is tried, which is exactly the moment it is tempting to
    write the credential down anyway; a credential that has not answered is
    what this app refuses to keep.
    """
    _ = create, gm_level
    password = generate_password()
    reset(state.account, password)
    # Built from the password that was just written, not before it: a channel
    # made ahead of the reset carries the credential the server has already
    # refused, and would prove nothing while looking like a repair that failed.
    channel = channel_for(password)
    answer = channel.send(commands.SERVER_INFO)  # type: ignore[attr-defined]
    if getattr(answer, "outcome", "") != "yes":
        logger.info(f"{game}: the reset password did not answer either; nothing saved")
        return Refused(
            account=state.account,
            password=password,
            reason=(
                "the account's password was reset and the server still did not answer, "
                "so nothing was saved"
            ),
        )
    verified = Verified(account=state.account, password=password, at=now())
    save_credential(
        verified,
        game=game,
        install_id=install_id,
        host=host,
        port=port,
        namespace=namespace,
        config_dir=config_dir,
    )
    return verified


def verified_at(game: str, install_id: str, *, config_dir: Path | None = None) -> str | None:
    """When this install's saved credential was proved, if it says.

    Read separately from `load_credential()` so that function keeps returning
    exactly an endpoint: everything that talks to the server wants the endpoint
    and nothing else, and only the tab's sentence wants this.
    """
    path = credential_path(game, install_id, config_dir=config_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug(f"no verified time at {path}: {type(exc).__name__}")
        return None
    at = raw.get("verified_at") if isinstance(raw, dict) else None
    return at if isinstance(at, str) and at else None


# -- what the tab is handed --------------------------------------------------


class InstallChannel:
    """One install's channel setup, as the Server tab sees it (8.2a).

    Two questions belong together here: pressing enable, and asking where the
    setup has got to. They are the same state machine from two sides.

    The state survives an app restart through the credential file and nothing
    else. A saved credential means a round trip answered — that is the only way
    one gets written — so finding one is `Verified` and finding none is `Idle`.
    Nothing is inferred from a conf file: a written setting is not a working
    channel, which is the distinction this whole box exists to keep.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        templates_root: Path,
        install_id: str,
        create: Callable[[str, str, int], object],
        channel_for: Callable[[soap.Endpoint], object],
        reset: Callable[[str, str], object] | None = None,
        config_dir: Path | None = None,
        db_password: str | None = None,
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self.templates_root = templates_root
        self.install_id = install_id
        self._create = create
        self._reset = reset
        self._channel_for = channel_for
        self._config_dir = config_dir
        self._db_password = db_password
        self._state: State = self._from_disk()

    def _from_disk(self) -> State:
        saved = load_credential(self.entry.id, self.install_id, config_dir=self._config_dir)
        if saved is None:
            return Idle()
        return Verified(
            account=saved.account,
            password=saved.password,
            at=verified_at(self.entry.id, self.install_id, config_dir=self._config_dir),
        )

    def _endpoint(self, account: str, password: str) -> soap.Endpoint:
        operations = self.entry.operations
        # An attach entry never reaches here -- no factory wires an
        # `InstallChannel` for a tree whose channel is the console (8.2e), and
        # `enable()` refuses one outright. The fallbacks are what a bug would
        # produce if one ever did: port 0 fails to connect loudly, rather than
        # quietly addressing something else.
        port = operations.port or 0 if operations is not None else 0
        return soap.Endpoint(
            host="127.0.0.1",
            port=port,
            account=account,
            password=password,
            # This tree's own, never the field default: TBC answers
            # `urn:MaNGOS` and the wrong namespace fails as though the world
            # were still loading (measured, m910q 2026-09-07).
            namespace=(operations.namespace or "urn:AC") if operations is not None else "urn:AC",
        )

    def check(self) -> State:
        """Ask whether the saved credential still works, and keep the answer.

        Only the SERVER rejecting this credential downgrades it, which is what
        `Answer.denied` names. Everything else -- a refused connection, a
        socket error, a reply nothing could parse, a GM level too low -- leaves
        the credential exactly as it was.

        The first version read "not yes, and not a timeout", and an adversarial
        review found what that costs: every one of those failures arrives as
        `unknown` with `indeterminate` false, so opening the tab against a
        stopped server read as "your password is wrong" and offered to rotate a
        GM account's password to fix a container that was not running.
        """
        state = self._state
        if not isinstance(state, Verified):
            return state
        channel = self._channel_for(self._endpoint(state.account, state.password))
        answer = channel.send(commands.SERVER_INFO)  # type: ignore[attr-defined]
        if getattr(answer, "outcome", "") == "yes":
            return state
        if not getattr(answer, "denied", False):
            logger.info(f"{self.entry.id}: the channel did not answer; the credential stands")
            return state
        self._state = refused(
            state,
            reason=(
                "the server did not accept the saved password. Nothing else about this "
                "install has changed; the account's password can be reset for it."
            ),
        )
        return self._state

    def repair(self) -> State:
        """Give the account this install already has a password that works.

        Does nothing unless the credential has actually been refused: this is a
        button, and pressing it against a working channel would break one that
        answers for as long as the reset takes to prove.
        """
        state = self._state
        if not isinstance(state, Refused):
            return state
        if self._reset is None:
            return GaveUp(
                account=state.account,
                reason=f"{self.entry.id} has no way to reset its own account's password yet",
            )
        operations = self.entry.operations
        endpoint = self._endpoint(state.account, "")
        self._state = repair(
            state=state,
            create=self._create,
            reset=self._reset,
            channel_for=lambda pw: self._channel_for(self._endpoint(state.account, pw)),
            game=self.entry.id,
            install_id=self.install_id,
            host=endpoint.host,
            port=endpoint.port,
            namespace=endpoint.namespace,
            config_dir=self._config_dir,
            gm_level=(operations.gm_level if operations is not None else None) or 3,
        )
        return self._state

    def settle(self) -> State:
        """Move the channel to wherever the live server says it is.

        One entry point for the tab, because the right thing to do depends on
        where the setup already is and the tab should not be the thing that
        knows: an install that has never been set up gets an account and a
        round trip, one that has a credential gets that credential checked, and
        one that has given up is left alone until a person acts.
        """
        # Two arms and not three: a state that gave up needs no arm here,
        # because `ensure()` is the latch and returns it untouched. A third one
        # was written, and a mutation that deleted it changed no observable
        # behaviour -- which is the definition of a guard that guards nothing.
        if isinstance(self._state, Verified):
            return self.check()
        return self.prove()

    def live_channel(self) -> object | None:
        """A channel on the saved credential, or None if there is not one yet.

        The credential file is the only source, and that is deliberate: it is
        written only after a round trip answered, so a channel handed out here
        is one that has worked at least once. A feature asking for it while the
        setup is `Idle` gets `None` and says so, rather than getting a channel
        built on a password nothing has proved.
        """
        saved = load_credential(self.entry.id, self.install_id, config_dir=self._config_dir)
        if saved is None:
            return None
        # One authority, and it is the round trip (adversarial review,
        # 2026-09-07). A namespace in this file is there because a real round
        # trip answered through it; the catalog is a claim about the tree. So
        # the file wins where it has an answer, the catalog bootstraps a file
        # that has none -- one written before 8.2c -- and a disagreement is
        # said out loud instead of being resolved in silence, because the
        # failure it would otherwise produce is HTTP 500, which looks exactly
        # like a world that has not finished loading.
        operations = self.entry.operations
        claimed = operations.namespace if operations is not None else ""
        if not saved.namespace:
            saved = replace(saved, namespace=claimed or "urn:AC")
        elif claimed and saved.namespace != claimed:
            logger.warning(
                f"{self.entry.id}'s saved credential was proved with namespace "
                f"{saved.namespace!r} and the catalog now says {claimed!r}; using the one "
                "that worked. If the channel stops answering, delete the credential and "
                "press the enable again."
            )
        return self._channel_for(saved)

    def roll_back(self) -> bool:
        """Undo this install's own press, and give its host port back.

        Hands `roll_back()` the text the press would write NOW, so a file that
        has been changed since is left alone rather than overwritten from a
        backup that may be arbitrarily old.

        Rendered with the install's bind label, as the press renders it (T102).
        Compared without one, an enforcing host's channel-on override -- `:z`
        on its bind, as the install's renderer writes it -- read as "edited
        since", so only the port was released and the channel stuck on
        (measured on `yulon-fedora`, 2026-09-24).

        And, on such a host, the unlabelled text as well: that is what the press
        wrote before T102, so it is what an install upgraded from then has on
        disk. Accepting only the labelled text would strand exactly those
        installs in the same stuck state from the other side. The two differ
        by the label alone, so a file a person has edited still matches
        neither and is still left alone.
        """
        operations = self.entry.operations
        expected: str | None = None
        if operations is not None:
            label = install_bind_label(self.server_dir)
            try:
                texts = [
                    composegen.render(
                        self.entry,
                        self.server_dir,
                        templates_root=self.templates_root,
                        world_env=_world_env(self.entry, operations.enable_env),
                        db_password=self._db_password,
                        bind_label=each,
                    ).override
                    for each in dict.fromkeys((label, ""))
                ]
            except Exception as exc:  # noqa: BLE001 - an unrenderable plan is not a reason to stop
                logger.info(f"could not re-render {self.entry.id}'s override to compare: {exc}")
            else:
                target = self.server_dir / composegen.OVERRIDE_FILE
                now = target.read_text(encoding="utf-8") if target.is_file() else None
                expected = now if now in texts else texts[0]
        return roll_back(self.entry, self.server_dir, expected=expected)

    def enable(self, *, world_running: bool) -> Enabled:
        """Write the channel on. Refuses while the world is running."""
        return enable(
            self.entry,
            self.server_dir,
            templates_root=self.templates_root,
            world_running=world_running,
            db_password=self._db_password,
        )

    def setup_state(self) -> State:
        """Where the setup has got to, without asking the server anything."""
        return self._state

    def prove(self) -> State:
        """Move the setup one step against the live server, and keep the answer.

        Called after a start, not on a timer: it creates an account the first
        time and then asks the server one real question. `ensure()` is what
        makes a second call re-verify rather than re-create.
        """
        operations = self.entry.operations
        if operations is None:
            return self._state
        account = account_name(self.install_id)
        password = (
            self._state.password
            if isinstance(self._state, Pending | Verified)
            else generate_password()
        )
        # The one seam, not a second construction: this line used to build its
        # own endpoint and kept `Endpoint.namespace`'s default, so TBC was sent
        # `urn:AC`, answered HTTP 500, and read as a world that had not finished
        # loading. `live_channel()` had been fixed and this had not.
        endpoint = self._endpoint(account, password)
        self._state = ensure(
            account=account,
            password=password,
            create=self._create,
            channel=self._channel_for(endpoint),
            game=self.entry.id,
            install_id=self.install_id,
            host=endpoint.host,
            port=endpoint.port,
            namespace=endpoint.namespace,
            config_dir=self._config_dir,
            state=self._state,
            gm_level=operations.gm_level or 3,
        )
        return self._state
