"""Take passwords and the home folder out of text before anybody else reads it (T93).

Two kinds of knowledge, applied in this order, because each catches what the
other cannot:

* **Known values** -- every password this machine can tell us about: the kept
  copies in `db-secrets/`, the channel credentials in `credentials/`, each
  generated install's `.db_password`, and the fourth field of every
  `*DatabaseInfo` line in each install's confs. Longest first, so a password
  that contains another is masked whole rather than leaving its tail behind.
  Eight characters or more are masked anywhere; four to seven only as a whole
  token, because `acore` is both a password and the start of `acore_auth`;
  shorter ones are not masked in free text -- `x9` would take every `x9` out
  of every log -- but the patterns below mask them wherever a password is
  WRITTEN, and the bundle and the Logs tab say plainly that one is set (Codex
  T93 review).
* **Patterns** -- what a password looks like when nobody told us its value: the
  `DatabaseInfo` field (`host;port;user;PASSWORD;schema`, in a conf and in the
  worldserver's own "cannot connect" line), a `*password* = value` setting, and
  the shape this app mints (`<word>-<16 hex>`, `tests/test_no_secrets_in_evidence.py`).

Then the user's home folder becomes `~`. Nothing here reads a file: the caller
gathers the values and this module only applies them.
Line endings are never touched -- no pattern consumes `\\r` or `\\n`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MASK = "***"
"""What a secret becomes. Three characters, no `;`, so a masked field keeps its line's shape."""

SUBSTRING_FLOOR = 8
"""A known value this long is masked wherever it occurs; a random collision is negligible."""

TOKEN_FLOOR = 4
"""Below this a known value is not masked in free text: `abc` as a token would mask
ordinary words. The patterns still mask it in a password position, and
`sources.Known.short` names where one is set so the user is told."""

_WORD = "A-Za-z0-9_"

_FIELD = r"[^\s;\"']{1,255}"

_DATABASE_INFO = re.compile(
    r"(?<![^\s\"'=:,(\[])"
    rf"(?P<head>(?:[^\s;\"'=]{{1,255}};\d{{1,5}}|\.;{_FIELD});{_FIELD};)"
    r"(?P<password>[^\s;\"']{0,255})"
    rf"(?=;{_FIELD})"
)
"""`host;port;user;PASSWORD;schema` wherever it appears. The lookahead keeps the schema.

The second field is a port (`\\d{1,5}`), or anything after the host `.`, which
is how AzerothCore's conf spells a unix socket or a Windows named pipe. Without
that a build log's `mod-a;mod-b;mod-c;mod-d;mod-e` lost its fourth entry.

**Linear, and that is why it looks like this.** The obvious spelling
(`[^;]+;[^;]+;...` with no anchor) restarts at every character of a long run
with no `;` in it and scans to the run's end each time: measured while this
plan was checked, a 200 KB base64 line kept one core at 100% for minutes. The
lookbehind lets a match START only after whitespace, a quote, `=`, `:`, `,`,
`(`, `[` or the start of the text, and every field is bounded.
"""

_DATABASE_INFO_LINE = re.compile(
    r"(?m)^[ \t]*\w*Database\.?Info[ \t]*=[ \t]*\"?(?P<value>[^\"\r\n]*)\"?"
)
"""An ACTIVE `*DatabaseInfo = ...` conf line; Tortoise spells the key `*Database.Info`.

A commented line starts with `#` and is skipped.
"""

_PASSWORD_SETTING = re.compile(
    r"(?i)(?<![\w.-])(?P<head>[\w.-]{0,64}?password[\w.-]{0,64}[\"']?[ \t]*[=:][ \t]*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"',;]*[^\s\"',;)])"
)
"""`Key.Password = value`, `DB_ROOT_PASSWORD=value`, `"password": "value"`.

A bare value never ends in `)`: MySQL's `(using password: YES)` closes its
bracket there. `YES` and `NO` themselves are left alone (`_mask_setting`) -- they
say whether a password was sent, which is the point of that line.

Starts only at the start of a token and bounds the key, for `_DATABASE_INFO`'s
reason: an unbounded run of word characters before `password` is quadratic on a
long line.
"""

