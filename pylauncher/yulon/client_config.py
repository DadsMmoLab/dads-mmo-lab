"""A ready-to-play client's `WTF/Config.wtf` and locale `realmlist.wtf`s (T181 b/c).

A catalog entry's `config_wtf` names settings its server needs in the client:
`always` keys (where the server is: `realmList`, `realmName`, ...) are set at
every Play, `seed` keys (a first-run preference such as `gxWindow`) only on the
first run and only where the player has no line for them, so a choice made in
the game's own options afterwards is kept. Centurion's launcher does the same.

The file is WoW's console script: one `SET key "value"` per line, read in order,
so the later of two lines for one key wins. That is why a key is matched however
it is spelled (`realmlist` is `realmList` to WoW, and `set` is `SET`): a line
this module did not recognise would be followed by its own, and a seed appended
after the player's line would override the player. Every other line is kept as
it is, byte for byte, in its place; a changed line keeps everything but its
value; added lines take the file's own line ending. Bytes, not text, because a
Config.wtf may hold an account name in any encoding the client wrote it in.

Only ever inside a folder carrying step (a)'s marker, and never through a link:
`WTF/` must be the folder's own and `Config.wtf` must have one name only (step
(a) copies it; a hard link would be the player's own client's file). The new
file is written beside it and renamed into place, so the client never reads
half of one.

`remove_locale_realmlists()` deletes `Data/<locale>/realmlist.wtf` (any case),
as Centurion's launcher does, so the server's address comes from Config.wtf
alone and not also from a file the player's old server left. Only in the
ready-to-play client.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from yulon import play_client
from yulon.catalog.catalog import ConfigWtf
from yulon.log import get_logger

logger = get_logger(__name__)

CONFIG_WTF = Path("WTF") / "Config.wtf"
LOCALE_REALMLIST = "realmlist.wtf"

_SET = re.compile(rb'^\s*SET\s+(\w+)\s+"(.*)"', re.IGNORECASE)
_NEW_FILE_ENDING = b"\r\n"
"""The ending of a Config.wtf Yu'lon creates: the Windows client's own, and WoW reads either."""


def _require_marker(play_dir: Path, what: str) -> None:
    """Refuse a folder that is a link or carries no step (a) marker."""
    if play_client._is_link(play_dir) or play_client.read_marker(play_dir) is None:
        raise play_client.PlayClientError(
            f"{play_dir} is not a ready-to-play client made by Yu'lon (it has no "
            f"{play_client.MARKER} marker), so {what} was left as it was. Make the "
            "ready-to-play client again from the server's Client settings."
        )


def _ending(lines: list[bytes]) -> bytes:
    """The line ending of the first line that has one, else the new-file default."""
    for line in lines:
        if line.endswith(b"\r\n"):
            return b"\r\n"
        if line.endswith(b"\n"):
            return b"\n"
    return _NEW_FILE_ENDING


def _split_ending(line: bytes) -> tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith(b"\n"):
        return line[:-1], b"\n"
    return line, b""


def _merged(raw: bytes, cfg: ConfigWtf, *, first_run: bool) -> bytes:
    """`raw` (a Config.wtf's bytes, empty for none) with `cfg` merged in. Pure."""
    lines = raw.splitlines(keepends=True)
    ending = _ending(lines)
    always = {key.casefold(): value for key, value in cfg.always.items()}
    present: set[str] = set()
    out: list[bytes] = []
    for line in lines:
        body, end = _split_ending(line)
        match = _SET.match(body)
        if match is not None:
            key = match.group(1).decode("ascii", errors="replace").casefold()
            present.add(key)
            if key in always:
                start, stop = match.span(2)
                body = body[:start] + always[key].encode("utf-8") + body[stop:]
        out.append(body + end)
    if out and not out[-1].endswith(b"\n"):
        out[-1] += ending
    added = [(k, v) for k, v in cfg.always.items() if k.casefold() not in present]
    if first_run:
        added += [(k, v) for k, v in cfg.seed.items() if k.casefold() not in present]
    out += [b'SET %s "%s"' % (k.encode("ascii"), v.encode("utf-8")) + ending for k, v in added]
    return b"".join(out)


def _config_path(play_dir: Path) -> Path:
    """`WTF/Config.wtf` of `play_dir`, refusing a `WTF` that is a link or a shared file."""
    wtf = play_dir / CONFIG_WTF.parent
    if play_client._is_link(wtf):
        raise play_client.PlayClientError(
            f"{wtf} is a link to another folder, so its Config.wtf was not changed: writing "
            "there could change another client's settings. Make the ready-to-play client "
            "again from the server's Client settings."
        )
    target = play_dir / CONFIG_WTF
    try:
        st = target.lstat()
    except FileNotFoundError:
        return target
    if stat.S_ISLNK(st.st_mode) or st.st_nlink > 1:
        raise play_client.PlayClientError(
            f"{target} shares its file with another client (it is a link), so it was not "
            "changed: writing it could change your own WoW client's settings too. Make the "
            "ready-to-play client again from the server's Client settings."
        )
    return target


def _replace(tmp: Path, dst: Path) -> None:
    """Rename `tmp` onto `dst`; a read-only `dst` (Windows refuses those) is made writable once.

    Only a file with one name gets here (`_config_path`), so the flag cleared is
    this folder's own.
    """
    try:
        os.replace(tmp, dst)
        return
    except PermissionError:
        st = dst.lstat()
        if st.st_mode & stat.S_IWRITE:
            raise
    os.chmod(dst, st.st_mode | stat.S_IWRITE)
    os.replace(tmp, dst)


def merge_config_wtf(play_dir: Path, cfg: ConfigWtf, *, first_run: bool) -> Path:
    """Merge `cfg` into `play_dir`'s `WTF/Config.wtf` (created if absent); returns its path.

    Raises `play_client.PlayClientError` for a folder without the marker, a `WTF`
    that is a link, or a Config.wtf with another name (a hard or symbolic link),
    with nothing written; OSError when the file cannot be read or written, with
    the old file still whole. A merge that changes nothing writes nothing.
    """
    _require_marker(play_dir, "its Config.wtf")
    target = _config_path(play_dir)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        raw = b""
    new = _merged(raw, cfg, first_run=first_run)
    if target.exists() and new == raw:
        return target
    target.parent.mkdir(exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.yulon-tmp")
    try:
        tmp.write_bytes(new)
        _replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("ready-to-play client: %s updated for this server", target)
    return target


def remove_locale_realmlists(play_dir: Path) -> tuple[Path, ...]:
    """Delete `Data/<locale>/realmlist.wtf` (names compared casefolded) in `play_dir`; sorted.

    Only one level under `Data`, never into a link (a locale folder linked to
    the player's own client keeps its file), and only in a folder carrying the
    marker. A client without a `Data` folder answers `()`.
    """
    _require_marker(play_dir, "its realmlist.wtf files")
    data = play_dir / "Data"
    if play_client._is_link(data) or not data.is_dir():
        return ()
    removed: list[Path] = []
    for locale in sorted(data.iterdir()):
        if play_client._is_link(locale) or not locale.is_dir():
            continue
        for path in sorted(locale.iterdir()):
            if path.name.casefold() == LOCALE_REALMLIST and not path.is_dir():
                path.unlink()
                removed.append(path)
    if removed:
        logger.info("ready-to-play client: removed %s", ", ".join(map(str, removed)))
    return tuple(removed)
