"""Which client add-ons a folder holds, under which names, and whether this client takes them.

A pure read (T613) with no Qt and no writes, shared by every game and every source: a
zip staged by `addon_archive`, a folder the player chose, or a clone. It answers
the same before a folder is copied and after a clone lands, so every route
refuses at the same point and for the same reason.

**What an add-on is to the client.** The game loads `Interface/AddOns/<Folder>/
<Folder>.toc`, and nothing else in that folder decides its name. So the name an
add-on is installed under is the stem of its `.toc`, with its case kept: a GitHub
archive zip gives `pfUI-master/pfUI.toc`, and that is the add-on `pfUI`. Where the
folder came from is recorded as `src` so the installer can copy it under that name.

**Where add-ons are looked for.** A source wrapped in one folder (`X-1.2/`, and a
Windows "Extract All" that wraps it twice) is unwrapped first. Then either the top
holds a `.toc` and is the add-on, or the add-ons are the folders holding a `.toc`
at the top, under `addon/`, `addons/` (`AddOns/`) or `Interface/AddOns/`. Several
add-ons in one source are all found (Bagnon with Bagnon_Config).

**Which `.toc`.** A file such as `pfUI-tbc.toc` beside `pfUI.toc` is the same
add-on's file for another game version, and is set aside. When the main toc does
not fit this client and such a variant does, the variant is taken, under its own
name, because the old client reads only `<Folder>.toc`. pfUI's own README says the
same for TBC ("Rename the folder pfUI-master to pfUI-tbc", read 2026-10-09); not yet
measured in a client: T613's TBC live step checks it. More than one main toc is refused,
except that a folder holding its own name's toc is loaded by that one.

**Which Interface numbers this client takes.** `band()`: from the major version's
first number up to the client's own, so 3.3.5a (30300) takes 30000-30300, 2.4.3
(20400) takes 20000-20400 and 1.12 (11200, Turtle too) takes 10000-11200. Above
the band is a Classic re-release with a different API, below it another expansion:
both refused. Inside it but older is installed with an "out of date" note (owner,
2026-10-09, Q4), and a toc with no Interface line is installed with a note. Only
the FIRST number on the line counts, as an old client reads it, and the toc is read
as utf-8-sig, because a byte-order mark in front of a first-line `## Interface:`
otherwise hides the line (both gaps were T596a's).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from yulon import links

NOTHING_CHANGED = "Nothing was changed."

ADDON_FOLDERS = ("addon", "addons", "interface/addons")
"""Folders (compared case-blind) whose children are add-ons, besides the source's top."""

UNWRAP_LEVELS = 2
"""How many lone wrapping folders are looked inside: a zip's own, and Windows' "Extract All"."""

CLIENT_NAMES: Mapping[int, str] = {11200: "1.12", 20400: "2.4.3", 30300: "3.3.5a"}
"""The clients Yu'lon runs, by their Interface number (`catalog.Client.addon_interface`)."""

TOC_READ_BYTES = 64 * 1024
"""How much of a `.toc` is read. Its `##` lines are a header; the rest is a file list."""

_INTERFACE = re.compile(r"^[ \t]*##[ \t]*Interface[ \t]*:(.*)$", re.IGNORECASE | re.MULTILINE)
_FIELD = re.compile(r"^[ \t]*##[ \t]*([^:\r\n]+?)[ \t]*:(.*)$", re.MULTILINE)
_FIRST_INTEGER = re.compile(r"\d+")

_VARIANT = re.compile(
    r"^(?P<base>.+?)[-_](tbc|bcc|wotlk|wotlkc|wrath|classic|vanilla|mainline|cata|mists)$",
    re.IGNORECASE,
)
"""`pfUI-tbc.toc` beside `pfUI.toc`: the same add-on's file for another game version."""

_HIDDEN = re.compile(r"^(\.|__MACOSX$)", re.IGNORECASE)
"""Entries that do not count as content: dot files (`.DS_Store`, `.git`) and macOS zip debris."""

_FOLDER_FORBIDDEN = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{n}" for n in range(1, 10)),
        *(f"LPT{n}" for n in range(1, 10)),
    }
)


@dataclass(frozen=True)
class Addon:
    """One add-on found: the name it installs under, where it is, and the toc that named it.

    `src` is relative to the folder `find_addons()` was handed, POSIX-spelled, and
    `"."` when that folder is the add-on itself.
    """

    name: str
    src: str
    toc: str
    interface: int | None


