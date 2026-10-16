"""Point the player's own launcher at a single-container server: one section of its settings.

DDON's community launcher (DDON-Launcher) lists every section of `DDO_Launcher.ini` as a
server it can log in to: a section added by hand on 2026-10-12 was offered in its list,
and a whole session was played through it. "Add to DDON-Launcher" on the server's tab
calls `point_launcher()`, which writes Yu'lon's own section
(`catalog.SingleLauncher.section`) there and nothing else:

- every other line of the file is kept byte for byte: its BOM, its line endings, the
  servers the player added and the launcher's own settings;
- a section of that name already there is rewritten in place, so a second press updates
  the entry rather than adding another;
- a new section goes in at the top, before the launcher's own, where the hand test put it;
- the file as it was before Yu'lon's first write is kept beside it, once
  (`<file>.yulon-backup`), and never overwritten after.

It refuses, writing nothing, when the file is missing (the launcher writes it the first
time it runs), cannot be read as text, or names the section twice. The client folder is
the player's (README §3a): this is the one write the Play side of the app makes there,
and only on that press.
"""

from __future__ import annotations

import codecs
import contextlib
from dataclasses import dataclass
from pathlib import Path

from yulon.catalog import composegen
from yulon.catalog.catalog import SingleEntry, SingleLauncher
from yulon.log import get_logger

logger = get_logger(__name__)

BACKUP_SUFFIX = ".yulon-backup"
"""What the launcher's file is kept as, beside itself, before Yu'lon first changes it."""


class LauncherRefusal(RuntimeError):
    """The launcher was not changed; the message says why and what to do."""


@dataclass(frozen=True)
class LauncherWrite:
    """What `point_launcher()` did."""

    path: Path
    changed: bool
    backup: Path | None = None
    """The backup this write made; None when there already was one, or nothing changed."""


def launcher_of(entry: SingleEntry) -> SingleLauncher | None:
    """The player's launcher this entry can add itself to, or None."""
    return entry.play.launcher if entry.play is not None else None


def section_lines(entry: SingleEntry, host: str) -> list[str]:
    """The section Yu'lon writes, header first, in the launcher's own `key = value` style."""
    launcher = launcher_of(entry)
    if launcher is None:
        raise LauncherRefusal(f"{entry.name} has no launcher Yu'lon can add it to.")
    tokens = {**entry.port_tokens(), "HOST": host}
    return [f"[{launcher.section}]"] + [
        f"{key} = {composegen.fill(text, tokens)}" for key, text in launcher.keys.items()
    ]


def _header(line: str) -> str | None:
    """The section a line opens, or None for any other line."""
    stripped = line.strip()
    if len(stripped) >= 2 and stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return None


def point_launcher(entry: SingleEntry, client_dir: Path | None, host: str) -> LauncherWrite:
    """Write this server into the player's launcher as one section of its settings file."""
    launcher = launcher_of(entry)
    if launcher is None or entry.play is None:
        raise LauncherRefusal(f"{entry.name} has no launcher Yu'lon can add it to.")
    if client_dir is None:
        raise LauncherRefusal(f"Choose {entry.play.client} first. Nothing was changed.")
    path = client_dir / launcher.file
    if not path.is_file():
        raise LauncherRefusal(
            f"There is no {launcher.file} in {client_dir}. Open {launcher.name} from that "
            "folder once, so it writes its settings, then try again. Nothing was changed."
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise LauncherRefusal(f"{path} could not be read ({exc}). Nothing was changed.") from exc
    bom = codecs.BOM_UTF8 if raw.startswith(codecs.BOM_UTF8) else b""
    try:
        text = raw[len(bom) :].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LauncherRefusal(
            f"{path} is not text Yu'lon can read, so it was left as it is."
        ) from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if _header(line) is not None]
    wanted = launcher.section.casefold()
    ours = [i for i in headers if (_header(lines[i]) or "").casefold() == wanted]
    if len(ours) > 1:
        raise LauncherRefusal(
            f"{path} lists [{launcher.section}] more than once, so Yu'lon cannot tell which one "
            "to update. Remove the extra ones in the launcher, then try again. Nothing was changed."
        )
    block = [line + newline for line in section_lines(entry, host)]
    if ours:
        start = ours[0]
        end = next((i for i in headers if i > start), len(lines))
        # The blank lines between this section and the next one stay where they are.
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        updated = lines[:start] + block + lines[end:]
    else:
        first = headers[0] if headers else len(lines)
        updated = lines[:first] + block + [newline] + lines[first:]
    new_text = "".join(updated)
    if new_text == text:
        return LauncherWrite(path, changed=False)
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    made: Path | None = None
    temporary = path.with_name(path.name + ".yulon-new")
    try:
        if not backup.exists():
            backup.write_bytes(raw)
            made = backup
        temporary.write_bytes(bom + new_text.encode("utf-8"))
        temporary.replace(path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise LauncherRefusal(
            f"{path} could not be written ({exc}). Is {launcher.name} open? Close it and try "
            "again."
        ) from exc
    logger.info(f"{entry.id}: [{launcher.section}] written to {path}")
    return LauncherWrite(path, changed=True, backup=made)
