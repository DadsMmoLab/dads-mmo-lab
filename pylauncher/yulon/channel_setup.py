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

The one thing kept before that is the `Pending` record (T138), in a file of its
own that nothing but this setup reads: the account row and the password it was
created with, so a launch after the app closed mid-settle re-verifies the row
it made instead of minting a password the row will never have. It is not a
credential -- `live_channel()` and every feature still see only the verified
file -- and `save_credential()` removes it the moment one is written.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from yulon import bot_population, commands, platform, soap, winacl
from yulon.catalog import bot_count, composegen, time_zone
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
                    "so it is not in use and it is over to you"
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
    plain: bool = False
    """True when `reason` is the whole line, not why a server said no (T386).

    The account's password is lost, or Repair could not reach the database:
    neither is the server refusing anything, so the tab does not say "refused".
    """


LOST_PASSWORD = "Yu'lon lost the password for its own server account. Repair sets a new one."
"""The line for this app's own account on the server with no password this machine keeps (T386).

A fresh data folder, a reinstall of Yu'lon or a new machine against a server
that already has the account: `create` keeps the row's own password, as it
must, so the one just minted can never prove it.
"""

GONE_ACCOUNT = "Yu'lon's own server account is not in the database any more. Repair makes it again."
"""The line for this app's own account missing from a server whose database was re-imported (T423).

A saved password for a row that is not there: the channel said it was verified
because nothing had asked the database since. Repair makes the row again.
"""

DATABASE_UNREACHABLE = (
    "Repair could not reach the server's database, so nothing was changed. "
    "Start the server, then press Repair again."
)


def lost(account: str, password: str = "") -> Refused:
    """This app's own account exists and its password is not one this machine has (T386)."""
    return Refused(account=account, password=password, reason=LOST_PASSWORD, plain=True)


def gone(account: str) -> Refused:
    """This app's own account was proved once and its row is not in the database now (T423)."""
    return Refused(account=account, password="", reason=GONE_ACCOUNT, plain=True)


@dataclass(frozen=True)
class GaveUp:
    """This run will not try again; the reason is for a person to read."""

    account: str
    reason: str


State = Idle | Pending | Verified | Refused | GaveUp


# -- the enable press --------------------------------------------------------


HOST_PORT_VAR = composegen.CHANNEL_PORT_VAR
"""The `.env` key the base compose reads for SOAP's whole host binding.

Named in `docker-compose.yml` beside the mapping itself: the value carries the
address AND the port, because a literal `127.0.0.1:` prefix on the mapping would
render `127.0.0.1:127.0.0.1:7878:7878` once this key is set.
"""

RELEASED_HOST_PORT = composegen.CHANNEL_PORT_RELEASED
"""What a rolled-back channel claims: loopback, and whatever port is free.

`composegen.channel_is_on()` reads it back: a released claim is a rollback (T101).
"""

BACKUP_SUFFIX = composegen.CHANNEL_BACKUP_SUFFIX
"""The override as it was before the first press, kept beside it.

Defined in `composegen`, which every writer of the override can import (T101).
"""


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
    feeds it -- for a press whose installed override cannot say which label it
    carries (`_label_on_disk()` comes first; T102). Both are looked up on the call, not
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


