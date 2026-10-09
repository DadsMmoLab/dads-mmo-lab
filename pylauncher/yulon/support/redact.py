"""Take passwords and the home folder out of text before anybody else reads it (T93).

Two kinds of knowledge, applied in this order, because each catches what the
other cannot:

* **Known values** -- every password this machine can tell us about: the kept
  copies in `db-secrets/`, the channel credentials in `credentials/` (and
  in `credentials/pending/` before they are proved, T138), each
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

* **Credentials by structure** (T595) -- what a login or a service prints that is
  not a password: a realm's `sessionkey` (the credential the world server accepts for
  that login), `sha_pass_hash`, the SRP `v`/`s`/verifier of an SQL line, hex blobs in
  an INSERT, `token`/`secret`/`api_key` settings, `Authorization` headers, `user:pass@`
  URLs and `?token=` queries, Docker's `"auth"` and a `docker login -p`, `mysql -p`,
  `account create` / `account set password` commands. Named by their KEY or by the
  statement they sit in, never by a guess at a random-looking string, so a commit
  hash in a build log stays readable.

Then the user's home folder becomes `~`, in every spelling a path takes in a log:
backslashes, forward slashes, doubled backslashes (a Python repr), URL-encoded, the
`/mnt/c/Users/name` and `wsl.localhost` forms, the 8.3 short name, any other Windows
account under `Users`, and the bare user name where it is a path component. Nothing
here reads a file: the caller gathers the values and this module only applies them.

`checked()` is the fail-closed form: a text whose redaction raises, or whose redacted
form would change if redacted again (something got through the first pass), is
refused with `Unredactable`, and the bundle leaves that file out (T595).
Line endings are never touched -- no pattern consumes `\\r` or `\\n`.
"""

from __future__ import annotations

import itertools
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


class Unredactable(Exception):
    """A text the redactor cannot vouch for: it raised, or it did not settle."""


_SEP = r"(?:\\|/|%5[Cc]|%2[Ff])"
"""One path separator as a log writes it: a backslash, a slash, or either URL-encoded."""

_SEPS = _SEP + "{1,16}"
"""A run of them: a repr doubles every backslash, a repr of a repr quadruples it. Bounded, so a
long run of backslashes costs a bounded scan from each position, not the rest of the run."""

_UNC = rf"(?:{_SEP}{{2,16}}(?:wsl\.localhost|wsl\$|wsl%24){_SEPS}[^\\/\s'\"<>|%]{{1,64}})"
"""The `wsl.localhost` / `wsl$` network share Windows reaches a distro's disk through."""

_DRIVE = r"(?:[A-Za-z](?::|%3[Aa])|" + _SEPS + r"[A-Za-z](?=" + _SEP + "))"
_MNT = rf"(?:{_UNC}?{_SEPS}mnt{_SEPS}[A-Za-z](?={_SEP}))"
_SHARED_PROFILES = r"(?:Public|Default(?: User)?|All Users)"
_NAME_CHAR = r"[^\\/\s'\"<>|:*?%]"

_PROFILE_WORD = rf"(?:%(?!5[Cc]|2[Ff])[0-9A-Fa-f]{{2}}|{_NAME_CHAR})++"
"""One word of a profile name. Possessive (Python 3.11+): it never gives characters back, so
the words after it are tried once, not once per way of splitting the text."""
_PROFILE_END = rf"{_SEP}|['\"<>|]"
"""What may follow a profile name that has spaces in it: a separator or a closing quote. A name
at the very end of a line is masked up to its first space, never over the words after it."""
_OTHER_WINDOWS_HOME = re.compile(
    rf"(?:{_MNT}|{_DRIVE}){_SEPS}Users{_SEPS}"
    rf"(?!{_SHARED_PROFILES}(?![\w]))"
    rf"{_PROFILE_WORD}(?: {_PROFILE_WORD}){{1,3}}(?={_PROFILE_END})"
    rf"|(?:{_MNT}|{_DRIVE}){_SEPS}Users{_SEPS}"
    rf"(?!{_SHARED_PROFILES}(?![\w])){_PROFILE_WORD}",
    re.IGNORECASE,
)
"""Some other Windows account's profile, seen by name from a path (T595).

Found by its shape -- a drive, then `Users`, then a name, or `/mnt/<drive>/Users/<name>`,
in any spelling above -- because a Linux or WSL process cannot know the Windows user's name
and a log written inside a distro prints it. The shared profiles are nobody's home.
"""