_NOT_A_PASSWORD = frozenset({"YES", "NO"})
"""Bare setting values that only say whether there is a password: MySQL's error 1045."""

_GENERATED = re.compile(r"\b[a-z]+-[0-9a-f]{16}\b")
"""The shape `resolve_secrets()` mints: `prefix + token_hex(8)` (`catalog/native.py`)."""


def database_info_passwords(text: str) -> set[str]:
    """The password field of every active `*DatabaseInfo` line in a conf's text."""
    found: set[str] = set()
    for match in _DATABASE_INFO_LINE.finditer(text):
        fields = match.group("value").split(";")
        if len(fields) >= 4 and fields[3]:
            found.add(fields[3])
    return found


def _mask_field(match: re.Match[str]) -> str:
    if not match.group("password"):
        return match.group(0)
    return match.group("head") + MASK


def _mask_setting(match: re.Match[str], short: frozenset[str] = frozenset()) -> str:
    """Mask the value, unless it is empty or MySQL's `YES`/`NO` -- and that is not a
    password this machine actually uses (`short`: the known values under `TOKEN_FLOOR`)."""
    value = match.group("value")
    if value in ('""', "''"):
        return match.group(0)
    if value.upper() in _NOT_A_PASSWORD and value not in short:
        return match.group(0)
    return match.group("head") + MASK


def _alternation(values: list[str]) -> str:
    return "|".join(re.escape(value) for value in values)


def _home_pattern(home: Path | None) -> re.Pattern[str] | None:
    """Both slash spellings of the home folder, never a longer name sharing its prefix."""
    if home is None:
        return None
    text = str(home).rstrip("/\\")
    if len(text) < 2:
        return None
    spellings = sorted(
        {text, text.replace("\\", "/"), text.replace("/", "\\")}, key=len, reverse=True
    )
    return re.compile(rf"(?:{_alternation(spellings)})(?![{_WORD}.-])")


@dataclass(frozen=True)
class Redactor:
    """Known values, patterns and the home folder, compiled once."""

    anywhere: re.Pattern[str] | None
    tokens: re.Pattern[str] | None
    home: re.Pattern[str] | None
    short: frozenset[str] = frozenset()
    """Known values under `TOKEN_FLOOR`: never masked in free text, only in a password position."""

    @classmethod
    def build(cls, known: Iterable[str], *, home: Path | None = None) -> Redactor:
        """Compile the known values (longest first) and the home folder."""
        given = set(known)
        values = {value for value in given if len(value) >= TOKEN_FLOOR}
        short = frozenset(value for value in given if 0 < len(value) < TOKEN_FLOOR)
        longest_first = sorted(values, key=lambda value: (-len(value), value))
        anywhere = [value for value in longest_first if len(value) >= SUBSTRING_FLOOR]
        tokens = [value for value in longest_first if len(value) < SUBSTRING_FLOOR]
        return cls(
            anywhere=re.compile(_alternation(anywhere)) if anywhere else None,
            tokens=(
                re.compile(rf"(?<![{_WORD}])(?:{_alternation(tokens)})(?![{_WORD}])")
                if tokens
                else None
            ),
            home=_home_pattern(home),
            short=short,
        )

    def redact(self, text: str) -> str:
        """`text` with every secret this redactor knows or recognises replaced."""
        if self.anywhere is not None:
            text = self.anywhere.sub(MASK, text)
        if self.tokens is not None:
            text = self.tokens.sub(MASK, text)
        text = _DATABASE_INFO.sub(_mask_field, text)
        short = self.short
        text = _PASSWORD_SETTING.sub(lambda match: _mask_setting(match, short), text)
        text = _GENERATED.sub(MASK, text)
        if self.home is not None:
            text = self.home.sub("~", text)
        return text
