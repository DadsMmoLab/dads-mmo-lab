"""Every module setting this install has, and the file behind it (T43).

Three things live here and none of them knows what a widget is: the READER
(`rows_for`), which turns this game's manifests plus what is on disk into an
ordered list of settings; the WRITER (`write`), which changes named keys inside
a conf file and leaves everything else byte-for-byte; and the GUARD (`lint`),
the cheap "does this still look like a `.conf`?" pass the raw editor shows while
somebody types. `yulon/ui/widgets/tuning_panel.py` draws them and decides
nothing, exactly as `modules_panel.py` draws T42's rows.

**Why this exists.** Counted across `manifests/wow-wotlk`: 24 manifests declare
conf keys, 107 keys in all, and 73 of them carry no `default`. `apply.py`'s
`_conf()` declines to write a key with no default on purpose -- "which value
belongs in a user's core configuration is the catalog's sentence to write" --
so those 73 were settings the catalog named, the applier skipped, and nobody
could reach from inside the app. Installing `mod-transmog` on the live WotLK
box (`yulon-win11`, 2026-09-12) reported five of them in one line.

**The safety rule, which every ambiguity here is resolved by.** A key with no
`type` is a TEXT BOX -- never a switch, never a spinner. No bound is invented
where `min`/`max` are absent. A value that fails its own type is refused before
a byte is written. And `current` is never fabricated: a file that is missing or
unreadable answers `None`, and the row still lists with its default.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon.log import get_logger
from yulon.manifest import Manifest
from yulon.manifest_store import FAMILY_FILES

logger = get_logger(__name__)

Backend = Literal["conf", "lua", "other"]
"""Where a declared key actually lives.

`conf` is a worldserver-style `Key = Value` file the app may rewrite. `lua` is a
deployed script whose assignments the installer patches in place -- out of v1 by
T43 decision 5, because Yu'lon's ALE story differs enough from DML's to want its
own ticket. `other` is everything a `ConfFile.file` can name that is not a file
at all: `manifests/wow-wotlk/ale/paragon.json` points five keys at
`acore_ale.paragon_config (DB table)`.
"""

LUA_IS_NOT_IN_V1 = (
    "This setting lives in a deployed Lua script, not in a .conf file. Yu'lon shows it "
    "here so it is not hidden, but does not write it yet — patching a running ALE script "
    "is its own job with its own restart rules. Re-install the script to change it."
)
"""T43 decision 5, in the words the row shows. Listed, read-only, and never dropped."""

NOT_A_CONF_FILE = (
    "The catalog points this key at `{file}`, which is not a file on disk — it is stored "
    "somewhere else (a database table). Yu'lon can name it here but cannot edit it."
)

MORE_THAN_ONE_FILE = (
    "The catalog points this key at `{file}`, which matches more than one file. There is "
    "no single file to show or to write, so this one is read-only."
)

CONF_SUFFIX = ".conf"
LUA_SUFFIX = ".lua"


def _is_glob(path: str) -> bool:
    """`apply._is_glob`'s rule, spelled again rather than imported.

    `apply` imports half the app (docker, git, the runner); this module is
    imported by a panel and by tests that want neither. Six characters of
    duplication against a dependency edge that would drag a subprocess seam into
    a pure reader.
    """
    return any(ch in path for ch in "*?[")


def backend_of(file: str) -> Backend:
    """Which kind of thing the manifest's `file` names, by its own spelling."""
    low = file.lower()
    if low.endswith(CONF_SUFFIX):
        return "conf"
    if low.endswith(LUA_SUFFIX):
        return "lua"
    return "other"


def _read_only_reason(file: str, backend: Backend) -> str | None:
    """Why this row cannot be written, or `None` when it can.

    Ordered by what blocks hardest. A glob has no single file at all, so it
    could not be written even if its backend were writable; the Lua sentence
    comes next because it is the one a user is most likely to go looking for
    (`accountwide/*.lua` is both, and either answer would be true).
    """
    if _is_glob(file):
        return MORE_THAN_ONE_FILE.format(file=file)
    if backend == "lua":
        return LUA_IS_NOT_IN_V1
    if backend == "other":
        return NOT_A_CONF_FILE.format(file=file)
    return None