_GENERIC_NAMES = frozenset(
    {"root", "user", "users", "home", "admin", "administrator", "public", "default", "guest"}
    | {"system", "docker", "mangos", "acore", "azeroth", "wow", "ubuntu"}
)
"""User names that are also ordinary path components: masked inside a home path only."""

_WINDOWS_HOME = re.compile(r"^(?:\\\\\?\\)?([A-Za-z]):[\\/]*(.*)$")
_MNT_HOME = re.compile(r"^/mnt/([A-Za-z])(?:/+(.*))?$")
_HOME_FOLDER = re.compile(
    rf"(?:^|{_SEP})(?:(?P<posix>home){_SEPS}(?P<pname>[^\\/]+)"
    rf"|(?P<mac>Users){_SEPS}(?P<mname>[^\\/]+))",
    re.IGNORECASE,
)


def _split_drive(text: str) -> tuple[str | None, str]:
    """`(drive letter, the path after it)` for `C:\\...` and `/mnt/c/...`, else `(None, text)`."""
    mnt = _MNT_HOME.match(text)
    if mnt is not None:
        return mnt.group(1), mnt.group(2) or ""
    win = _WINDOWS_HOME.match(text)
    if win is not None:
        return win.group(1), win.group(2)
    return None, text


def home_of(path: str) -> str | None:
    """The home folder an install's path is under, or None when it shows none (T595).

    `/home/<name>` (also behind the `wsl.localhost` share), a drive, then `Users` and a name,
    and `/mnt/<drive>/Users/<name>` (both give the Windows spelling), `/Users/<name>`.
    """
    text = path.strip()
    drive, rest = _split_drive(text)
    if drive is not None:
        parts = [part for part in re.split(r"[\\/]+", rest) if part]
        if len(parts) >= 2 and parts[0].lower() == "users":
            return f"{drive.upper()}:\\Users\\{parts[1]}"
        return None
    found = _HOME_FOLDER.search(
        re.sub(r"^(?:[\\/]{2,}wsl(?:\.localhost|\$)[\\/]+[^\\/]+)", "", text)
    )
    if found is None:
        return None
    if found.group("posix"):
        return f"/home/{found.group('pname')}"
    return f"/Users/{found.group('mname')}"


def _char(ch: str) -> str:
    """One character of a name, as it may be spelled: literal, %XX, `\\uXXXX`, or `+`."""
    if ch.isascii():
        if ch.isalnum():
            return f"(?:{ch}|%{ord(ch):02X})"  # %41 is "A": a name can be spelled all in escapes
        if ch == " ":
            return r"(?: |%20|\+)"
        return f"(?:{re.escape(ch)}|%{ord(ch):02X})"
    utf8 = "".join(f"%{byte:02X}" for byte in ch.encode("utf-8"))
    code = ord(ch)
    if code > 0xFFFF:
        high, low = 0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)
        escaped = rf"\\+u{high:04x}\\+u{low:04x}"
    else:
        escaped = rf"\\+u{code:04x}"
    return f"(?:{re.escape(ch)}|{escaped}|{utf8})"


def _part(name: str) -> str:
    """One folder name in every spelling a log writes it."""
    return "".join(_char(ch) for ch in name)


def _home_body(home: str) -> tuple[str, str] | None:
    """The regex for one home folder in every spelling, and its user name; None if too short."""
    text = home.strip().rstrip("/\\")
    if len(text) < 2:
        return None
    drive, rest = _split_drive(text)
    parts = [part for part in re.split(r"[\\/]+", rest) if part]
    if not parts:
        return None
    body = "".join(f"{_SEPS}{_part(part)}" for part in parts)
    if drive is not None:
        head = rf"(?:{_MNT}|{_DRIVE})?"
    else:
        head = rf"{_UNC}?"
    return f"{head}{body}(?![\\w.-])", parts[-1]


def _home_patterns(
    homes: Iterable[str],
) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """One pattern for every home folder spelling, and one for the bare user names."""
    bodies: list[str] = []
    names: list[str] = []
    for home in dict.fromkeys(homes):
        built = _home_body(home)
        if built is None:
            continue
        bodies.append(built[0])
        name = built[1]
        if len(name) >= 3 and name.lower() not in _GENERIC_NAMES and name not in names:
            names.append(name)
    whole = (
        re.compile("|".join(f"(?:{body})" for body in bodies), re.IGNORECASE) if bodies else None
    )
    alone = None
    if names:
        spelled = "|".join(_part(name) for name in names)
        alone = re.compile(
            rf"(?:(?:(?<=[\\/])|(?<=%5[Cc])|(?<=%2[Ff]))(?:{spelled})(?![\w.-])"
            rf"|(?<![\w.-])(?:{spelled})(?={_SEP}))",
            re.IGNORECASE,
        )
    return whole, alone


