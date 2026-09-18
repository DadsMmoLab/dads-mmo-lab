"""The manifest schema: typed models for module / ALE / mod / keg JSON (roadmap 2.1).

This is the data contract the whole project rests on (README §6): everything
`wow-manage.sh` hard-codes per module — repo, SQL, conf files and keys, files
to deploy, patches, client files, prompts — is expressed here as a closed set
of declarative primitives, so adding content is a JSON file, never Python
(style-guide §3/§4). The models are game-agnostic; the `game` field and the
manifest directory decide which controller consumes them.

Validation is strict on purpose: unknown fields are rejected (a typo must not
silently become a no-op), ids/game are acronym-only slugs (style-guide §6), and
`Source.repo` is checked against `ALLOWED_REPO_HOSTS` (README §3a — no piracy
sources, ever).

`python -m yulon.manifest --dump-schema` prints the JSON Schema for the
language-neutral copy kept at `manifests/schema/manifest.schema.json`.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Literal[1] = 1

# Legitimate open-source forges a manifest may point at (README §3a). A bare
# `owner/name` slug means GitHub. Anything else is refused at parse time.
ALLOWED_REPO_HOSTS: tuple[str, ...] = ("github.com", "gitlab.com", "codeberg.org")

# kebab-case, lowercase, no leading/trailing/double hyphens (style-guide §6/§6a).
_SLUG = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_REPO_SLUG = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"

Slug = Annotated[str, Field(pattern=_SLUG, min_length=1)]
ManifestType = Literal["module", "ale", "mod", "keg"]
Db = Literal["auth", "characters", "world", "playerbots", "ale"]
When = Literal["install", "remove", "configure"]
ClientDest = Literal["addons", "interface", "data"]
PromptKind = Literal["string", "int", "float", "bool", "choice"]


class _Strict(BaseModel):
    """Base for every manifest model: unknown keys are an error, not ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(_Strict):
    """Where the content is cloned from. `repo` is `owner/name` (GitHub) or a URL."""

    repo: str = Field(min_length=3)
    branch: str | None = None
    sparse_path: str | None = Field(
        default=None, description="Subdirectory to sparse-checkout (kegs inside a larger repo)."
    )
    depth: int | None = Field(
        default=1,
        gt=0,
        description=(
            "Shallow-clone depth, or null for a full clone. Data because the warning in "
            "`git.CloneSpec` is a per-source fact: AzerothCore's core repo must say null, since "
            "its `genrev.cmake` reads the revision out of git history and a shallow clone hands "
            "a three-hour build the wrong answer."
        ),
    )
    rev: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{40}$",
        description=(
            "Commit to check out after the clone, or null for the branch tip. A PIN, not a "
            "ref: only a full lowercase SHA is accepted, because a tag can be moved and a pinned "
            "server must rebuild the same bytes, and because GitHub serves a fetch-by-hash only "
            "for the full object id. Honoured by `git.CloneSpec.rev` (roadmap 7.3)."
        ),
    )

    @field_validator("repo")
    @classmethod
    def _repo_is_allowed(cls, value: str) -> str:
        if "://" not in value:
            if not re.match(_REPO_SLUG, value) or value.endswith(".git"):
                raise ValueError(f"repo must be 'owner/name' (no .git, no spaces): {value!r}")
            return value
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in ALLOWED_REPO_HOSTS:
            raise ValueError(
                f"repo must be https on an allowed host {ALLOWED_REPO_HOSTS}, got {value!r}"
            )
        return value

    @property
    def url(self) -> str:
        """The clone URL (slugs resolve to GitHub)."""
        return self.repo if "://" in self.repo else f"https://github.com/{self.repo}.git"


