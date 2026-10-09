"""Client add-ons from a link, a folder or a zip, for every game Yu'lon runs (T613 PR-2).

The route under the add-on box PR-3 draws on every Modules tab. No Qt: a refusal
is an exception carrying the player's sentence, and the slow steps (a download,
a clone, an unpack) take the view's cancel and progress callables.

**What is derived.** A client-only `mod` manifest: no SQL, no settings file,
nothing deployed, nothing patched, no DBC, no rebuild, one `client` step per
add-on found, each `dest: addons` under the add-on's own `.toc` name
(`addon_layout`). Its id is the slug of the add-on's name, whatever brought it,
so `pfUI` from a link, a folder and a zip is one item (`pfui`) and the second is
an update of the first. Where it came from is the `origin`: a link (with a
`source` that clones), a folder (with its path), or an archive (a zip on this
computer by its path, or a zip link by its url, and the zip's sha256 either way).

**Through the tab's own applier.** Install, Update and Remove go through the
applier the Modules tab holds, so a Remove takes the add-on's files back by
receipt (`Applier._take_back_addon_files()`) and a ready-to-play client is the
one written to (the client Play uses, owner 2026-10-09 Q3). Every press is
guarded first (`client_only_refusal()`): a manifest that carries anything but
client add-ons is refused before anything is cloned, copied or downloaded.
Centurion has no module applier, so it gets an add-on-only one
(`AddonOnlyApplier`), which refuses the same way itself. Wired once, in
`ControllerServices.for_entry()`.

**A git link is read twice.** The id is the add-on's name, and the name is in a
`.toc` that is only known once the repository is on disk, while the applier's
clone goes to `<clones>/<id>`. So a link is cloned shallowly into a staging folder
first, read, and removed; the install then clones it where it belongs, and reads
it again there (`completer()`), refusing if it no longer holds the same add-on.
An add-on repository is small; a wrong id is a second row for one add-on.
"""

from __future__ import annotations

import os
import re
import tempfile
import urllib.parse
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import ValidationError

from yulon import addon_archive, addon_layout, client_packs, module_source
from yulon.addon_archive import AddonRefusal, Staged
from yulon.addon_layout import NOTHING_CHANGED, Addon, Found
from yulon.apply import (
    Applier,
    ApplyRefusal,
    ApplyReport,
    CompletionRefused,
    FolderSource,
    UncheckedApproval,
)
from yulon.git import CloneSpec, Git
from yulon.log import get_logger
from yulon.manifest import (
    ALLOWED_REPO_HOSTS,
    Build,
    ClientFile,
    Manifest,
    Origin,
    Source,
    parse_manifest,
)
from yulon.manifest_store import ManifestStore

logger = get_logger(__name__)

MAX_ID = 64
"""As every derived id: a folder name and a list key."""

LINK_DESCRIPTION = "Client add-on (from a link you provided)."
FOLDER_DESCRIPTION = "Client add-on (copied from a folder you provided)."
ARCHIVE_DESCRIPTION = "Client add-on (unpacked from a zip you provided)."

NOTE_PREFIX = "Add-on: "
"""How a manifest's notes mark what the add-on reader said (an older patch, a missing library)."""

_RELEASES = re.compile(r"/releases(?:/latest)?/?$")