_STRONG_KEY = (
    r"session[ _-]?key|session[_-]?id|jsessionid|phpsessid|sha[_-]?pass(?:[_-]?hash)?|verifier"
    r"|api[_-]?key|private[_-]?key|passwd|[_-]pwd|pwd[_-]"
)
_WEAK_KEY = r"token|secret|credentials?"
_KEYED = re.compile(
    rf"(?i)(?<![\w.-])(?P<head>[\w.-]{{0,64}}?(?:(?P<strong>{_STRONG_KEY})|(?P<weak>{_WEAK_KEY}))"
    r"[\w.-]{0,64}[\\\"']{0,5}[ \t]*[=:][ \t]*)"
    r"(?P<value>\\{1,4}[\"'][^\"'\\\r\n]*\\{1,4}[\"']|[bBuU]?\"[^\"\r\n]*\"|[bBuU]?'[^'\r\n]*'|[^\s\"',;)&]+)"
)
"""`sessionkey = 'hex'`, `"token": "..."`, `{'sessionkey': '...'}`, `MYSQL_PWD=...`, in a
repr or JSON string too (backslash-escaped quotes).

A weak name (`token`, `secret`) masks a value only when it is long enough to be one:
`token: ok` and `tokens: 5` are prose. The strong names mask anything.
"""

WEAK_VALUE_FLOOR = 6

_SQL_LINE = re.compile(
    r"(?im)^[^\r\n]{0,200}?\b(?:UPDATE|INSERT|REPLACE)\b[^\r\n]{0,200}?\b(?:SET|INTO|VALUES)\b[^\r\n]*"
)
_SQL_ASSIGN = re.compile(
    r"(?i)(?<![\w.])(?P<head>`?(?:sessionkey|session_key|sha_pass_hash|sha_pass|verifier|salt"
    r"|token|v|s)`?[ \t]*=[ \t]*)"
    r"(?P<value>X'[^']*'|'(?:[^'\\]|\\.)*'|\"[^\"\r\n]*\"|0x[0-9a-f]+|[^\s,;)]+)"
)
_SQL_HEX = re.compile(r"(?i)(?P<pre>\bX['\"]|['\"])[0-9a-f]{32,}(?P<post>['\"])|\b0x[0-9a-f]{32,}")
"""Inside a realmd/auth `UPDATE`/`INSERT` line: the SRP and session columns by name, and any
quoted or `0x` hex blob of 32 digits or more (a SHA-1 hash, a session key, `v`, `s`) by shape."""

