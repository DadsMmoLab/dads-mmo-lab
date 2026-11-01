"""Is this account's stored credential the password we asked for?

The CMaNGOS trees report a successful `account set password` as a failure --
deliberately, "to avoid normal report for hide passwords" (`Level3.cpp:1178-1183`
on TBC, `:1139-1145` on Vanilla) -- so the reply cannot say what happened and the
row has to. **"The row changed" is not that answer.** Any other writer changes
the row too: a second window, an administrator at a console, a retry. A command
that was refused would then be reported as a success while the account holds
somebody else's password, and the person who asked would be locked out by the
reassurance. That was 8.3b's adversarial review, and it was right.

What answers is the verifier. SRP6 stores `g^H(s, H(USER:PASS)) mod N`, so
recomputing it from the row's own salt and the password that was asked for says
whether THAT password is the one this account now has -- whoever wrote it, and
whatever the server said.

**The recipe is measured, not read off a specification.** An account was created
through this app with a known password on the live Vanilla server (m910q,
2026-09-07), its `s` and `v` read back, and every candidate byte order computed
until one reproduced the stored verifier. Exactly one did, and the wrong password
reproduced none. The one entry that instead rests on read source argues for
itself in `COLUMNS` below. A scheme that has been through neither is absent from
the table below, and this module answers `None` for it: a password change this
app cannot confirm is a smaller answer, not a wrong one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

_N = int("894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7", 16)
"""The SRP6 modulus every WoW auth server has used since 2004."""

_G = 7

COLUMNS: dict[str, tuple[str, ...]] = {
    "mangos_srp6": ("s", "v"),
    "mangos_sha": ("sha_pass_hash",),
    "trinitycore_sha": ("sha_pass_hash",),
}
"""The columns each scheme keeps its credential in, IN RECIPE ORDER.

The order is the point and the trees disagree about it: AzerothCore names its
pair `salt`, `verifier` and the CMaNGOS trees name the same pair `s`, `v` -- so
a pair read in a table's own column order would be back to front on one of them
and would answer "no" for every correct password. `mangos_sha` and
`trinitycore_sha` have ONE column and no salt at all, which is why this is a
tuple rather than a pair.

`trinitycore_sha` (MoP, T850-C) is the one row here that rests on read source
rather than on a row this app wrote and read back, and it says so: it is
`mangos_sha`'s recipe because MoP's core hashes the SAME way -- one SHA1 over
the folded `name` + `":"` + password, printed uppercase (`AccountMgr.cpp:345-355`
`CalculateShaPassHash`, `Util.cpp:614` `%02X`) -- and its `sha_pass_hash` is also
`utf8mb4_bin` (`auth.sql:19`), so the comparison is case-sensitive on both.

`azerothcore` is deliberately absent, and so is any scheme nobody has created a
known-password account under and read back: that tree answers its own commands
properly, so nothing there needs a row believed, and a recipe nobody has run
against a live AzerothCore row would be a guess wearing a measurement's clothes.
"""

MEASURED_SCHEMES = tuple(COLUMNS)
"""Schemes whose stored credential this module can reproduce."""


def _verifier(scheme: str, account: str, password: str, salt: str) -> str | None:
    """The verifier this account and password produce, or `None`.

    `None` where the scheme has not been measured, or the salt is not the hex
    the database is supposed to hold -- both are absences rather than failures,
    because the caller reads that salt off a live server and cannot promise
    what comes back.

    The name and the password are upper-cased before hashing, which is the
    server's own rule and the reason a lower-case account name still logs in.
    """
    if scheme != "mangos_srp6":
        return None
    try:
        salt_bytes = bytes.fromhex(salt.strip())
    except ValueError:
        return None
    if not salt_bytes:
        return None
    identity = hashlib.sha1(f"{account.upper()}:{password.upper()}".encode()).digest()
    # The salt is stored big-endian text of a little-endian number, so it is
    # reversed before hashing and the digest is read back little-endian. Those
    # two are the measurement: three of the four other combinations reproduced
    # nothing, and the fourth reproduced it only for the wrong password.
    x = int.from_bytes(hashlib.sha1(salt_bytes[::-1] + identity).digest(), "little")
    return f"{pow(_G, x, _N):X}".rjust(2, "0")


def sha_hash(account: str, password: str) -> str:
    """`SHA1(UPPER(user):UPPER(pass))`, which is what the Tortoise fork and MoP store.

    Measured on the live server, 2026-09-07: an account created through this app
    held exactly this in `sha_pass_hash`, and after the SERVER changed the
    password itself the column held the hash of the new one -- while `v` and `s`
    went to `0`, so this column is the one that decides.
    """
    return hashlib.sha1(f"{account.upper()}:{password.upper()}".encode()).hexdigest().upper()


def matches(scheme: str, account: str, password: str, values: Sequence[str]) -> bool | None:
    """Does this account's stored credential belong to this password?

    Three answers, and the third is the one the adversarial review asked for.
    `True` and `False` are a comparison that was actually made; **`None` is
    "this module could not read what is in that column"** -- an unmeasured
    scheme, a row with the wrong number of columns in it, a salt that is not
    hex, an empty or unreadable stored value.

    The distinction is not academic here. Since 8.3d a `False` CONTRADICTS the
    server's own report of success and tells a person their new password "will
    not log in" -- so a core that changed how it stores credentials, or a query
    that came back truncated, would turn every successful password change into
    a reported lockout. A shape this module does not recognise is a question it
    could not ask.

    `values` are the columns `COLUMNS[scheme]` names, in that order, as read.

    A plain `==` would be wrong twice over on the SRP6 trees: the stored value
    is a big number's hex, whose width nobody promised, and the case is the
    writer's choice. Both sides are normalised.
    """
    columns = COLUMNS.get(scheme)
    if columns is None or len(values) != len(columns):
        return None
    if scheme in ("mangos_sha", "trinitycore_sha"):
        # One column, one unsalted hash, on both trees: `sha_hash` is MoP's own
        # `CalculateShaPassHash` to the byte, uppercase hex included.
        return _same(values[0], sha_hash(account, password))
    salt, stored = values
    expected = _verifier(scheme, account, password, salt)
    if expected is None:
        return None
    return _same(stored, expected)


def _same(stored: str, expected: str) -> bool | None:
    """One hex number equalling another, however either was written down.

    `None` where the stored side is not a hex number at all: that is a column
    this module cannot read rather than a password that does not match.
    """
    text = stored.strip()
    if not text:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return text.upper().lstrip("0") == expected.upper().lstrip("0")
