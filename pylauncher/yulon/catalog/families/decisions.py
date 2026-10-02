"""Every place the code branches on a server family, and what each family gets there (T179).

Before T179 most of these sites read `if family == "azerothcore": … elif family ==
"cmangos": … else: nothing`. Two families filled both arms; a third falls into the
`else` at every one of them, and the feature is simply absent, with nothing
failing. This registry is where that cannot happen silently: each site names, per
family in `NativeInstall.family`, one of

* `supported` -- the site serves this family (its own branch, or a default that is
  right for it);
* `not-available: <reason>` -- the feature is deliberately not offered on this
  family, and `reason` is the sentence a player is told;
* `not-applicable: <why>` -- this family never reaches the site (it sits inside
  another family's engine, reads another family's block, or the family keeps the
  same fact somewhere else), so there is nothing to offer or withhold;
* `pending: Task N` -- `trinitycore` only, while T179 is being built: the site
  will serve it, in the plan task named. `TRINITYCORE_PENDING` turns that
  allowance off at the end of T179 (Task 8).

`tests/test_family_decisions.py` holds it: every site decides every family, and an
AST scan of `yulon/` finds every family branch -- a compare against a family's
name, a family block's attribute, a dict keyed by family names, an isinstance on
a family's class, a `case` on a family's name -- and fails for one that is not a
site here. A few sites branch on an id or a data field the scan cannot see; they
are listed with `scanned=False`, and the test keeps their scopes from rotting.

A site is a scope -- `module` and the qualified name of the def or class holding
the branch (`<module>` for a top-level one) -- not a line, so it survives edits.
Data only: no import of the code it describes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["supported", "not-available", "not-applicable", "pending"]

TRINITYCORE_PENDING = True
"""Whether `pending` is still accepted for `trinitycore`. Turned off at the end of T179."""


@dataclass(frozen=True)
class Decision:
    """What one family gets at one site, and why when it gets less than the feature."""

    kind: Kind
    note: str = ""


def supported(note: str = "") -> Decision:
    return Decision("supported", note)


def not_available(reason: str) -> Decision:
    return Decision("not-available", reason)


def not_applicable(why: str) -> Decision:
    return Decision("not-applicable", why)


def pending(task: str) -> Decision:
    return Decision("pending", task)


@dataclass(frozen=True)
class Site:
    """One scope that branches on a family, and every family's decision there."""

    module: str
    scope: str
    what: str
    decisions: Mapping[str, Decision] = field(default_factory=dict)
    scanned: bool = True


_NOT_THE_ENGINE = "never reached: this is inside the CMaNGOS engine, which only its own entries run"
_CMANGOS_CONTROLLER = "never reached: only this CMaNGOS game's own controller package reads it"
_NO_SQL_PLAN = "AzerothCore's own database updater applies its updates; its block has no SQL plan"
_NO_CONF_TABLE = "AzerothCore's confs are made by its image from their .dist; it has no conf table"
_COUNT_IN_CONF = "this family's random-bot count is in a conf file, not the override's environment"
_PARTY_REASON = "My Party needs AzerothCore's Lua bridge; it is WotLK-only."
_DASHBOARD_REASON = (
    "The bot dashboard belongs to the Tortoise bot module, which this server does not run."
)


def _all(decision: Decision) -> dict[str, Decision]:
    return {"azerothcore": decision, "cmangos": decision, "trinitycore": decision}


def _cmangos_only(why_others: str, trinitycore: Decision | None = None) -> dict[str, Decision]:
    return {
        "azerothcore": not_applicable(why_others),
        "cmangos": supported(),
        "trinitycore": trinitycore or not_applicable(why_others),
    }