class Build(_Strict):
    """What must happen after installing/removing before the change is live."""

    rebuild: bool = True
    restart: bool = Field(
        default=False,
        description=(
            "Declare that this item needs the worldserver restarted. `ApplyReport."
            "restart_recommended` is otherwise DERIVED — from NPCs, direct SQL and server "
            "DBCs, all of which reach the database or the data volume — and that derivation "
            "cannot see a conf write. An item whose whole content is `conf[].keys` reported "
            "'nothing further needed' while the value it had just written sat in a file the "
            "emulator reads once, at startup. On CMaNGOS that IS the whole shape of a module "
            "(roadmap 8.7b), so it is declared here rather than guessed at. False by default, "
            "and it never suppresses a derived yes — only adds one."
        ),
    )


class ExistsCheck(_Strict):
    """A read run against the server's own database, whose answer decides something.

    Declared here rather than coded in the applier because it is per-item
    knowledge (style-guide §4), and it exists because of what the AH bot modules
    do with a wrong answer: `AuctionHouseBot.GUID` naming no character is not an
    error inside the module, it is a module that quietly posts nothing — which
    from the outside is indistinguishable from "this module does not work"
    (measured on the owner's own install, 2026-09-07).

    Three fields, three users, and the model is shared on purpose: all three ask
    one SELECT and read "did a row come back". What DIFFERS is what the answer
    means, and that lives at the field, not here:

    * `Prompt.exists` — no row REFUSES the action (a bad answer);
    * `SqlStep.precondition` — no row SKIPS that one step, with `missing` said
      and nothing written (T63);
    * `SqlStep.verify` — no row REFUSES after the fact: the file ran and the
      database is not in the state it claims to produce (T63).

    `query` and `missing` are templates over the manifest's prompt keys for
    `Prompt.exists`; a `SqlStep` check is never rendered and must carry no
    `{field}` at all (`test_manifest.py`). `query` must be a single SELECT: the
    applier hands it to the READ half of the SQL seam, and a manifest is content
    rather than code, so the one thing it must not be able to do through this
    field is write.
    """

    db: Db
    query: str = Field(min_length=1)
    missing: str = Field(min_length=1, description="What to tell the user when no row came back.")

    @field_validator("query")
    @classmethod
    def _one_select(cls, value: str) -> str:
        if not value.strip().upper().startswith("SELECT "):
            raise ValueError(f"ExistsCheck.query must be a SELECT: {value!r}")
        if ";" in value.strip().rstrip(";"):
            raise ValueError(f"ExistsCheck.query must be ONE statement: {value!r}")
        return value


class SqlStep(_Strict):
    """One SQL application: a file/glob from the clone, or an inline statement.

    `applied_by="db-import"` means AzerothCore's own `ac-db-import` picks the
    files up on next start and ledgers them in the `updates` table (the default
    for C++ modules — applying those by hand breaks that tracking). `"direct"`
    means the app runs it via `mysql` itself (ALE mods, SQL mods). Inline
    `statement` templates may reference prompt keys as `{key}`.

    `precondition` and `verify` are T63's two halves of one problem: a direct
    step whose ORDER cannot be satisfied inside a single press. `mod-city-bots`
    imports its 400-citizen roster into `acore_playerbots`, and that file writes
    `playerbots_account_type` — a table mod-playerbots creates on the world
    server's FIRST start, which is necessarily after the rebuild this install
    only reports. So the step can be right and still be too early, and the two
    honest answers to that are *say so and do nothing* and *prove it worked*:

    * `precondition` — asked before the file is opened. No row (or a database
      that cannot be asked at all, which is what a schema that does not exist
      yet looks like) means the step is SKIPPED with `missing` in the report and
      not one row written. Install again after the missing thing exists; the
      import is idempotent by construction (the roster file drops and recreates
      its own table), so a repeat is a repair.
    * `verify` — asked after, once per entry, and each entry says its own
      sentence. No row RAISES: the file ran, so "skipped" would be a lie, and a
      module reported installed whose roster is half there is exactly the defect
      wow-manage's `city_bots_import_roster` verified 400/400 to catch.

    Both are `direct`-only. A `db-import` step is handed to another program on a
    later boot and nothing here is in a position to check either end of it, so
    declaring one there would be a field nothing reads.
    """

    db: Db
    path: str | None = Field(default=None, description="File or glob, relative to the clone.")
    statement: str | None = Field(default=None, description="Inline SQL (template).")
    when: When = "install"
    applied_by: Literal["db-import", "direct"] = "direct"
    note: str | None = None
    precondition: ExistsCheck | None = Field(
        default=None,
        description="Must return a row or this step is skipped, unrun, with `missing` said.",
    )
    verify: tuple[ExistsCheck, ...] = Field(
        default=(),
        description="Read after the step; an entry returning no row is a failure, by its own name.",
    )

    @model_validator(mode="after")
    def _exactly_one_body(self) -> SqlStep:
        if (self.path is None) == (self.statement is None):
            raise ValueError("SqlStep needs exactly one of `path` or `statement`")
        if self.applied_by != "direct" and (self.precondition is not None or self.verify):
            raise ValueError(
                "SqlStep.precondition/verify need `applied_by='direct'`: a db-import step is run "
                "by the core updater on a later boot, and this app checks neither end of it"
            )
        return self