@dataclass(frozen=True)
class TuningRow:
    """One setting: what it is, what it says now, and whether it may be changed.

    `label` is never `None` -- the fallback to the key happens here, once, so
    the panel and the tests cannot spell it differently. `explain`, `type`,
    `min` and `max` stay `None` when the catalog is silent, and the panel reads
    that silence as "a text box and no prose" rather than filling it.
    """

    module_id: str
    module_name: str
    family: str
    file: str
    key: str
    label: str
    explain: str | None
    type: str | None
    min: int | None
    max: int | None
    default: str | None
    current: str | None
    installed: bool
    backend: Backend
    read_only_reason: str | None

    @property
    def editable(self) -> bool:
        return self.read_only_reason is None


def conf_value(text: str, key: str) -> str | None:
    """The LAST active setting of `key` in `text`, unquoted, or `None`.

    Column 0 and last-wins, both for `party.read_conf()`'s measured reasons: a
    pattern that also matched indented or commented lines read the shipped
    `mod_ale.conf.dist`'s own commented prose as its settings, and a server
    reading a file top to bottom takes the last assignment -- which is exactly
    the line `apply._set_conf_key()` appends when it corrects a key.
    """
    found: str | None = None
    for line in text.splitlines():
        head, sep, tail = line.partition("=")
        if sep and head.strip() == key and head[:1] not in ("#", " ", "\t"):
            found = tail.strip().strip('"')
    return found


def _read(path: Path) -> str | None:
    """The file's text, or `None` when it is not there or will not open.

    `None` rather than `""`: an empty file has said every key is absent, and a
    missing one has said nothing at all. The row draws them the same way -- no
    current value -- but only one of them is worth a message about the install.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug(f"tuning: could not read {path}: {exc}")
        return None


def rows_for(
    manifests: Iterable[Manifest],
    installed: Mapping[str, frozenset[str]],
    server_dir: Path,
) -> tuple[TuningRow, ...]:
    """Every setting this install can be tuned by, in the order the tab draws them.

    `manifests` is this game's catalog in the store's own order; `installed` is
    `apply.installed_clones()`'s answer, ids per family read from each family's
    OWN clone directory (T41). Rows come only from installed modules, and that
    is not a display filter: an uninstalled module has no deployed conf, so
    every value shown for it would be a value nothing reads.

    The order is family by family in `FAMILY_FILES` order, then the catalog's
    own order inside a family, then the manifest's own conf-file order, then its
    own key order. Four orderings and not one of them this module's invention --
    a catalog that reorders its keys reorders the cards, which is the only way
    the author gets to say what comes first.
    """
    rows: list[TuningRow] = []
    catalog = list(manifests)
    for kind in FAMILY_FILES:
        here = installed.get(kind, frozenset())
        for manifest in catalog:
            if manifest.type != kind or manifest.id not in here:
                continue
            for conf in manifest.conf:
                backend = backend_of(conf.file)
                reason = _read_only_reason(conf.file, backend)
                # Read once per FILE, not once per key: a conf with a dozen keys
                # is one open, and the whole tab is one pass over the install.
                text = (
                    None
                    if _is_glob(conf.file) or backend == "other"
                    else _read(server_dir / conf.file)
                )
                for key in conf.keys:
                    rows.append(
                        TuningRow(
                            module_id=manifest.id,
                            module_name=manifest.name,
                            family=manifest.type,
                            file=conf.file,
                            key=key.key,
                            label=key.label or key.key,
                            explain=key.explain,
                            type=key.type,
                            min=key.min,
                            max=key.max,
                            default=key.default,
                            current=None if text is None else conf_value(text, key.key),
                            installed=True,
                            backend=backend,
                            read_only_reason=reason,
                        )
                    )
    return tuple(rows)