def _label_on_disk(override: Path) -> str | None:
    """The label the installed override's host binds carry, or None if it cannot say.

    `":z"` when every `- ./` bind carries it, `""` when none does. None when
    there is no file, the file is not this engine's (no generated marker), or
    it has no host bind at all -- a CMaNGOS override has none until a time
    zone is set -- and the caller then asks the host. Binds that disagree are
    no install's rendering, so that is a refusal rather than a guess.

    `composegen.bind_label_of()` since T171, the fold both docstrings asked
    for: the time zone's read-only bind is `:ro,z`, which a trailing-`:z` test
    read as unlabelled, so an enforcing CMaNGOS install with a zone set would
    have re-rendered its bind with no label and lost the zone.
    """
    if not override.is_file() or not composegen.is_ours(override):
        return None
    try:
        return composegen.bind_label_of(override.read_text(encoding="utf-8"))
    except composegen.MixedBindLabels:
        pass
    raise EnableRefused(
        f"{override.name} has some host folders labelled for SELinux (`:z`) and some not, "
        "which is not how Yu'lon writes it, so the command channel was not turned on and "
        "nothing was written. Give every `- ./` line the same ending, or none, and press "
        "this again."
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

    It is rewritten WITH the install's bind label: `bind_label` if given,
    else the label the installed override's binds already carry
    (`_label_on_disk()`), else the host's answer through
    `install_bind_label()`. The file comes before the probe because the probe
    can be wrong at press time -- `getenforce` failing, or a host briefly
    permissive -- and the file is what the install actually wrote, which is
    also what the rollback recognises. Until T102 it was
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

    target = server_dir / composegen.OVERRIDE_FILE
    # Before anything is written, so a mixed file is refused with nothing touched.
    if bind_label is None:
        bind_label = _label_on_disk(target)
    if bind_label is None:
        bind_label = install_bind_label(server_dir)

    # The conf half first, and deliberately: a tree that reads no environment is
    # not switched on by the override at all, and the override's only job there
    # is publishing the port. Doing it first means a refusal -- a conf that is
    # not where the entry says -- happens before anything has been written.
    conf_changed = _write_the_conf(entry, server_dir)

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
        # The player's bot count, off the file this replaces (T117): the press
        # rendered the catalog's 500 until then.
        world_env=bot_count.world_env(entry, server_dir, _world_env(entry, operations.enable_env)),
        db_password=db_password,
        bind_label=bind_label,
    )
    if plan.override == before:
        logger.info(f"{entry.id}'s command channel was already switched on in {target.name}")
        return Enabled(path=target, changed=conf_changed)
    # T171: the zone the new file names, its file copied before a bind names it.
    try:
        time_zone.place(entry, server_dir, plan.override)
    except (OSError, time_zone.TimeZoneError) as exc:
        raise EnableRefused(
            f"the server's time zone file could not be copied ({exc}), so the command channel "
            "was not turned on and the override was not written."
        ) from exc
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
    target.write_text(
        _keep_the_bot_count(entry, now, backup.read_text(encoding="utf-8")),
        encoding="utf-8",
        newline="\n",
    )
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


def _keep_the_bot_count(entry: CatalogEntry, now: str, restored: str) -> str:
    """The pre-press file to put back, with the bot count (T117) and zone (T171) `now` holds.

    The backup is the override before the FIRST press, so it holds the count
    from then: one changed on the Bots tab while the channel was on would go
    back with the channel. Only the two values move; a backup without the two
    lines, or a `now` without a usable pair, is put back as it was.
    """
    restored = _keep_the_time_zone(entry, now, restored)
    kept = bot_count.in_override_text(now, entry)
    if not kept:
        return restored
    try:
        return bot_population.patch_env(restored, entry, composegen.OVERRIDE_FILE, kept)
    except bot_population.BotCountError:
        return restored


def _keep_the_time_zone(entry: CatalogEntry, now: str, restored: str) -> str:
    """The pre-press file with the time zone `now` holds laid over it (T171).

    For the bot count's reason above: a zone set on the Tuning tab while the
    channel was on would otherwise go back with the channel. Only the `TZ`
    lines move; a backup whose shape takes none is put back as it was.
    """
    try:
        label = composegen.bind_label_of(now) or ""
        return time_zone.lay_over(restored, entry, time_zone.carried(now, entry), label=label)
    except (time_zone.TimeZoneError, composegen.MixedBindLabels):
        return restored


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

**So the mode is a POSIX fact, not a Windows one.** There the file takes the ACL
of the folder it is created in, and since T151 that folder carries its own
protected DACL -- this account, SYSTEM and Administrators, nothing inherited from
`%APPDATA%` -- set by `winacl.secure_folder()` before the file is created. Until
T151 it was the profile's inherited ACL, private by default and not in a profile
someone loosened.
"""

_ON_WINDOWS = os.name == "nt"
"""Where a rename can be refused for a reader, and a folder cannot be fsync'd (T138)."""