class ConfKey(_Strict):
    """A config key the app sets or surfaces, with its default, the why, and how to edit it.

    The four T43 fields below are what the Tuning tab draws a control from, and
    they are on the manifest rather than in a second registry on purpose: one
    file describes a module's install AND its tuning, so the two cannot name
    different keys (T43 decision 2).

    Every one of them is OPTIONAL, and the absence of each is a decision rather
    than a gap:

    * no `label` -> the tab shows the key itself, which is what the module's own
      documentation calls it;
    * no `explain` -> the tab shows nothing. The author's silence is honest;
      invented prose about somebody else's module is not;
    * **no `type` -> a text box.** Never a switch, never a spinner. This is the
      safety rule of the whole feature: a wrong `type` writes a wrong value into
      somebody's live server, and "unknown" has to degrade to the one control
      that can express anything;
    * no `min`/`max` on an `int` -> no clamping is invented. The module decides
      what it accepts; a bound this catalog made up would refuse a value the
      module is happy with.
    """

    key: str = Field(min_length=1)
    default: str | None = Field(
        default=None, description="Value written at configure time; template `{prompt_key}` ok."
    )
    note: str | None = None
    label: str | None = Field(
        default=None, description="Short name for the control; the key itself when absent."
    )
    explain: str | None = Field(
        default=None, description="The module author's own sentence about this key."
    )
    type: Literal["bool", "int", "list", "text"] | None = Field(
        default=None, description="Which control edits this key. Absent means a text box."
    )
    min: int | None = Field(default=None, description="`int` keys only; no bound is invented.")
    max: int | None = Field(default=None, description="`int` keys only; no bound is invented.")

    @model_validator(mode="after")
    def _bounds_belong_to_an_int(self) -> ConfKey:
        """A `min`/`max` anywhere but on an `int` is refused at parse time.

        Not ignored, because a reader who saw `min` on a `bool` would believe
        it: the field would say the catalog had thought about a range, and the
        tab would draw a switch that no bound could ever apply to. A key with
        no `type` at all is included -- it renders as a text box, and a bound on
        a text box is the same lie.

        The bound is also checked for being satisfiable: `min` above `max` is a
        range no value meets, and the first thing it would break is the refusal
        in `tuning.check()`, which would then decline every value the user
        typed while naming a range that reads reasonable.
        """
        if (self.min is not None or self.max is not None) and self.type != "int":
            named = self.type or "no type"
            raise ValueError(f"{self.key}: `min`/`max` are for an `int` key; this one is {named}")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"{self.key}: min {self.min} is above max {self.max}")
        return self


class ConfFile(_Strict):
    """A config file belonging to the item: where it lives, its template, its keys.

    `file` is relative to the server dir (e.g. `env/dist/etc/modules/mod_ahbot.conf`);
    `template` is relative to the clone (the `.conf.dist` copied on activation).
    Lua-script config lives in the deployed `.lua` itself — then `file` points
    at it and keys are patched in place.
    """

    file: str = Field(min_length=1)
    template: str | None = None
    keys: tuple[ConfKey, ...] = ()