_AUTH_HEADER = re.compile(
    r"(?i)(?<![\w-])(?P<head>(?:proxy-)?authorization[\"']?[ \t]*[=:][ \t]*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"
)
"""An `Authorization` header: the scheme and everything after it, whatever the scheme is
(Basic, Bearer, Digest's whole parameter list, NTLM, a scheme invented tomorrow). A quoted
value ends at its quote, so a JSON log line keeps its other fields."""
_BEARER = re.compile(r"(?i)(?<![\w-])(?P<head>bearer[ \t]+)(?P<value>[A-Za-z0-9._~+/=-]{12,})")
_BASIC = re.compile(
    r"(?<![\w-])(?P<head>[Bb]asic[ \t]+)"
    r"(?P<value>(?=[A-Za-z0-9+/]*[A-Z0-9])[A-Za-z0-9+/]{16,}={0,2})"
)
_URL_USERINFO = re.compile(
    r"(?i)(?P<head>\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]*:)(?P<value>[^\s/@'\"]+)(?=@)"
)
_URL_QUERY = re.compile(
    r"(?i)(?P<head>[?&;](?:[\w-]*token|[\w-]*key|auth|sig|signature|session(?:id)?|sid|secret"
    r"|passwd|password|pwd)=)(?P<value>[^&\s\"'#<>]+)"
)
_DOCKER_AUTH = re.compile(
    r"(?i)(?P<head>\\{0,4}[\"'](?:auth|identitytoken|registrytoken)\\{0,4}[\"']\s*:\s*\\{0,4}[\"'])"
    r"(?P<value>[^\"'\\\r\n]+)"
)
_REGISTRY_LOGIN = re.compile(
    r"(?P<head>\b(?i:docker|podman)(?:\.exe)?[ \t]+login\b[^\r\n]{0,300}?[ \t'\"](?:-p|--password)"
    r"(?:[ \t=]+|['\"],[ \t]*['\"]))(?P<value>[^\s\"',\]]+)"
)
_DASH_PASSWORD = re.compile(r"(?P<head>(?<![\w-])--password[ \t]+)(?P<value>[^\s\"'\-][^\s\"']*)")
_ARGV_PASSWORD = re.compile(
    r"(?P<head>['\"](?:-p|--password|--pass|--pwd)['\"],[ \t]*['\"])(?P<value>[^'\"]+)"
)
_MYSQL_CLI = re.compile(
    r"(?P<head>\b(?i:mysql|mariadb|mysqldump|mysqladmin)(?:\.exe)?\b[^\r\n]{0,300}?[ \t'\"]-p)"
    r"(?P<value>[^\s\"',\]]+)"
)
_USER_THEN_PASSWORD = re.compile(
    r"(?P<head>[ \t'\"]-u[^\s\"',]+['\"]?,?[ \t]*['\"]?-p)(?P<value>[^\s\"',\]]+)"
)
_ACCOUNT_CREATE = re.compile(
    r"(?i)(?P<head>\baccount[ \t]+create[ \t]+[^\s\"']+[ \t]+)(?P<value>[^\s\"']+)"
)
_ACCOUNT_PASSWORD = re.compile(
    r"(?i)(?P<head>\baccount[ \t]+(?:set[ \t]+)?password[ \t]+(?:[^\s\"']+[ \t]+)?)"
    r"(?P<value>[^\s\"']+(?:[ \t]+[^\s\"']+)?)"
)
_XML_SECRET = re.compile(
    r"(?i)(?P<head><(?P<tag>[\w:.-]*(?:password|passwd|secret|token|session[_-]?key"
    r"|authorization|verifier)[\w:.-]*)(?:\s[^<>]*)?>)(?P<value>[^<\r\n]+)(?=</)"
)
"""Every gap between two words is bounded, so a line of ten thousand `docker login` words
costs a bounded scan each, not the rest of the line (`test_secret_masking_is_linear...`).

The console commands the app itself sends (`commands.py`), an XML element a SOAP reply
or request carries, and the command lines that put a password on an argv."""

_COOKIE = re.compile(
    r"(?i)(?<![\w-])(?P<head>(?P<set>set-)?cookie[\"']?[ \t]*[=:][ \t]*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n]+)"
)
_COOKIE_PAIR = re.compile(r"(?P<head>(?:^|[;,][ \t]*)[^=;,\s]+=)(?P<value>[^;,\r\n]*)")
_COOKIE_ATTRIBUTES = frozenset(
    {"path", "domain", "expires", "max-age", "samesite", "secure", "httponly", "version"}
)
"""A `Cookie:` / `Set-Cookie:` line carries a login session. In `Cookie:` EVERY pair is a
cookie (`path` is a valid cookie name there) and every value is masked. In `Set-Cookie:` the
first pair is the cookie and the later `Path`, `Expires`, ... are attributes, which stay so
the line still reads. A quoted value (a JSON field) ends at its quote."""

_HEAD_VALUE = (
    _AUTH_HEADER,
    _BEARER,
    _BASIC,
    _URL_USERINFO,
    _URL_QUERY,
    _DOCKER_AUTH,
    _REGISTRY_LOGIN,
    _DASH_PASSWORD,
    _ARGV_PASSWORD,
    _MYSQL_CLI,
    _USER_THEN_PASSWORD,
    _ACCOUNT_CREATE,
    _ACCOUNT_PASSWORD,
    _XML_SECRET,
)


def _is_masked(value: str) -> bool:
    """`value` is what a mask leaves: `***`, with at most the closing brackets that were
    after the secret (`"password": "x"}` leaves `***}`). A secret that merely STARTS with
    `***` is not masked: `***hunter2` is a password."""
    bare = value.strip("\"'\\")
    return bare == MASK or (bare.startswith(MASK) and not bare[len(MASK) :].strip("}])>"))


def _mask_head_value(match: re.Match[str]) -> str:
    if _is_masked(match.group("value")):
        return match.group(0)  # already masked: a second pass must change nothing
    return match.group("head") + MASK