REPLACE_TRIES = 5
"""How many times a refused rename onto a credential is tried, on Windows only (T138).

Windows refuses `os.replace` onto a file another handle holds open without
delete-sharing -- a check reading the credential while a settle promotes it, or
a virus scanner looking at a file that was just written. That lasts
milliseconds, so a few short tries clear it, and a bound keeps a file that is
really locked from hanging the settle. POSIX renames over a reader, so there a
refusal is real and is not tried again.
"""

REPLACE_PAUSE = 0.05
"""Seconds between those tries: 0.2 s at most before the refusal is reported."""


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
    # Replaced rather than refused: a rotated password has to be able to land
    # on top of the old one, and refusing that would strand an install whose
    # credential changed.
    _write_private(path, payload + "\n")
    logger.info(f"saved the command-channel credential for {game} to {path}")
    # Proved now, so the record of the row before it was proved is spent. Both
    # `ensure()` and `repair()` come through here, which is what makes a
    # repaired channel drop it too (T138).
    forget_pending(game, install_id, config_dir=config_dir)
    return path


def _write_private(path: Path, text: str) -> None:
    """Put `text` at `path` whole or not at all, and owner-only (T138, T151).

    Owner-only by the creation flags on POSIX, for `CREDENTIAL_MODE`'s reason;
    on Windows by the folder's own DACL, checked and if need be set before the
    sibling is created (`winacl.secure_folder`), so the sibling inherits it and
    the rename keeps it. A folder Windows will not narrow is a warning, not a
    refusal: the password is already the server's, and not saving it breaks the
    channel (`winacl`'s docstring). Whole by
    writing a sibling, forcing it to disk, renaming it over `path` and then
    forcing the rename itself to disk: a power cut part-way leaves the file
    that was there, never a truncated one that reads as no credential and sends
    the next launch to mint a password.

    **The sibling's name is this write's own.** It was one shared `<name>.tmp`
    removed before every write, and a check and a settle writing at once -- or
    two app windows -- then removed each other's file in the middle of a write
    (Codex review of T138). Now each write makes a fresh name `O_EXCL`, removes
    only that name if it fails, and never touches another writer's. The name
    ends `.tmp`, so nothing that lists `*.json` in these folders reads a
    leftover as a credential.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Before the sibling is created, so it is born under the folder's owner-only
    # DACL on Windows and the rename carries that DACL onto `path` (T151).
    winacl.secure_folder(path.parent)
    temp = _temporary_path(path)
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CREDENTIAL_MODE)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _sync_folder(path.parent)


def _temporary_path(path: Path) -> Path:
    """A name beside `path` that no other write will pick: hidden, random, `.tmp`."""
    return path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")


def _replace(temp: Path, path: Path) -> None:
    """`os.replace`, tried again a few times where Windows refuses it for a reader."""
    _tried_again_on_windows(lambda: os.replace(temp, path))


def _tried_again_on_windows(action: Callable[[], object]) -> None:
    """Run `action`; on Windows, try a refused one again up to `REPLACE_TRIES` times.

    Only `PermissionError`, which is how Windows refuses a rename or a delete of
    a file another handle holds; every other failure, and the last refusal,
    reaches the caller.
    """
    tries = REPLACE_TRIES if _ON_WINDOWS else 1
    for attempt in range(1, tries + 1):
        try:
            action()
            return
        except PermissionError:
            if attempt == tries:
                raise
            time.sleep(REPLACE_PAUSE)


def _sync_folder(folder: Path) -> None:
    """Make a rename in `folder` survive a power cut, where the OS lets this app ask.

    A rename is an entry in the folder, and on POSIX the entry is not durable
    until the folder itself is fsync'd. Windows is skipped: `os.open` cannot
    open a directory there (that needs `FILE_FLAG_BACKUP_SEMANTICS`), and NTFS
    journals the rename's metadata itself. A folder that cannot be synced
    costs durability, not the write, so it is said and not raised.
    """
    if _ON_WINDOWS:
        return
    try:
        handle = os.open(folder, os.O_RDONLY)
    except OSError as exc:
        logger.info(f"could not open {folder} to sync it: {type(exc).__name__}")
        return
    try:
        os.fsync(handle)
    except OSError as exc:
        logger.info(f"could not sync {folder}: {type(exc).__name__}")
    finally:
        os.close(handle)


def pending_path(game: str, install_id: str, *, config_dir: Path | None = None) -> Path:
    """Where this install's un-proved channel account is recorded (T138).

    A folder of its own under `credentials/`, not a second name beside the
    verified file: everything that lists `credentials/*.json` reads each one as
    a credential, and this is not one.
    """
    folder = credential_path(game, install_id, config_dir=config_dir).parent / "pending"
    return folder / f"{game}-{install_id}.json"


def save_pending(
    pending: Pending, *, game: str, install_id: str, config_dir: Path | None = None
) -> Path:
    """Record a row that exists and has not answered yet, for a later launch (T138).

    Takes a `Pending` and nothing else: only `create` returning makes one, so a
    record on disk means the row is there with this password. That is what lets
    a later launch re-verify it instead of creating -- the latch, across a
    close. Written with the verified credential's care, because it is the same
    password a minute earlier: through `_write_private()`, owner-only on POSIX
    and under its folder's owner-only DACL on Windows (T151), exactly as that
    file is.
    """
    path = pending_path(game, install_id, config_dir=config_dir)
    payload = json.dumps({"account": pending.account, "password": pending.password}, indent=2)
    _write_private(path, payload + "\n")
    logger.info(f"kept the un-proved command-channel account for {game} in {path}")
    return path


def load_pending(game: str, install_id: str, *, config_dir: Path | None = None) -> Pending | None:
    """The row an earlier run created and did not prove, or `None`. Never raises.

    Its `tries` start again at nought: they count this run's round trips, and a
    launch is a new run.
    """
    path = pending_path(game, install_id, config_dir=config_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Pending(account=str(raw["account"]), password=str(raw["password"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug(f"no pending channel account at {path}: {type(exc).__name__}")
        return None


def forget_pending(game: str, install_id: str, *, config_dir: Path | None = None) -> None:
    """Drop the un-proved record once a verified credential stands. Never raises.

    The lenient half of `remove_pending()`, for the promotion. A record that
    outlives this -- a locked file on Windows, a crash between the two writes
    -- is harmless there: `InstallChannel` reads the verified file first, and
    the next save removes it.
    """
    try:
        remove_pending(game, install_id, config_dir=config_dir)
    except OSError as exc:
        path = pending_path(game, install_id, config_dir=config_dir)
        logger.info(f"could not remove {path}: {type(exc).__name__}")


def remove_pending(game: str, install_id: str, *, config_dir: Path | None = None) -> None:
    """Remove the un-proved record for good, or raise saying why it is still there.

    The strict half, for a purge that has just removed the database the record
    names a row in (T138). There a record left behind is not harmless: a
    reinstall to the same folder reads it, trusts a row that is gone, and is
    refused for good. So nothing is swallowed. No record is the ordinary case
    and is fine; a Windows refusal gets the same few tries a rename does; any
    other failure reaches the caller, which says it. On POSIX the folder is
    synced after the removal, for the reason `_sync_folder()` gives: a
    removal is an entry in the folder, and a power cut can bring back an
    unsynced one.
    """
    path = pending_path(game, install_id, config_dir=config_dir)
    try:
        # A call, not `path.unlink` handed over, so the write ledger's walk sees it.
        _tried_again_on_windows(lambda: path.unlink())
    except FileNotFoundError:
        return
    _sync_folder(path.parent)


def _forget_credential(game: str, install_id: str, *, config_dir: Path | None = None) -> None:
    """Remove a saved credential a Repair has just made wrong. Never raises (T386).

    One left behind -- a locked file on Windows -- costs a later launch a
    refusal and a second Repair, which is what this is here to save; it is not
    a reason to fail a Repair whose reset has already landed.
    """
    path = credential_path(game, install_id, config_dir=config_dir)
    try:
        # A call, not `path.unlink` handed over, so the write ledger's walk sees it.
        _tried_again_on_windows(lambda: path.unlink())
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning(f"could not remove the replaced credential {path}: {type(exc).__name__}")
        return
    _sync_folder(path.parent)


def load_credential(
    game: str, install_id: str, *, config_dir: Path | None = None
) -> soap.Endpoint | None:
    """This install's saved endpoint, or `None` if there is not a usable one.

    Never raises. A stale or hand-edited file is a reason to set the channel up
    again, not a reason the app cannot open.
    """
    path = credential_path(game, install_id, config_dir=config_dir)
    # A credential is rewritten only on a rotation, so one saved before T151
    # gets its folder's owner-only DACL here, on the read every launch makes --
    # asked once a launch, since `live_channel()` reads it on every GM press.
    winacl.secure_folder(path.parent, reading=True)
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

    One app per machine is assumed. Two Yu'lon windows settling the same
    install at once can each mint a password from `Idle`, and the second
    pending record then overwrites the first; the app has no single-instance
    guard yet, which is T152.
    """
    current = state if state is not None else Idle()
    # `Refused` is here for the same reason `Verified` and `GaveUp` are, and for
    # one more: it carries an account that EXISTS, so the try-again branch below
    # would call methods on it that only an un-created state has. The repair
    # path is what a refused credential is for.
    if isinstance(current, Verified | Refused | GaveUp):
        return current
    if isinstance(current, Idle):
        made = create(account, password, gm_level)
        if getattr(made, "created", None) is False:
            # The row was already there, and `create` kept its password, as it
            # must (T386). The one minted above can never prove it, so no round
            # trip is needed to know that and nothing is kept as pending: the
            # way out is a reset of this app's own row, which is Repair.
            logger.info(f"{account} already exists with a password this machine does not have")
            return lost(account, password)
        current = current.created(account, password)
        # After `create` and not before it: a record on disk says the row is
        # there, which is what lets a later launch re-verify rather than
        # create (T138). Until then the password lived only in this run, and
        # closing the app inside the ~40 s the world takes to answer SOAP lost
        # it -- the next launch minted another, `create` kept the row's first,
        # and every round trip was a 401. A record that cannot be written costs
        # only that later launch; this run still holds the password, so it is
        # said and not raised.
        try:
            save_pending(current, game=game, install_id=install_id, config_dir=config_dir)
        except OSError as exc:
            logger.warning(
                f"could not keep the un-proved command-channel account for {game} "
                f"({type(exc).__name__}); closing Yu'lon before it is proved will need a repair"
            )

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
    try:
        save_credential(
            verified,
            game=game,
            install_id=install_id,
            host=host,
            port=port,
            namespace=namespace,
            config_dir=config_dir,
        )
    except OSError as exc:
        # Proved, and the file would not land -- on Windows a reader holding
        # it past `REPLACE_TRIES`, anywhere a full disk. Failing the settle
        # would throw the answer away with nothing said; staying `Pending`
        # keeps the password in this run and its record on disk, so the next
        # ask or the next launch promotes it. No try is spent: the server
        # answered (T138).
        logger.warning(
            f"the command channel for {game} answered, but its credential could not save "
            f"({type(exc).__name__}); it stays pending and is saved at the next ask"
        )
        return current
    return verified