class Deploy(_Strict):
    """Copy `src` (relative to the clone) to `dest` (relative to the server dir)."""

    src: str = Field(min_length=1)
    dest: str = Field(min_length=1)
    rename: tuple[tuple[str, str], ...] = Field(
        default=(), description="(from, to) basename renames applied after the copy."
    )

    @model_validator(mode="after")
    def _rename_needs_a_directory(self) -> Deploy:
        """`rename` is meaningless on a single-file deploy, and corrupted it.

        For a file src, `_deploy_target()` already returns the full destination
        FILENAME, so the applier's `(target / old).replace(target / new)` builds
        a path inside the copied file -- `.../LootPet2.lua/LootPet.lua` -- and
        the install dies with `NotADirectoryError` AFTER the copy, leaving the
        file in place under its old name.

        The `lootpet` manifest was merged to `Yulon` in exactly that shape
        (#160) and every Loot Pet install from that tree failed; measured on
        936beda5, 2026-09-14. Refused here so a wrong manifest does not
        load at all, rather than failing part way through an install that has
        already written into the server folder.
        """
        if self.rename and not self.src.endswith("/"):
            raise ValueError(
                f"deploy src {self.src!r} is a single file, and `rename` only applies to a "
                f"directory src (one ending in '/'). Name the file correctly at the source "
                f"instead."
            )
        return self


class Patch(_Strict):
    """A find/replace applied to a file after deploy/clone (the script's `sed -i`s).

    `file` is relative to the server dir for deployed files, or to the clone when
    `in_clone` is true (e.g. a source patch before a rebuild). `replace` may use
    `{prompt_key}` templates. Regex patches use Python `re` syntax.
    """

    file: str = Field(min_length=1)
    find: str = Field(min_length=1)
    replace: str
    regex: bool = False
    in_clone: bool = False
    when: When = "install"
    note: str | None = None


class ClientFile(_Strict):
    """Open-source client-side files (an addon, a patch) copied into the USER'S client.

    README §3a: the app never ships game assets — these come from the item's
    own open-source repo and go into the user-supplied client directory.
    """

    src: str = Field(min_length=1)
    dest: ClientDest
    name: str | None = Field(default=None, description="Subfolder name for `addons` dests.")


class ServerDbc(_Strict):
    """DBC files from the clone copied into the server's `data/dbc/` volume."""

    src: str = Field(min_length=1)


class Npc(_Strict):
    """An NPC the item adds that the user may need to spawn (`.npc add <entry>`)."""

    entry: int = Field(gt=0)
    name: str = Field(min_length=1)
    auto_spawned: bool = False
    note: str | None = None


class Prompt(_Strict):
    """A value asked of the user at configure time, referenced as `{key}` elsewhere."""

    key: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    question: str = Field(min_length=1)
    kind: PromptKind = "string"
    default: str | None = None
    choices: tuple[str, ...] = ()
    exists: ExistsCheck | None = Field(
        default=None,
        description="A row that must be found before this answer is used; see `ExistsCheck`.",
    )

    @model_validator(mode="after")
    def _choice_needs_choices(self) -> Prompt:
        if self.kind == "choice" and not self.choices:
            raise ValueError("kind='choice' needs a non-empty `choices`")
        if self.kind != "choice" and self.choices:
            raise ValueError("`choices` only valid with kind='choice'")
        return self