FAMILY_DECISIONS: tuple[Site, ...] = (
    # -- the catalog model ----------------------------------------------------------
    Site(
        "yulon.catalog.catalog",
        "NativeInstall._exactly_the_family_block",
        "family names exactly its block; the extract image is a built one",
        _all(supported()),
    ),
    Site(
        "yulon.catalog.catalog",
        "CatalogEntry._every_patch_names_a_source_this_entry_clones",
        "a CMaNGOS source patch names a cloned dest",
        _cmangos_only("this family's block carries no source patches"),
    ),
    Site(
        "yulon.catalog.catalog",
        "CatalogEntry._the_trinitycore_block_agrees_with_the_entry",
        "the TrinityCore checkout is a cloned dest; renames land on the entry's schemas",
        {
            "azerothcore": not_applicable("only a trinitycore block has a checkout and renames"),
            "cmangos": not_applicable("only a trinitycore block has a checkout and renames"),
            "trinitycore": supported(),
        },
    ),
    # -- engine dispatch ---------------------------------------------------------------
    Site(
        "yulon.catalog.families.__init__",
        "<module>",
        "FAMILIES: family id -> installer class",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 3 (TrinityCoreInstaller)"),
        },
    ),
    Site(
        "yulon.catalog.families.azerothcore",
        "confs_from_dist",
        "module confs the AzerothCore install copies from their .dist (T137)",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("CMaNGOS writes every conf from its own conf table"),
            "trinitycore": not_applicable(
                "TrinityCore writes its confs, playerbots.conf included, from its conf table"
            ),
        },
    ),
    Site(
        "yulon.catalog.families.cmangos",
        "CmangosInstaller._data",
        "the CMaNGOS engine reads its own block",
        _cmangos_only(_NOT_THE_ENGINE),
    ),
    # -- the install spine ---------------------------------------------------------------
    Site(
        "yulon.catalog.native",
        "no_rollback_confirmation",
        "the rebuild confirmation names Reset to default where it reads the image",
        {
            "azerothcore": supported("WotLK's defaults are not in its image"),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Reset to default from the image's .dist)"),
        },
    ),
    Site(
        "yulon.catalog.native",
        "update_phases",
        "the SQL phases an update of the server to its latest code applies",
        _cmangos_only(_NO_SQL_PLAN, pending("Task 6 (world tables re-import route)")),
    ),
    Site(
        "yulon.catalog.native",
        "correction_phases",
        "the SQL phases Apply database corrections offers (T129)",
        _cmangos_only(_NO_SQL_PLAN, pending("Task 6 (world tables re-import route)")),
    ),
    Site(
        "yulon.catalog.native",
        "StagedInstaller._conf_edits",
        "folder settings a compose repair sets in the confs (T169)",
        _cmangos_only(_NO_CONF_TABLE, pending("Task 3 (conf stage: DataDir, LogsDir)")),
    ),
    Site(
        "yulon.catalog.native",
        "held_at_its_pin",
        "which sources stay on their pin when the server is updated (*-db repos)",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 6 (the SQL lives in the core repo and moves with it)"),
        },
        scanned=False,
    ),
    Site(
        "yulon.catalog.native",
        "StagedInstaller._checkout_is_the_server_dir",
        "whether a source is cloned into the server folder itself (dest '.')",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 3 (the checkout's dest)"),
        },
        scanned=False,
    ),
    # -- compose -------------------------------------------------------------------------
    Site(
        "yulon.catalog.composegen",
        "world_env",
        "the world server's environment block in the compose override",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("CMaNGOS sets its server through its conf table"),
            "trinitycore": pending("Task 2 (TrinityCore compose templates)"),
        },
    ),
    Site(
        "yulon.catalog.composegen",
        "entry_tokens",
        "MAKE_JOBS and CORE_DIR template tokens",
        {
            "azerothcore": supported("its checkout ships its own Dockerfile"),
            "cmangos": supported(),
            "trinitycore": pending("Task 2 (TrinityCore Dockerfile and compose tokens)"),
        },
    ),
    Site(
        "yulon.catalog.composegen",
        "folder_target",
        "where a conf's *Dir value lands in the container",
        _cmangos_only(_NO_CONF_TABLE, pending("Task 2 (DataDir/LogsDir binds)")),
    ),
    Site(
        "yulon.catalog.composegen",
        "folder_settings",
        "the folder settings a conf table states, and their binds (T169)",
        _cmangos_only(_NO_CONF_TABLE, pending("Task 2 (DataDir/LogsDir binds)")),
    ),
    Site(
        "yulon.catalog.composegen",
        "conf_texts",
        "the conf texts render() reads for folder settings",
        _cmangos_only(_NO_CONF_TABLE, pending("Task 2 (DataDir/LogsDir binds)")),
    ),
    # -- preflight -----------------------------------------------------------------------
    Site(
        "yulon.catalog.preflight",
        "client_spec_for",
        "the client-folder checks before an install reads the player's client",
        _cmangos_only(
            "AzerothCore downloads its client data; it reads no client folder",
            pending("Task 3 (the temporary extraction client)"),
        ),
    ),
    Site(
        "yulon.catalog.preflight",
        "_build_jobs",
        "how many compilers the build runs",
        {
            "azerothcore": supported("its Dockerfile runs nproc+1"),
            "cmangos": supported(),
            "trinitycore": pending("Task 2 (TrinityCore Dockerfile make_jobs)"),
        },
    ),
    # -- tabs and their seams -------------------------------------------------------------
    Site(
        "yulon.catalog.time_zone",
        "services",
        "the containers the server time zone is set on",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Tuning: time zone)"),
        },
    ),
    Site(
        "yulon.catalog.time_zone",
        "needs_files",
        "whether the server folder must bring zone files the image lacks",
        {
            "azerothcore": supported("its image has zone files"),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Tuning: time zone)"),
        },
    ),
    Site(
        "yulon.catalog.bot_count",
        "in_override_text",
        "the random-bot count carried over from the compose override (T117)",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable(_COUNT_IN_CONF),
            "trinitycore": not_applicable(_COUNT_IN_CONF + " (playerbots.conf)"),
        },
    ),
    Site(
        "yulon.catalog.bot_dashboard",
        "conf_file",
        "the conf carrying the Tortoise bot dashboard switch (T127)",
        {
            "azerothcore": not_available(_DASHBOARD_REASON),
            "cmangos": supported("where the entry's table names the switch: Tortoise"),
            "trinitycore": not_available(_DASHBOARD_REASON),
        },
    ),
    Site(
        "yulon.bot_population",
        "where",
        "the file the Bots tab writes the random-bot count into",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Bots: count in playerbots.conf)"),
        },
    ),
    Site(
        "yulon.bot_population",
        "_table",
        "the conf table holding the bot-count keys",
        _cmangos_only(
            "AzerothCore's count is in the override's environment",
            pending("Task 5 (Bots: count in playerbots.conf)"),
        ),
    ),
    Site(
        "yulon.channel_setup",
        "_world_env",
        "the world environment the command channel is enabled through",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("CMaNGOS enables SOAP in its conf (operations.enable_conf)"),
            "trinitycore": not_applicable(
                "TrinityCore enables SOAP in its world server conf (operations.enable_conf)"
            ),
        },
    ),
    Site(
        "yulon.install_wiring",
        "repair_compose_for_app",
        "Repair server files: the compose half (T106)",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Server tab: Repair server files)"),
        },
    ),
    Site(
        "yulon.install_wiring",
        "import_gate_for",
        "the one-shot import probe/reset pair (keyed on import_service)",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("CMaNGOS's import is the engine's marker-gated SQL plan"),
            "trinitycore": pending("Task 3 (marker-gated import)"),
        },
        scanned=False,
    ),
    Site(
        "yulon.install_wiring",
        "repair_confs_for_app",
        "Repair server files: missing module confs (T137, via confs_from_dist)",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("CMaNGOS writes every conf from its own conf table"),
            "trinitycore": pending("Task 5 (Server tab: Repair server files)"),
        },
        scanned=False,
    ),
    Site(
        "yulon.install_wiring",
        "corrections_for_app",
        "Apply database corrections (T129, via correction_phases)",
        _cmangos_only(_NO_SQL_PLAN, pending("Task 6 (world tables re-import route)")),
        scanned=False,
    ),
    Site(
        "yulon.reset_defaults",
        "core_files",
        "the files Reset to default offers",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Tuning: Reset to default)"),
        },
    ),
    Site(
        "yulon.reset_defaults",
        "default_texts",
        "where each file's default text comes from",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Tuning: Reset to default)"),
        },
    ),
    Site(
        "yulon.reset_defaults",
        "_from_image",
        "defaults read from the server image",
        _cmangos_only(
            "WotLK's defaults are not in its image",
            pending("Task 5 (Reset to default from the image's .dist)"),
        ),
    ),
    Site(
        "yulon.reset_defaults",
        "install_keys",
        "the keys the install table writes, which win over a carry-over",
        _cmangos_only(_NO_CONF_TABLE, pending("Task 5 (Tuning: Reset to default)")),
    ),
    Site(
        "yulon.reset_defaults",
        "install_writes",
        "whether a fresh install writes a file, so a missing one is made again",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Tuning: Reset to default)"),
        },
    ),
    Site(
        "yulon.party",
        "InstallParty.for_entry_is_possible",
        "whether My Party is offered (keyed on the entry id)",
        {
            "azerothcore": supported(),
            "cmangos": not_available(_PARTY_REASON),
            "trinitycore": not_available(_PARTY_REASON),
        },
        scanned=False,
    ),
    # -- account schemes (Accounts.scheme; the AzerothCore arm names a family) -------
    Site(
        "yulon.controller_wow_wotlk.accounts",
        "reset_own_password",
        "re-password the app's own account, per scheme",
        {
            "azerothcore": supported(),
            "cmangos": supported("mangos_srp6 / mangos_sha"),
            "trinitycore": pending("Task 5 (scheme trinitycore; refuses by name until then)"),
        },
    ),
    Site(
        "yulon.controller_wow_wotlk.accounts",
        "_insert_statement",
        "the account INSERT, per scheme",
        {
            "azerothcore": supported(),
            "cmangos": supported("mangos_srp6 / mangos_sha"),
            "trinitycore": pending("Task 5 (scheme trinitycore; refuses by name until then)"),
        },
    ),
    Site(
        "yulon.controller_wow_wotlk.accounts",
        "_grant_gm",
        "the GM level write, per scheme",
        {
            "azerothcore": supported(),
            "cmangos": supported("mangos_srp6 / mangos_sha"),
            "trinitycore": pending("Task 5 (scheme trinitycore; refuses by name until then)"),
        },
    ),
    Site(
        "yulon.controller_wow_wotlk.accounts",
        "_gm_level",
        "the GM level read, per scheme",
        {
            "azerothcore": supported(),
            "cmangos": supported("mangos_srp6 / mangos_sha"),
            "trinitycore": pending("Task 5 (scheme trinitycore; refuses by name until then)"),
        },
    ),
    # -- per-game controller packages that read their CMaNGOS block ---------------------
    Site(
        "yulon.controller_wow_tbc.docker_ctl",
        "<module>",
        "the TBC controller binds its CMaNGOS block",
        _cmangos_only(_CMANGOS_CONTROLLER),
    ),
    Site(
        "yulon.controller_wow_vanilla.repair",
        "sql_plan",
        "the Vanilla controller's SQL plan",
        _cmangos_only(_CMANGOS_CONTROLLER),
    ),
    Site(
        "yulon.controller_wow_tortoise.game",
        "cmangos",
        "the Tortoise controller's CMaNGOS block",
        _cmangos_only(_CMANGOS_CONTROLLER),
    ),
    Site(
        "yulon.controller_wow_tortoise.poolreset",
        "_with_value",
        "the Tortoise pool reset's bot conf table",
        _cmangos_only(_CMANGOS_CONTROLLER),
    ),
    # -- the UI's per-game tables (keyed on the entry id) ---------------------------------
    Site(
        "yulon.ui.controller_view",
        "_FACTORIES",
        "which controller package manages an installed game",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (_for_centurion)"),
        },
        scanned=False,
    ),
    Site(
        "yulon.ui.catalog_view",
        "_CAMPAIGN_GLYPHS",
        "the catalog tile's glyph and subtitle per game (with a fallback)",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (catalog glyph and subtitle)"),
        },
        scanned=False,
    ),
)