def _row_is_gone(exists: Callable[[str], bool] | None, account: str) -> bool:
    """True only when the database was asked and said this account is not there (T423)."""
    if exists is None:
        return False
    try:
        return not exists(account)
    except Exception as exc:  # noqa: BLE001 - a look that cannot be made says nothing
        logger.info(f"could not look for {account}: {type(exc).__name__}")
        return False


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
    exists: Callable[[str], bool] | None = None,
    now: Callable[[], str] = now_utc,
) -> State:
    """Give the account this install already has a password that works.

    `create` is called only when `exists` says this app's own row is not there
    (T423: a database Repair re-imported the server and took it with it). A
    repair that quietly minted a second account would look identical from the
    outside -- the channel would work -- while leaving another GM-level-3 row
    in the user's auth database every time a credential went stale, so a row
    that is there is reset and never made again, and a database that cannot
    say is treated as one that has the row.

    A round trip still decides. The password is already changed in the database
    by the time it is tried, which is exactly the moment it is tempting to
    write the credential down anyway; a credential that has not answered is
    what this app refuses to keep.
    """
    password = generate_password()
    try:
        if _row_is_gone(exists, state.account):
            create(state.account, password, gm_level)
        else:
            reset(state.account, password)
    except Exception as exc:  # noqa: BLE001 - every way the write fails is the same sentence
        # The reset is a write to the auth database, and a database that is
        # not running is the ordinary reason it fails (T386). Nothing changed,
        # so the state is the one the press found, with a line that says what
        # to do; the detail goes to the log, never onto the line.
        logger.info(f"{game}: the reset of {state.account} did not land: {type(exc).__name__}")
        return Refused(
            account=state.account,
            password=state.password,
            reason=DATABASE_UNREACHABLE,
            plain=True,
        )
    # The row has this password now, whatever the server says next, so it is
    # kept the way a fresh account's is (T138, T386): a world that is still
    # loading proves it at the next ask, and closing Yu'lon first loses nothing.
    pending = Pending(account=state.account, password=password)
    try:
        save_pending(pending, game=game, install_id=install_id, config_dir=config_dir)
    except OSError as exc:
        logger.warning(
            f"could not keep the reset command-channel account for {game} "
            f"({type(exc).__name__}); closing Yu'lon before it is proved will need a repair"
        )
    # And the saved credential, when the press found one, is wrong from now on:
    # left on disk it is read ahead of the record above, so the next launch
    # would call a password the row no longer has "verified" and need a
    # second Repair once the world answered.
    _forget_credential(game, install_id, config_dir=config_dir)
    # Built from the password that was just written, not before it: a channel
    # made ahead of the reset carries the credential the server has already
    # refused, and would prove nothing while looking like a repair that failed.
    channel = channel_for(password)
    answer = channel.send(commands.SERVER_INFO)  # type: ignore[attr-defined]
    if getattr(answer, "denied", False):
        logger.info(f"{game}: the reset password was refused too; it is kept as not proved")
        return Refused(
            account=state.account,
            password=password,
            reason=(
                "the account's password was reset and the server still did not accept it, "
                "so the channel is not working yet"
            ),
        )
    if getattr(answer, "outcome", "") != "yes":
        logger.info(f"{game}: the reset password has not answered yet; it waits to be proved")
        return pending
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

    The state survives an app restart through the credential file and the
    pending record, and nothing else. A saved credential means a round trip
    answered — that is the only way one gets written — so finding one is
    `Verified`. Without one, a pending record means an earlier run created the
    row and closed before it was proved, so that is `Pending` with the row's
    own password (T138); finding neither is `Idle`. Nothing is inferred from a
    conf file: a written setting is not a working channel, which is the
    distinction this whole box exists to keep.
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
        exists: Callable[[str], bool] | None = None,
        config_dir: Path | None = None,
        db_password: str | Callable[[], str] | None = None,
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self.templates_root = templates_root
        self.install_id = install_id
        self._create = create
        self._reset = reset
        # A read of the auth database: is this app's own account there (T386)?
        # Asked by `check()` with nothing saved, so a tab or a Refresh can offer
        # Repair for a lost password without writing a row.
        self._exists = exists
        self._channel_for = channel_for
        self._config_dir = config_dir
        # A reader rather than the value for a server inside a WSL distro
        # (T133, `apply.RootPassword`): its folder is read at the press that
        # renders, not when the tab is built.
        self._db_password = db_password
        self._state: State = self._from_disk()
        # How many `prove()`s are running now. A Start's or an install's settle
        # runs on a worker while a Refresh or a tab's opening look can run on
        # another, and the look from `Idle` must not judge a row mid-settle.
        self._settles = 0
        self.channel_is_off = False
        """Set by `check()`, `settle()` and `repair()`: waiting, and the files say off (T423)."""

    def _password(self) -> str | None:
        """The install's database password, read now if it was handed over as a reader."""
        given = self._db_password
        return given() if callable(given) else given

    def _from_disk(self) -> State:
        saved = load_credential(self.entry.id, self.install_id, config_dir=self._config_dir)
        if saved is None:
            # The verified file first: a crash between writing it and removing
            # the pending record leaves both, and the proved one is the answer.
            pending = load_pending(self.entry.id, self.install_id, config_dir=self._config_dir)
            return pending if pending is not None else Idle()
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
        """`_check()`, and a note of whether a waiting row is waiting on a channel that is off."""
        return self._noted(self._check())

    def _check(self) -> State:
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

        A `Pending` state is proved here too (T138). It is how a launch finds a
        row an earlier run created and closed before proving, and the server
        was usually left running -- so the check the tab makes on opening is
        the only thing that will ask. `prove()` from `Pending` is `ensure()`'s
        re-verify arm: one round trip, and never a create, so opening a tab
        still writes nothing to the user's auth database. A check that gets no
        answer costs the state none of its tries: it is a look, and the three
        belong to the settles a Start and an install make -- spending them on
        reopens of the app would leave the Start that follows given up.
        """
        state = self._state
        if isinstance(state, Verified | Pending) and self._account_is_gone(state.account):
            # T423: a database Repair re-imports the server and the app's row
            # goes with it, while the saved credential (or the pending record)
            # still says it works. A world that is not up cannot say otherwise,
            # so the database is asked.
            self._state = gone(state.account)
            return self._state
        if isinstance(state, Pending):
            after = self.prove()
            if isinstance(after, Verified | Refused):
                return after
            self._state = state
            return state
        if isinstance(state, Idle):
            return self._find_a_lost_account()
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

    def _noted(self, state: State) -> State:
        """Remember, for the tab, whether a waiting row waits on a channel that is off (T423).

        Read here, on the worker that ran the look or the press, and not by the
        tab: `is_enabled()` reads files, which for an install inside a WSL
        distro can boot a stopped one if it is done on the GUI thread.
        """
        self.channel_is_off = isinstance(state, Pending) and self.is_enabled() is False
        return state

    def _account_is_gone(self, account: str) -> bool:
        """True when the auth database was asked and has no row by this name (T423).

        Never true when the database cannot be asked. Only called for a state
        that already has a row to ask about (`Verified` or `Pending`), so a
        settle's `create` that has not landed yet is not a case here.
        """
        if self._exists is None or self.entry.operations is None:
            return False
        try:
            there = self._exists(account)
        except Exception as exc:  # noqa: BLE001 - a look that cannot be made changes nothing
            logger.info(f"{self.entry.id}: could not look for {account}: {type(exc).__name__}")
            return False
        return not there

    def _find_a_lost_account(self) -> State:
        """`Idle`, or `lost()` when this app's own account is on the server already (T386).

        Nothing on this machine remembers a password for it -- that is what
        `Idle` means -- so an account that is there has one nobody here has.
        Only read: the database is asked whether the row exists, and a
        database that cannot be asked leaves the state as it was.
        """
        looked_from = self._state
        if self._exists is None or self.entry.operations is None:
            return looked_from
        account = account_name(self.install_id)
        try:
            there = self._exists(account)
        except Exception as exc:  # noqa: BLE001 - a look that cannot be made changes nothing
            logger.info(f"{self.entry.id}: could not look for {account}: {type(exc).__name__}")
            return self._state
        # Only over the same `Idle` and with no settle running: a settle is what
        # makes a row with nothing saved yet, and one that began and even
        # finished while the database was asked knows better than the look --
        # a `Verified` it left must not become a Repair.
        if there and self._state is looked_from and not self._settles:
            self._state = lost(account)
        return self._state

    def repair(self) -> State:
        """`_repair()`, and the same note as `check()`."""
        return self._noted(self._repair())

    def _repair(self) -> State:
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
            exists=self._exists,
        )
        return self._state

    def settle(self) -> State:
        """`_settle()`, and the same note as `check()`."""
        return self._noted(self._settle())

    def _settle(self) -> State:
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

        Compared against BOTH renderings, with the `:z` label and without it,
        whatever the host says now (T102). Compared without one, an enforcing
        host's channel-on override -- `:z` on its bind, as the press and the
        install's renderer write it -- read as "edited since", so only the port
        was released and the channel stuck on (measured on `yulon-fedora`,
        2026-09-24). Compared only against the host's CURRENT label, the same
        stick comes back the moment the host stops answering "enforcing"
        (`setenforce 0`, or a probe that cannot tell): the file carries the
        label the press wrote then, not the one the host would pick now. And
        the unlabelled text is what the press wrote before T102, so it is what
        an install upgraded from then has on disk, on any host.

        Each candidate is still an exact match. The two differ only in the
        `:z` token at the end of each host bind
        (`test_the_two_recognised_texts_differ_only_in_the_label`), so a file a
        person has edited in any other way matches neither and is left alone.
        """
        operations = self.entry.operations
        expected: str | None = None
        if operations is not None:
            try:
                # Both renders carry the player's bot count off the file (T117):
                # without it a count changed after Enable matches neither, and the
                # rollback leaves the channel stuck on.
                env = bot_count.world_env(
                    self.entry, self.server_dir, _world_env(self.entry, operations.enable_env)
                )
                texts = [
                    composegen.render(
                        self.entry,
                        self.server_dir,
                        templates_root=self.templates_root,
                        world_env=env,
                        db_password=self._password(),
                        bind_label=each,
                    ).override
                    for each in (":z", "")
                ]
            except Exception as exc:  # noqa: BLE001 - an unrenderable plan is not a reason to stop
                logger.info(f"could not re-render {self.entry.id}'s override to compare: {exc}")
            else:
                target = self.server_dir / composegen.OVERRIDE_FILE
                now = target.read_text(encoding="utf-8") if target.is_file() else None
                expected = now if now in texts else texts[0]
        return roll_back(self.entry, self.server_dir, expected=expected)

    def is_enabled(self) -> bool | None:
        """Whether this install's files already switch the command channel on (T423).

        A read of what `enable()` writes: the conf keys of the trees that read
        them, and the override. `None` when it cannot be told -- an entry with
        no measured channel, a file that is not there to read, a plan that does
        not render -- so a caller offers Enable only on a plain `False`.
        """
        operations = self.entry.operations
        if operations is None:
            return None
        try:
            if operations.enable_conf is not None:
                target = self.server_dir / operations.enable_conf.file
                if not target.is_file():
                    return None
                wanted = dict(operations.enable_conf.keys)
                found = _conf_values(target.read_text(encoding="utf-8"), wanted)
                if any(found[key] != value for key, value in wanted.items()):
                    return False
            env = bot_count.world_env(
                self.entry, self.server_dir, _world_env(self.entry, operations.enable_env)
            )
            texts = [
                composegen.render(
                    self.entry,
                    self.server_dir,
                    templates_root=self.templates_root,
                    world_env=env,
                    db_password=self._password(),
                    bind_label=each,
                ).override
                for each in (":z", "")
            ]
            override = self.server_dir / composegen.OVERRIDE_FILE
            now = override.read_text(encoding="utf-8") if override.is_file() else ""
        except Exception as exc:  # noqa: BLE001 - not knowing is not a reason to offer a write
            logger.info(f"could not tell whether {self.entry.id}'s channel is on: {exc}")
            return None
        if now in texts:
            return True
        # No override at all is plainly off. One that is there and reads
        # differently may be an older render of the same channel (a template
        # updated since), which is not for this read to call off.
        return False if not now else None

    def enable(self, *, world_running: bool) -> Enabled:
        """Write the channel on. Refuses while the world is running."""
        return enable(
            self.entry,
            self.server_dir,
            templates_root=self.templates_root,
            world_running=world_running,
            db_password=self._password(),
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
        # Counted while it runs (T386): between `create` and the round trip
        # the row is there and nothing is saved, which a look from `Idle` would
        # otherwise read as a lost password.
        self._settles += 1
        try:
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
        finally:
            self._settles -= 1
        return self._state