class Origin(_Strict):
    """How a CUSTOM manifest came to exist: derived by this app, not shipped by the project.

    Present only on a manifest `yulon.module_source` derived from a link the user
    pasted or a folder the user chose. Every file under `manifests/` has
    `origin=None`, and `test_origin_is_optional_and_every_shipped_manifest_has_none`
    asserts that over the tree rather than trusting it.

    It exists because a local folder is **not** a `Source`. `Source` means "where
    content is cloned from" and its `url` property feeds `git.CloneSpec`; a path
    is not a clone URL, `same_repo()`/`remote_url()` have no meaning for a copy,
    and `README.md` §3a treats `repo` as the piracy fence, which the user's own
    disk should neither need to pass nor be allowed to weaken. So a folder-derived
    manifest carries no source at all and records where it came from here.

    `path` is the folder the module was copied from, for a human reading the file
    and for choosing the same folder again; it is `None` for a link, whose source
    already says where it came from. Nothing in the applier reads this model —
    ownership is decided by the clone claim, as it is for a shipped module.
    """

    kind: Literal["link", "folder"]
    path: str | None = Field(
        default=None, description="The folder it was copied from (kind='folder'); null for a link."
    )
    added: str = Field(min_length=1, description="ISO date the derivation happened.")


class Manifest(_Strict):
    """One installable item — a C++ module, an ALE Lua mod, a SQL mod, or a keg."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: Slug
    name: str = Field(min_length=1)
    type: ManifestType
    game: Slug
    description: str = ""
    source: Source | None = None
    origin: Origin | None = Field(
        default=None, description="Set only on a manifest this app derived; see `Origin`."
    )
    build: Build = Build(rebuild=False)
    requires: tuple[Slug, ...] = ()
    conflicts_with: tuple[Slug, ...] = ()
    sql: tuple[SqlStep, ...] = ()
    conf: tuple[ConfFile, ...] = ()
    deploy: tuple[Deploy, ...] = ()
    patches: tuple[Patch, ...] = ()
    client: tuple[ClientFile, ...] = ()
    server_dbc: tuple[ServerDbc, ...] = ()
    npcs: tuple[Npc, ...] = ()
    prompts: tuple[Prompt, ...] = ()
    notes: tuple[str, ...] = Field(
        default=(), description="Tacit knowledge worth showing a human; not machine-read."
    )

    @property
    def _copied_from_a_folder(self) -> bool:
        """A C++ module this app copied off the user's own disk — the one sourceless module.

        One clause wide on purpose. A module derived from a LINK has a source and
        must still carry it, so this cannot be reached by dropping the field and
        claiming an origin; and ALE scripts and kegs are always cloned, so they
        are not relaxed at all.
        """
        return self.type == "module" and self.origin is not None and self.origin.kind == "folder"

    @model_validator(mode="after")
    def _shape_by_type(self) -> Manifest:
        if (
            self.type in ("module", "ale", "keg")
            and self.source is None
            and not self._copied_from_a_folder
        ):
            raise ValueError(f"type={self.type!r} requires a `source`")
        if self.type == "keg" and (self.source is None or self.source.sparse_path is None):
            raise ValueError("type='keg' requires `source.sparse_path` (kegs live inside a repo)")
        if self.id in self.requires or self.id in self.conflicts_with:
            raise ValueError("an item cannot require or conflict with itself")
        return self


class Index(_Strict):
    """A per-category index: the ordered list of manifest ids in that directory."""

    schema_version: Literal[1] = SCHEMA_VERSION
    game: Slug
    type: ManifestType
    items: tuple[Slug, ...] = ()


def parse_manifest(data: object) -> Manifest:
    """Validate raw JSON-decoded data into a `Manifest` (raises `pydantic.ValidationError`)."""
    return Manifest.model_validate(data)


def parse_index(data: object) -> Index:
    """Validate raw JSON-decoded data into an `Index`."""
    return Index.model_validate(data)


def json_schema() -> dict[str, object]:
    """The JSON Schema for `Manifest` (for the checked-in, language-neutral copy)."""
    return Manifest.model_json_schema()


if __name__ == "__main__":
    if "--dump-schema" in sys.argv[1:]:
        json.dump(json_schema(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stderr.write("usage: python -m yulon.manifest --dump-schema\n")
        sys.exit(2)
