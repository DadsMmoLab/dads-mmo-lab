"""What counts as a link, for the walks and copies that must never go through one (T375, T300).

The rule: a walk or a copy never goes through a link it did not choose.

A link is a symlink, or on Windows a reparse point whose tag has the
name-surrogate bit: Windows' own mark for "this entry stands for another file or
folder" (a junction, a Windows symlink, a WSL symlink, and any later kind). A
reparse point with no tag reported counts as a link, the cautious answer. Other
reparse points are NOT links: OneDrive's files-on-demand placeholders and
deduplicated files carry the attribute too, and are the player's own files.

Python cannot be asked this one way on every version. On Windows a junction is a
folder to `stat.S_ISLNK`, `DirEntry.is_symlink()` and `os.walk(followlinks=False)`,
and `DirEntry.is_junction()` exists only from 3.12, while the Windows build ships
3.11. The reparse attribute and tag are in every look Python takes, so that is
what is read.

Where it is applied:

* the build fingerprint (`catalog.build_context`) records a link by its path,
  mode and target text and never enters it, as Docker's own walk never does;
* a module's client copy (`apply.Applier._client()`) never writes through a link:
  in a ready-to-play client, which Yu'lon makes without any, a link anywhere on
  the way is refused before anything is copied; in the player's own client a
  linked folder is theirs to have chosen and is followed, but a linked file is
  refused, because writing it would change the file it points to;
* a module's own checkout (`apply.Applier._refuse_checkout_links()`, T530): what
  Yu'lon reads or writes there is never reached through a link, inside the
  checkout or out of it, so a repository cannot hand the server, the client or
  the database a file from elsewhere on the player's disk; `walk()` is the walk.

`play_client` takes its own link test from here.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator

# Defined here, not taken from `stat`: there they exist only on Windows builds, and
# the test must be exercisable everywhere.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # a junction
IO_REPARSE_TAG_SYMLINK = 0xA000000C
NAME_SURROGATE = 0x20000000  # winnt.h IsReparseTagNameSurrogate

_lstat = os.lstat  # a seam: tests stand in Windows' answer for a junction


def stat_is_link(st: object) -> bool:
    """Whether a look already taken (`lstat`, `DirEntry.stat(follow_symlinks=False)`) is a link."""
    if stat.S_ISLNK(getattr(st, "st_mode", 0)):
        return True
    if not getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    tag = getattr(st, "st_reparse_tag", None)
    return not tag or bool(tag & NAME_SURROGATE)


def is_link(path: str | os.PathLike[str]) -> bool:
    """Whether `path` itself is a link; False when nothing is there.

    Raises:
        OSError: the path could not be looked at for any other reason, so a
            caller deciding whether to write there never takes "unknown" for "no".
    """
    try:
        st = _lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat_is_link(st)


def walk(top: str | os.PathLike[str]) -> Iterator[tuple[str, list[str], list[str], list[str]]]:
    """`os.walk(top)` top-down, that never enters a link and names the links it met.

    Yields `(folder, dirs, files, linked)`: `linked` holds every name in `folder`
    that is a link (`is_link()`), of any kind, and those names are in neither of
    the other two lists. `dirs` may be pruned in place, as with `os.walk`.
    `os.walk(followlinks=False)` is not this: on Python 3.11 under Windows a
    junction is a folder to it and it walks in. A folder that cannot be listed is
    passed over, as `os.walk` does by default; one entry that cannot be looked
    at counts as a link, the cautious answer.
    """
    folder = os.fspath(top)
    try:
        with os.scandir(folder) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError:
        return
    dirs: list[str] = []
    files: list[str] = []
    linked: list[str] = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            if is_link(path):
                linked.append(name)
            elif stat.S_ISDIR(_lstat(path).st_mode):
                dirs.append(name)
            else:
                files.append(name)
        except OSError:
            linked.append(name)
    yield folder, dirs, files, linked
    for name in dirs:
        yield from walk(os.path.join(folder, name))
