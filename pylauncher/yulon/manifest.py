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
    """Whether installing/removing needs the worldserver rebuilt."""

    rebuild: bool = True


class SqlStep(_Strict):
    """One SQL application: a file/glob from the clone, or an inline statement.

    `applied_by="db-import"` means AzerothCore's own `ac-db-import` picks the
    files up on next start and ledgers them in the `updates` table (the default
    for C++ modules — applying those by hand breaks that tracking). `"direct"`
    means the app runs it via `mysql` itself (ALE mods, SQL mods). Inline
    `statement` templates may reference prompt keys as `{key}`.
    """

    db: Db
    path: str | None = Field(default=None, description="File or glob, relative to the clone.")
    statement: str | None = Field(default=None, description="Inline SQL (template).")
    when: When = "install"
    applied_by: Literal["db-import", "direct"] = "direct"
    note: str | None = None

    @model_validator(mode="after")
    def _exactly_one_body(self) -> SqlStep:
        if (self.path is None) == (self.statement is None):
            raise ValueError("SqlStep needs exactly one of `path` or `statement`")
        return self


class ConfKey(_Strict):
    """A config key the app sets or surfaces, with its default and the why."""

    key: str = Field(min_length=1)
    default: str | None = Field(
        default=None, description="Value written at configure time; template `{prompt_key}` ok."
    )
    note: str | None = None


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

    @model_validator(mode="after")
    def _choice_needs_choices(self) -> Prompt:
        if self.kind == "choice" and not self.choices:
            raise ValueError("kind='choice' needs a non-empty `choices`")
        if self.kind != "choice" and self.choices:
            raise ValueError("`choices` only valid with kind='choice'")
        return self


class Manifest(_Strict):
    """One installable item — a C++ module, an ALE Lua mod, a SQL mod, or a keg."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: Slug
    name: str = Field(min_length=1)
    type: ManifestType
    game: Slug
    description: str = ""
    source: Source | None = None
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

    @model_validator(mode="after")
    def _shape_by_type(self) -> Manifest:
        if self.type in ("module", "ale", "keg") and self.source is None:
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