def _mask_cookie(match: re.Match[str]) -> str:
    value = match.group("value")
    quoted = len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]
    inner = value[1:-1] if quoted else value
    set_cookie = match.group("set") is not None
    seen = itertools.count()

    def pair(found: re.Match[str]) -> str:
        position = next(seen)
        name = found.group("head").rstrip("=").lstrip(";, \t").lower()
        cookie_value = found.group("value")
        attribute = set_cookie and position > 0 and name in _COOKIE_ATTRIBUTES
        if attribute or not cookie_value.strip() or _is_masked(cookie_value.strip()):
            return found.group(0)
        return found.group("head") + MASK

    masked = _COOKIE_PAIR.sub(pair, inner)
    return match.group("head") + (f"{value[0]}{masked}{value[0]}" if quoted else masked)


def _mask_keyed(match: re.Match[str]) -> str:
    value = match.group("value")
    bare = value.strip("\"'\\")
    if not bare or _is_masked(bare):
        return match.group(0)
    if match.group("strong") is None and len(bare) < WEAK_VALUE_FLOOR:
        return match.group(0)
    return match.group("head") + MASK


def _mask_sql_hex(match: re.Match[str]) -> str:
    pre = match.group("pre")
    return f"{pre}{MASK}{match.group('post')}" if pre else MASK


def _mask_sql_line(match: re.Match[str]) -> str:
    line = _SQL_ASSIGN.sub(_mask_head_value, match.group(0))
    return _SQL_HEX.sub(_mask_sql_hex, line)


_SESSION_SHAPED = re.compile(r"(?<![\w])[0-9A-Fa-f]{80}(?![\w])")
"""80 hex digits standing alone: the 40-byte SRP session key (K), wherever a log prints it.
Not 40 (a commit, a SHA-1), 64 (a SHA-256 checksum) or 128 (SHA-512): those are read daily."""


def mask_credentials(text: str) -> str:
    """`text` with every login key, token and credential these patterns know taken out."""
    text = _SESSION_SHAPED.sub(MASK, text)
    text = _SQL_LINE.sub(_mask_sql_line, text)
    text = _KEYED.sub(_mask_keyed, text)
    text = _COOKIE.sub(_mask_cookie, text)
    for pattern in _HEAD_VALUE:
        text = pattern.sub(_mask_head_value, text)
    return text


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
    if value in ('""', "''") or _is_masked(value):
        return match.group(0)
    if value.upper() in _NOT_A_PASSWORD and value not in short:
        return match.group(0)
    return match.group("head") + MASK


def _alternation(values: list[str]) -> str:
    return "|".join(re.escape(value) for value in values)


@dataclass(frozen=True)
class Redactor:
    """Known values, patterns and the home folder, compiled once."""

    anywhere: re.Pattern[str] | None
    tokens: re.Pattern[str] | None
    home: re.Pattern[str] | None
    short: frozenset[str] = frozenset()
    """Known values under `TOKEN_FLOOR`: never masked in free text, only in a password position."""
    names: re.Pattern[str] | None = None
    """The user name alone, where it is a path component (T595)."""
    other_windows: bool = True
    """Mask any other Windows profile under `Users` by its shape (T595)."""

    @classmethod
    def build(
        cls,
        known: Iterable[str],
        *,
        home: Path | None = None,
        also_home: Iterable[str] = (),
    ) -> Redactor:
        """Compile the known values (longest first) and the home folders.

        `also_home`: other home folders this machine's logs may show -- the one a WSL
        install lives under, `USERPROFILE` -- as `sources.other_homes` finds them.
        """
        given = set(known)
        values = {value for value in given if len(value) >= TOKEN_FLOOR}
        short = frozenset(value for value in given if 0 < len(value) < TOKEN_FLOOR)
        homes = ([str(home)] if home is not None else []) + list(also_home)
        whole, alone = _home_patterns(homes)
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
            home=whole,
            short=short,
            names=alone,
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
        text = mask_credentials(text)
        if self.home is not None:
            text = self.home.sub("~", text)
        if self.other_windows:
            text = _OTHER_WINDOWS_HOME.sub("~", text)
        if self.names is not None:
            text = self.names.sub("~", text)
        return text

    def checked(self, text: str) -> str:
        """`redact()`, or `Unredactable` when it cannot be trusted (T595).

        Trusted means it did not raise and it settled: redacting the result again
        changes nothing. A second pass that still finds something to mask proves
        the first one missed it, and a support file must not carry what its own
        redactor admits it half-removed.
        """
        try:
            once = self.redact(text)
            again = self.redact(once)
        except Exception as exc:  # noqa: BLE001 - any failure of a pattern means "not safe"
            raise Unredactable(f"redaction failed ({type(exc).__name__})") from exc
        if again != once:
            raise Unredactable("redacting the result again still changed it")
        return once