def item_id_for(name: str) -> str | None:
    """The id an add-on named `name` is kept under: its slug (`pfUI` → `pfui`), or None."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug if slug and len(slug) <= MAX_ID else None


def item_name(addons: Sequence[Addon]) -> str:
    """The item's name for the add-ons one source holds: the one every other name starts with.

    `Bagnon` beside `Bagnon_Config` and `Bagnon_GuildBank` is Bagnon. Without
    such a name, the first in the reader's order.
    """
    names = [addon.name for addon in addons]
    common = [
        name
        for name in names
        if all(other.casefold().startswith(name.casefold()) for other in names)
    ]
    return min(common, key=len) if common else names[0]


def client_only_refusal(manifest: Manifest) -> str:
    """Why `manifest` is not a client add-on this route may apply, or empty.

    Asked before every Install, Update and Remove, before anything is cloned:
    the add-on route never runs SQL, writes a settings file, deploys or patches
    a server file, copies DBCs, or asks for a rebuild, and its applier on a game
    with no module support (Centurion) must not start to.
    """
    carried: list[str] = []
    if manifest.type != "mod":
        carried.append(f"a {manifest.type} for the server")
    if manifest.sql:
        carried.append("database changes")
    if manifest.conf:
        carried.append("settings files")
    if manifest.deploy:
        carried.append("files for the server")
    if manifest.patches:
        carried.append("patches")
    if manifest.server_dbc:
        carried.append("server DBC files")
    if manifest.build.rebuild:
        carried.append("a rebuild")
    if manifest.folders:
        carried.append("server folders")
    if any(step.dest != "addons" for step in manifest.client):
        carried.append("game client files outside Interface/AddOns")
    if not carried:
        return ""
    return (
        f"{manifest.name} is not only a client add-on: it carries {', '.join(carried)}, and "
        f"Yu'lon's add-on route installs add-ons alone. {NOTHING_CHANGED}"
    )


def is_client_addon(manifest: Manifest) -> bool:
    """Whether `manifest` is an outside client add-on: derived, and add-ons alone."""
    return manifest.origin is not None and not client_only_refusal(manifest)


def notes_of(manifest: Manifest) -> tuple[str, ...]:
    """What the add-on reader said when the item was last read, one sentence per line."""
    return tuple(
        note[len(NOTE_PREFIX) :] for note in manifest.notes if note.startswith(NOTE_PREFIX)
    )


class AddonOnlyApplier(Applier):
    """An applier that applies client add-ons and nothing else (Centurion, T613 PR-2).

    Centurion's Modules tab has no module applier: its core takes none of
    Yu'lon's modules. Its add-on box still needs Install, Update and Remove into
    the client Play uses, with receipts; every other kind of manifest is refused
    here, before anything is touched, whoever calls.
    """

    def _only_addons(self, manifest: Manifest) -> None:
        said = client_only_refusal(manifest)
        if said:
            raise ApplyRefusal(said)

    def install(self, manifest: Manifest, *args: object, **kwargs: object) -> ApplyReport:
        self._only_addons(manifest)
        return super().install(manifest, *args, **kwargs)  # type: ignore[arg-type]

    def update(self, manifest: Manifest, *args: object, **kwargs: object) -> ApplyReport:
        self._only_addons(manifest)
        return super().update(manifest, *args, **kwargs)  # type: ignore[arg-type]

    def remove(self, manifest: Manifest, *args: object, **kwargs: object) -> ApplyReport:
        self._only_addons(manifest)
        return super().remove(manifest, *args, **kwargs)  # type: ignore[arg-type]

    def configure(self, manifest: Manifest, *args: object, **kwargs: object) -> ApplyReport:
        self._only_addons(manifest)
        return super().configure(manifest, *args, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Prepared:
    """An add-on read and checked, ready to install: its manifest and what it came as.

    `folder` is what the applier copies into the item's folder (the player's own
    folder, or the staging folder a zip was unpacked into); None for a git link,
    which the applier clones. `discard()` once installed or declined.
    """

    manifest: Manifest
    notes: tuple[str, ...] = ()
    folder: Path | None = None
    staged: Staged | None = None

    def discard(self) -> None:
        if self.staged is not None:
            self.staged.discard()


StageClone = Callable[[CloneSpec], None]


@dataclass
class ClientAddons:
    """One server's add-on route: derive, install, update and remove outside client add-ons.

    `applier` is the tab's (or Centurion's add-on-only one); `interface` the
    client's `## Interface:` number (`catalog.Client.addon_interface`);
    `shipped` the add-on folder names this game's shipped items install, any
    case, to the item that installs them; `shipped_ids` the `mod` ids it ships.
    """

    applier: Applier
    game: str
    interface: int
    shipped: Mapping[str, str]
    shipped_ids: Collection[str] = ()
    user_root: Path | None = None
    today: Callable[[], date] = date.today
    opener: client_packs.Opener = client_packs._open
    stage_clone: StageClone | None = None
    """How a git link is cloned for its first read; the applier's own git when None."""

    # ------------------------------------------------------------------ where things are

    def _user_root(self) -> Path:
        if self.user_root is not None:
            return self.user_root
        from yulon.controller_wow_wotlk.modules import user_manifests_dir

        return user_manifests_dir()

    def store(self) -> ManifestStore:
        """This game's user layer alone: the records of what the player brought."""
        return ManifestStore(Path(os.devnull), self.game, self._user_root(), shipped=False)

    def installed(self) -> list[Manifest]:
        """The outside add-ons recorded for this game, in id order; a broken record is skipped."""
        return [m for m in self.store().load_all("mod") if is_client_addon(m)]

    def _installed_names(self) -> list[str]:
        """The add-on folders in the client the applier writes to, for the dependency notes."""
        client = self.applier.client_dir
        if client is None:
            return []
        addons = client / "Interface" / "AddOns"
        try:
            return [p.name for p in addons.iterdir() if p.is_dir()]
        except OSError:
            return []

    # ------------------------------------------------------------------ reading a source

    def _read(self, root: Path, label: str) -> tuple[Found, str]:
        """The add-ons `root` holds, checked, and the item's name; raises with the sentence."""
        found = addon_layout.find_addons(
            root,
            interface=self.interface,
            shipped=self.shipped,
            installed=self._installed_names(),
            label=label,
        )
        if isinstance(found, addon_layout.Refusal):
            raise AddonRefusal(found.sentence)
        for addon in found.addons:
            addon_archive.check_folder(root if addon.src == "." else root / addon.src)
        return found, item_name(found.addons)

    def _manifest(
        self,
        found: Found,
        name: str,
        *,
        origin: Origin,
        source: Source | None,
        description: str,
        came_from: str,
    ) -> Manifest:
        item_id = item_id_for(name)
        if item_id is None:
            raise AddonRefusal(
                f"The add-on is named {name!r}, which gives Yu'lon no name to keep it under: it "
                f"needs letters or digits, at most {MAX_ID}. {NOTHING_CHANGED}"
            )
        if item_id in self.shipped_ids:
            raise AddonRefusal(
                f"{name} is an item Yu'lon already ships for this server: install it from its "
                f"row on the Modules tab. {NOTHING_CHANGED}"
            )
        return parse_manifest(
            {
                "id": item_id,
                "name": name,
                "type": "mod",
                "game": self.game,
                "description": description,
                "source": source.model_dump() if source is not None else None,
                "origin": origin.model_dump(),
                "build": Build(rebuild=False, restart=False).model_dump(),
                "client": [_step(addon).model_dump() for addon in found.addons],
                "notes": [
                    f"Derived by Yu'lon from {came_from} on {self.today().isoformat()}; nothing "
                    "here was written by the add-on's author.",
                    *(NOTE_PREFIX + note for note in found.notes),
                ],
            }
        )

    def from_folder(self, path: Path) -> Prepared:
        """An add-on folder (or a folder of add-ons) on this computer, read before any copy."""
        if not path.is_dir():
            raise AddonRefusal(f"{path} is not a folder Yu'lon can read. {NOTHING_CHANGED}")
        addon_archive.check_folder(path)
        found, name = self._read(path, path.name)
        origin = Origin(kind="folder", path=str(path), added=self.today().isoformat())
        manifest = self._manifest(
            found,
            name,
            origin=origin,
            source=None,
            description=FOLDER_DESCRIPTION,
            came_from=str(path),
        )
        return Prepared(manifest=manifest, notes=found.notes, folder=path)

    def from_zip(self, path: Path, *, cancelled: Callable[[], bool] = lambda: False) -> Prepared:
        """A zip on this computer, unpacked into staging and read there."""
        staged = addon_archive.stage_zip(path, cancelled=cancelled)
        origin = Origin(
            kind="archive", path=str(path), sha256=staged.sha256, added=self.today().isoformat()
        )
        return self._from_staged(staged, path.name, origin, came_from=str(path))

    def from_link(
        self,
        url: str,
        *,
        progress: client_packs.Progress | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> Prepared:
        """A zip link (downloaded and unpacked) or a repository link (cloned, read, let go).

        Off the GUI thread: both go to the network. A link ending in `.zip` is a
        file; anything else must be a repository on GitHub, GitLab or Codeberg, and
        a GitHub `.../releases` link follows the newest release from then on.
        """
        url = url.strip()
        if PurePosixPath(urllib.parse.urlsplit(url).path).suffix.casefold() == ".zip":
            staged = addon_archive.stage_link(
                url, opener=self.opener, progress=progress, cancelled=cancelled
            )
            origin = Origin(
                kind="archive", url=url, sha256=staged.sha256, added=self.today().isoformat()
            )
            label = PurePosixPath(urllib.parse.urlsplit(url).path).name
            return self._from_staged(staged, label, origin, came_from=url)
        source = repository_source(url)
        folder = self._stage_clone(source)
        try:
            label = source.repo.rstrip("/").rsplit("/", 1)[-1]
            found, name = self._read(folder, label)
        finally:
            addon_archive._remove(folder.parent)
        origin = Origin(kind="link", added=self.today().isoformat())
        manifest = self._manifest(
            found, name, origin=origin, source=source, description=LINK_DESCRIPTION, came_from=url
        )
        return Prepared(manifest=manifest, notes=found.notes)

    def _from_staged(
        self, staged: Staged, label: str, origin: Origin, *, came_from: str
    ) -> Prepared:
        try:
            found, name = self._read(staged.root, label)
            manifest = self._manifest(
                found,
                name,
                origin=origin,
                source=None,
                description=ARCHIVE_DESCRIPTION,
                came_from=came_from,
            )
        except BaseException:
            staged.discard()
            raise
        return Prepared(manifest=manifest, notes=found.notes, folder=staged.root, staged=staged)

    def _stage_clone(self, source: Source) -> Path:
        """A shallow clone of `source` in a new staging folder, for its first read."""
        parent = addon_archive.staging_dir()
        try:
            parent.mkdir(parents=True, exist_ok=True)
            dest = Path(tempfile.mkdtemp(dir=parent)) / "clone"
        except OSError as exc:
            raise AddonRefusal(addon_archive._os_sentence(exc, parent)) from exc
        clone = self.stage_clone if self.stage_clone is not None else self.applier.git.clone
        try:
            clone(CloneSpec(url=source.url, dest=dest, branch=source.branch))
        except Exception as exc:
            addon_archive._remove(dest.parent)
            raise AddonRefusal(
                f"Yu'lon could not fetch {source.url} to read it ({exc}). Check the link and the "
                f"connection, then try again. {NOTHING_CHANGED}"
            ) from exc
        return dest

    # ------------------------------------------------------------------ the presses

    def _guard(self, manifest: Manifest) -> None:
        said = client_only_refusal(manifest)
        if said:
            raise ApplyRefusal(said)

    def completer(self, manifest: Manifest, clone: Path) -> Manifest:
        """The applier's completion for an add-on: read the folder again, record what it holds.

        Every other manifest is handed back as it is: the hook is the applier's,
        and only an outside client add-on is this route's to read. A folder that
        no longer holds the same add-on (its author renamed its `.toc`) is
        refused, and a first install's folder is taken back by the applier.
        """
        if not is_client_addon(manifest):
            return manifest
        try:
            found, name = self._read(clone, manifest.name)
        except AddonRefusal as refused:
            raise CompletionRefused(str(refused).removesuffix(" " + NOTHING_CHANGED)) from None
        if item_id_for(name) != manifest.id:
            raise CompletionRefused(
                f"{manifest.name}'s source now holds the add-on {name} instead, so Yu'lon will "
                f"not put it under {manifest.name}'s name: add it as an add-on of its own."
            )
        completed = parse_manifest(
            {
                **manifest.model_dump(),
                "client": [_step(addon).model_dump() for addon in found.addons],
                "notes": [
                    *(n for n in manifest.notes if not n.startswith(NOTE_PREFIX)),
                    *(NOTE_PREFIX + note for note in found.notes),
                ],
            }
        )
        module_source.persist(self._user_root(), completed, shipped_ids=self.shipped_ids)
        return completed

    def install(self, prepared: Prepared, *, replacing: bool = False) -> ApplyReport:
        """Install what `from_*()` prepared through the tab's applier; the staging goes after."""
        manifest = prepared.manifest
        try:
            self._guard(manifest)
            folder = (
                FolderSource(prepared.folder, module_source.copy_folder)
                if prepared.folder is not None
                else None
            )
            first = not os.path.lexists(self.applier.clone_dir(manifest))
            recorded = module_source.recorded(self._user_root(), manifest)
            try:
                report = self.applier.install(
                    manifest, None, folder=folder, complete=self.completer, replacing=replacing
                )
            except BaseException:
                # The applier took a refused first install's folder back; the record
                # this press wrote goes with it, never one an earlier press wrote.
                if first and not recorded and not os.path.lexists(self.applier.clone_dir(manifest)):
                    module_source.forget(self._user_root(), manifest)
                raise
        finally:
            prepared.discard()
        return _with_notes(report, prepared.notes)

    def update(
        self,
        manifest: Manifest,
        *,
        progress: client_packs.Progress | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        approved: UncheckedApproval | None = None,
    ) -> ApplyReport:
        """Bring an installed add-on up to date from where it came from.

        A link's clone is fetched by the applier's own Update, then read again. A
        zip link is downloaded again and installed over the old copy when its
        bytes changed. A folder or a zip on this computer is read again from
        where it was, so choosing a newer copy there is how it is updated.
        """
        self._guard(manifest)
        origin = manifest.origin
        if origin is None:
            raise ApplyRefusal(f"{manifest.name} is not an outside add-on. {NOTHING_CHANGED}")
        if origin.kind == "link":
            return self.applier.update(manifest, approved=approved)
        if origin.kind == "archive" and origin.url is not None:
            prepared = self.from_link(origin.url, progress=progress, cancelled=cancelled)
        elif origin.kind == "archive" and origin.path is not None:
            prepared = self.from_zip(Path(origin.path), cancelled=cancelled)
        else:
            assert origin.path is not None
            prepared = self.from_folder(Path(origin.path))
        if prepared.manifest.id != manifest.id:
            prepared.discard()
            raise ApplyRefusal(
                f"{origin.url or origin.path} now holds the add-on {prepared.manifest.name}, not "
                f"{manifest.name}, so Yu'lon did not update {manifest.name}. {NOTHING_CHANGED}"
            )
        new = prepared.manifest.origin
        if (
            origin.kind == "archive"
            and new is not None
            and new.sha256 == origin.sha256
            and os.path.lexists(self.applier.clone_dir(manifest))
        ):
            prepared.discard()
            return ApplyReport(
                action="install",
                item_id=manifest.id,
                family="mod",
                done=(f"{manifest.name} is already up to date: the zip is the one installed",),
            )
        return self.install(prepared, replacing=True)

    def remove(self, manifest: Manifest) -> ApplyReport:
        """Remove an outside add-on through the applier, then its record (after, never before)."""
        self._guard(manifest)
        report = self.applier.remove(manifest)
        self.forget(manifest)
        return report

    def forget(self, manifest: Manifest) -> bool:
        """Drop `manifest`'s record from the user layer; True if there was one."""
        return module_source.forget(self._user_root(), manifest)


def repository_source(url: str) -> Source:
    """The `Source` a repository link clones from: GitHub, GitLab or Codeberg, https only.

    `https://github.com/<o>/<r>/releases` (or `/releases/latest`) follows the
    repository's newest release; any other page under a repository is refused,
    because a link to one file or one branch's folder is not an add-on Yu'lon can
    keep up to date.
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    follow: Literal["branch", "releases"] = "branch"
    if host == "github.com" and _RELEASES.search(path):
        path = _RELEASES.sub("", path)
        follow = "releases"
    segments = [s for s in path.split("/") if s]
    if parts.scheme != "https" or host not in ALLOWED_REPO_HOSTS or len(segments) != 2:
        raise AddonRefusal(
            f"{url[:200]} is not a link Yu'lon can take an add-on from: a repository on "
            f"{', '.join(ALLOWED_REPO_HOSTS)} (https://github.com/<owner>/<name>), its "
            "releases page, or a link to a .zip file there. "
            f"{NOTHING_CHANGED}"
        )
    owner, name = segments
    name = name.removesuffix(".git")
    try:
        return Source(repo=f"https://{host}/{owner}/{name}", follow=follow)
    except ValidationError as exc:
        raise AddonRefusal(
            f"{url[:200]} is not a repository link Yu'lon can clone. {NOTHING_CHANGED}"
        ) from exc


def shipped_addons(manifests: Iterable[Manifest]) -> dict[str, str]:
    """The add-on folder names shipped items install (any case) → the item that installs them."""
    names: dict[str, str] = {}
    for manifest in manifests:
        for step in manifest.client:
            if step.dest == "addons":
                names[step.name or PurePosixPath(step.src).name] = manifest.name
    return names


def _step(addon: Addon) -> ClientFile:
    return ClientFile(src=addon.src, dest="addons", name=addon.name)


def _with_notes(report: ApplyReport, notes: Sequence[str]) -> ApplyReport:
    """The reader's notes said with the press's own lines: they are the player's to read."""
    return replace(report, done=(*report.done, *notes)) if notes else report


__all__ = [
    "AddonOnlyApplier",
    "ClientAddons",
    "Git",
    "Prepared",
    "client_only_refusal",
    "is_client_addon",
    "item_id_for",
    "item_name",
    "repository_source",
    "shipped_addons",
]