@dataclass(frozen=True)
class Found:
    """The add-ons a source holds, and what the player should be told before installing."""

    addons: tuple[Addon, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Refusal:
    """Why the source is not installed: one sentence, ending with `NOTHING_CHANGED`."""

    sentence: str


def band(interface: int) -> tuple[int, int]:
    """The Interface numbers a client of `interface` takes: its major version's, up to its own."""
    return (interface // 10000 * 10000, interface)


def read_interface(toc: Path) -> int | None:
    """The first integer of the toc's first `## Interface:` line, or None where it has none."""
    said = _INTERFACE.search(_read_toc(toc))
    if said is None:
        return None
    number = _FIRST_INTEGER.search(said[1])
    return int(number[0]) if number else None


def find_addons(
    root: Path,
    *,
    interface: int,
    shipped: Mapping[str, str],
    installed: Collection[str] = (),
    label: str | None = None,
) -> Found | Refusal:
    """The add-ons `root` holds for a client of `interface`, or the one reason it is refused.

    `shipped` maps the add-on folder names Yu'lon already installs for this server
    (any case) to the item that installs them; `installed` names add-ons already in
    the client, read only to word the dependency notes. `label` names the source in
    a sentence (default: the folder's name).
    """
    label = label or root.name
    try:
        return _find(root, interface=interface, shipped=shipped, installed=installed, label=label)
    except OSError as exc:
        where = exc.filename or root
        return Refusal(
            f"Yu'lon could not read {where} ({exc.strerror or exc}). If it is in OneDrive, make "
            f"it available offline in OneDrive, then try again. {NOTHING_CHANGED}"
        )


def _find(
    root: Path,
    *,
    interface: int,
    shipped: Mapping[str, str],
    installed: Collection[str],
    label: str,
) -> Found | Refusal:
    base = unwrap(root)
    picked: list[tuple[Addon, Path]] = []
    notes: list[str] = []
    for folder in _candidates(base):
        chosen = _choose(folder, root, interface)
        if isinstance(chosen, Refusal):
            return chosen
        addon, note = chosen
        picked.append((addon, folder))
        if note:
            notes.append(note)
    if not picked:
        return Refusal(_nothing_found(base, label))
    seen: dict[str, Addon] = {}
    for addon, _folder in picked:
        refusal = _name_refusal(addon.name, shipped)
        if refusal:
            return Refusal(refusal)
        twin = seen.get(addon.name.casefold())
        if twin is not None:
            return Refusal(
                f"{label} holds two add-ons named {twin.name} ({twin.src}, {addon.src}), and the "
                f"game client can hold only one. {NOTHING_CHANGED}"
            )
        seen[addon.name.casefold()] = addon
    present = {name.casefold() for name in (*seen, *installed, *shipped)}
    for addon, folder in picked:
        missing = _missing_dependencies(folder / Path(addon.toc).name, present)
        if missing:
            notes.append(_dependency_note(addon.name, missing))
    notes.extend(_pkgmeta_notes(base, picked))
    return Found(addons=tuple(addon for addon, _folder in picked), notes=tuple(notes))


# ------------------------------------------------------------------ where


def unwrap(root: Path) -> Path:
    """`root`, or the lone folder it is wrapped in (up to `UNWRAP_LEVELS` deep)."""
    here = root
    for _ in range(UNWRAP_LEVELS):
        if _tocs(here):
            return here
        visible = _visible(here)
        if len(visible) != 1 or not _is_real_dir(visible[0]):
            return here
        here = visible[0]
    return here


def _candidates(base: Path) -> list[Path]:
    """The add-on folders under `base`: itself when it holds a toc, else its add-on children."""
    if _tocs(base):
        return [base]
    found: list[Path] = []
    for parent in _addon_parents(base):
        for child in _visible(parent):
            if _is_real_dir(child) and _tocs(child) and child not in found:
                found.append(child)
    return found


def _addon_parents(base: Path) -> list[Path]:
    """The top folder, then each add-on folder (`addons/`, `Interface/AddOns/`, ...) present."""
    parents = [base]
    for wanted in ADDON_FOLDERS:
        here = base
        for part in wanted.split("/"):
            match = next(
                (p for p in _visible(here) if _is_real_dir(p) and p.name.casefold() == part),
                None,
            )
            if match is None:
                break
            here = match
        else:
            if here not in parents:
                parents.append(here)
    return parents


def _visible(folder: Path) -> list[Path]:
    if not _is_real_dir(folder):
        return []
    return sorted(
        (p for p in folder.iterdir() if not _HIDDEN.match(p.name)),
        key=lambda p: (p.name.casefold(), p.name),
    )


def _is_real_dir(path: Path) -> bool:
    """A folder reached without a link: a reader never goes through one (`links`)."""
    return not links.is_link(path) and path.is_dir()


def _tocs(folder: Path) -> list[Path]:
    if not _is_real_dir(folder):
        return []
    return sorted(
        (
            p
            for p in folder.iterdir()
            if p.suffix.casefold() == ".toc" and not links.is_link(p) and p.is_file()
        ),
        key=lambda p: (p.name.casefold(), p.name),
    )


# ------------------------------------------------------------------ which toc


def _choose(folder: Path, root: Path, client: int) -> tuple[Addon, str] | Refusal:
    """The toc `folder` installs from on this client, as an `Addon` and its note."""
    tocs = _tocs(folder)
    stems = {toc.stem.casefold() for toc in tocs}
    mains = [toc for toc in tocs if not _is_variant(toc.stem, stems)]
    if len(mains) > 1:
        own = [toc for toc in mains if toc.stem.casefold() == folder.name.casefold()]
        if len(own) != 1:
            listed = ", ".join(toc.name for toc in mains)
            return Refusal(
                f"{folder.name} has several .toc files ({listed}), and Yu'lon cannot tell which "
                f"one is the add-on. {NOTHING_CHANGED}"
            )
        mains = own
    main = mains[0]
    number = read_interface(main)
    verdict = _judge(main.stem, number, client)
    if not isinstance(verdict, Refusal):
        return _addon(main, folder, root, number), verdict
    fitting: list[tuple[int, Path]] = []
    low, high = band(client)
    for toc in tocs:
        found = _VARIANT.match(toc.stem)
        if found is None or found["base"].casefold() != main.stem.casefold():
            continue
        variant = read_interface(toc)
        if variant is not None and low <= variant <= high:
            fitting.append((variant, toc))
    if not fitting:
        return verdict
    best = max(fitting, key=lambda pair: (pair[0], pair[1].name))
    note = _judge(best[1].stem, best[0], client)
    assert not isinstance(note, Refusal)
    picked_note = (
        f"{best[1].stem} is installed from {best[1].name}, the add-on's own file for this game "
        f"version, because {main.name} is for another one."
    )
    return _addon(best[1], folder, root, best[0]), " ".join(n for n in (picked_note, note) if n)


def _addon(toc: Path, folder: Path, root: Path, number: int | None) -> Addon:
    src = folder.relative_to(root).as_posix()
    return Addon(
        name=toc.stem,
        src=src,
        toc=(Path(src) / toc.name).as_posix() if src != "." else toc.name,
        interface=number,
    )


def _is_variant(stem: str, stems: set[str]) -> bool:
    found = _VARIANT.match(stem)
    return found is not None and found["base"].casefold() in stems


def _judge(name: str, number: int | None, client: int) -> str | Refusal:
    """The note for an add-on of `number` on this client (empty when none), or its refusal."""
    low, high = band(client)
    yours = f"{CLIENT_NAMES.get(client, str(client))} (Interface {client})"
    if number is None:
        return (
            f"{name}'s .toc has no ## Interface line, so it does not say which game version it "
            "is for; Yu'lon installs it as it is."
        )
    if low <= number <= high:
        if number == high:
            return ""
        return (
            f"{name} is made for an older patch (Interface {number}), so the game may mark it "
            'out of date: tick "Load out of date AddOns" in the AddOns list on the character '
            "screen."
        )
    if number > high:
        return Refusal(
            f"{name} is made for {_made_for(number)} (Interface {number}); this server's client "
            f"is {yours}. {NOTHING_CHANGED}"
        )
    return Refusal(
        f"{name} is made for an older game (Interface {number}); this client is {yours}, and "
        f"an add-on from another expansion does not work in it. {NOTHING_CHANGED}"
    )


def _made_for(number: int) -> str:
    """The game an Interface number above this client's band belongs to, in a player's words."""
    if number <= 11200:
        return "the original game (1.x)"
    if number < 11500:
        return "WoW Classic"
    if number < 20000:
        return "WoW Classic Era"
    if number <= 20400:
        return "The Burning Crusade"
    if number < 30000:
        return "Burning Crusade Classic"
    if number <= 30300:
        return "Wrath of the Lich King"
    if number < 40000:
        return "Wrath of the Lich King Classic"
    if number < 50000:
        return "Cataclysm"
    if number < 60000:
        return "Mists of Pandaria"
    return "a later version of the game"


# ------------------------------------------------------------------ names


def _name_refusal(name: str, shipped: Mapping[str, str]) -> str:
    """Why `name` cannot be an outside add-on's folder in this client, or empty."""
    if name.casefold().startswith("blizzard_"):
        return (
            f"{name} is one of the game's own add-on names, so Yu'lon will not put another one "
            f"there. {NOTHING_CHANGED}"
        )
    folded = {key.casefold(): item for key, item in shipped.items()}
    item = folded.get(name.casefold())
    if item is not None:
        return (
            f"{name} is the name of an add-on Yu'lon already installs for this server ({item}). "
            f"Install that one from its row, or remove it first. {NOTHING_CHANGED}"
        )
    if (
        any(ch in _FOLDER_FORBIDDEN or ord(ch) < 0x20 for ch in name)
        or name.endswith((".", " "))
        or name.split(".")[0].upper() in _WINDOWS_DEVICES
    ):
        return (
            f"{name}.toc names a folder the game client cannot hold on Windows, so Yu'lon cannot "
            f"install it. {NOTHING_CHANGED}"
        )
    return ""


# ------------------------------------------------------------------ notes


def _read_toc(toc: Path) -> str:
    with toc.open("rb") as handle:
        data = handle.read(TOC_READ_BYTES)
    return data.decode("utf-8-sig", errors="replace")


def _missing_dependencies(toc: Path, present: set[str]) -> list[str]:
    """The required add-ons the toc names that are neither found here nor in the client."""
    missing: list[str] = []
    for field in _FIELD.finditer(_read_toc(toc)):
        tag = field[1].strip().casefold()
        if not (tag.startswith("dep") or tag == "requireddeps"):
            continue  # the client reads any `## Dep...` tag, and RequiredDeps, as required
        for dep in (d.strip() for d in field[2].split(",")):
            if (
                dep
                and not dep.casefold().startswith("blizzard_")
                and dep.casefold() not in present
                and dep not in missing
            ):
                missing.append(dep)
    return missing


def _dependency_note(name: str, missing: list[str]) -> str:
    one = len(missing) == 1
    return (
        f"{name} needs {', '.join(missing)}, which Yu'lon did not find beside it or in the game "
        f"client; add {'it' if one else 'them'} too, or the game will not load {name}."
    )


def _pkgmeta_notes(base: Path, picked: list[tuple[Addon, Path]]) -> list[str]:
    """A warning per `.pkgmeta` whose `externals` folders this copy does not have.

    A repository packaged by the CurseForge/BigWigs packager keeps its libraries
    out of git and names them here; the packager adds them to the release zip. A
    branch archive or clone of such a repository lacks them, and the add-on fails
    in the game with a Lua error the player cannot connect to the download.
    """
    notes: list[str] = []
    folders: list[Path] = []
    for folder in (base, *(folder for _addon, folder in picked)):
        if folder not in folders:
            folders.append(folder)
    for folder in folders:
        meta = folder / ".pkgmeta"
        if links.is_link(meta) or not meta.is_file():
            continue
        missing = [key for key in _externals(_read_toc(meta)) if not (folder / key).exists()]
        if not missing:
            continue
        names = [a.name for a, f in picked if f == folder] or [
            a.name for a, f in picked if folder in f.parents
        ]
        shown = ", ".join(missing[:5]) + (
            f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        )
        notes.append(
            f"{' and '.join(names)}: its libraries are added when its author packages a release "
            f"(its .pkgmeta lists {shown}), and this copy does not have them; use the release "
            "zip instead."
        )
    return notes


def _externals(text: str) -> list[str]:
    """The folder keys under a `.pkgmeta`'s top-level `externals:`, read without a YAML parser."""
    keys: list[str] = []
    inside = False
    indent: int | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        depth = len(raw) - len(raw.lstrip(" \t"))
        if depth == 0:
            inside = raw.split(":", 1)[0].strip() == "externals"
            indent = None
            continue
        if not inside:
            continue
        if indent is None:
            indent = depth
        if depth != indent or ":" not in raw:
            continue
        key = raw.split(":", 1)[0].strip().strip("'\"")
        if key and not key.startswith("-"):
            keys.append(key)
    return keys


def _nothing_found(base: Path, label: str) -> str:
    entries = [p.name for p in _visible(base)]
    if entries:
        shown = ", ".join(entries[:5]) + (
            f" and {len(entries) - 5} more" if len(entries) > 5 else ""
        )
        holds = f"It holds: {shown}."
    else:
        holds = "It is empty."
    return (
        f"Yu'lon found no add-on in {label}: an add-on is a folder with a .toc file of the same "
        f"name (for example pfUI/pfUI.toc). {holds} {NOTHING_CHANGED}"
    )
