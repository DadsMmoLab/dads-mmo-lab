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
  will serve it, in the plan task named. Allowed only while no entry of the
  shipped catalog has `family: trinitycore` (`pending_problems()`), so a
  Centurion entry cannot ship while any site still owes it a branch.

`tests/test_family_decisions.py` holds it: every site decides every family, and an
AST scan of `yulon/` finds every family branch -- any string constant equal to a
family's name outside a `Literal[...]` type, a family block's attribute, a keyword
named after a family, an isinstance or `case` on a family's class, a load of a
module constant bound to a family's name, a method called on `.family` -- and
fails for one that is not a site here. Each scanned site pins the kinds of the
hits it was decided on (`hits`), so a branch added inside a registered scope is
red until somebody decides it. A few sites branch on an id or a data field the
scan cannot see; they are listed with `scanned=False`, and the test keeps their
scopes from rotting.

A site is a scope -- `module` and the qualified name of the def or class holding
the branch (`<module>` for a top-level one) -- not a line, so it survives edits.
Data only: no import of the code it describes.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["supported", "not-available", "not-applicable", "pending"]

PENDING_FAMILY = "trinitycore"
"""The one family `pending` is allowed for: T179's, while it is being built."""


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
    hits: tuple[str, ...] = ()
    """The sorted kinds of the scan's hits in this scope when it was decided."""
    scanned: bool = True


def pending_problems(sites: Iterable[Site], shipped_families: Collection[str]) -> list[str]:
    """Every `pending` decision the rules refuse, as a sentence; empty when there is none.

    `pending` is `PENDING_FAMILY`'s alone, and only while `shipped_families` --
    the families of the entries in the shipped catalog -- does not include it.
    """
    problems: list[str] = []
    for site in sites:
        for family, decision in site.decisions.items():
            if decision.kind != "pending":
                continue
            where = f"{site.module}:{site.scope}"
            if family != PENDING_FAMILY:
                problems.append(
                    f"{where} is pending for {family}; pending is {PENDING_FAMILY}'s alone"
                )
            elif family in shipped_families:
                problems.append(
                    f"{where} is still pending for {family} ({decision.note}), "
                    f"and the shipped catalog installs {family}"
                )
    return problems


_NOT_THE_ENGINE = "never reached: this is inside the CMaNGOS engine, which only its own entries run"
_NOT_THE_TC_ENGINE = (
    "never reached: this is inside the TrinityCore engine, which only its own entries run"
)
_CMANGOS_CONTROLLER = "never reached: only this CMaNGOS game's own controller package reads it"
_NO_SQL_PLAN = "AzerothCore's own database updater applies its updates; its block has no SQL plan"
_NO_CONF_TABLE = "AzerothCore's confs are made by its image from their .dist; it has no conf table"
_COUNT_IN_CONF = "this family's random-bot count is in a conf file, not the override's environment"
_WOTLK_DEFAULTS = "WotLK's Reset to default does not read its image: its defaults are not there"
_COSMETIC = "cosmetic: a game without its own entry here gets the generic fallback"
_PARTY_REASON = "My Party needs AzerothCore's Lua bridge; it is WotLK-only."
_MMAPS_IN_THE_INSTALL = (
    "no background job: this family's movement maps are made before the server starts "
    "(CMaNGOS's `mmaps` stage) or come in the client-data download (AzerothCore)"
)
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
        hits=(
            "'azerothcore'",
            "'cmangos'",
            "'cmangos'",
            "'trinitycore'",
            "'trinitycore'",
            ".azerothcore",
            ".cmangos",
            ".cmangos",
            ".cmangos",
            ".trinitycore",
            ".trinitycore",
            ".trinitycore",
        ),
    ),
    Site(
        "yulon.catalog.catalog",
        "CatalogEntry._every_patch_names_a_source_this_entry_clones",
        "a CMaNGOS source patch names a cloned dest",
        _cmangos_only("this family's block carries no source patches"),
        hits=(".cmangos",),
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
        hits=("'trinitycore'", ".trinitycore"),
    ),
    # -- engine dispatch ---------------------------------------------------------------
    Site(
        "yulon.catalog.families.__init__",
        "<module>",
        "FAMILIES: family id -> installer class",
        _all(supported()),
        hits=("'azerothcore'", "'cmangos'", "'trinitycore'"),
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
        hits=(".azerothcore", ".azerothcore"),
    ),
    Site(
        "yulon.catalog.families.cmangos",
        "CmangosInstaller._data",
        "the CMaNGOS engine reads its own block",
        _cmangos_only(
            _NOT_THE_ENGINE,
            not_applicable(
                "never reached: TrinityCoreInstaller overrides `_data()` with a view of its own "
                "block (T179 Task 3)"
            ),
        ),
        hits=(".cmangos",),
    ),
    Site(
        "yulon.catalog.families.trinitycore",
        "TrinityCoreInstaller",
        "the TrinityCore engine's own `family` name",
        {
            "azerothcore": not_applicable("the name the TrinityCore engine answers to"),
            "cmangos": not_applicable("the name the TrinityCore engine answers to"),
            "trinitycore": supported(),
        },
        hits=("'trinitycore'",),
    ),
    Site(
        "yulon.catalog.families.trinitycore",
        "TrinityCoreInstaller._tc",
        "the TrinityCore engine reads its own block",
        {
            "azerothcore": not_applicable(_NOT_THE_TC_ENGINE),
            "cmangos": not_applicable(_NOT_THE_TC_ENGINE),
            "trinitycore": supported(),
        },
        hits=(".trinitycore",),
    ),
    # -- the install spine ---------------------------------------------------------------
    Site(
        "yulon.catalog.native",
        "no_rollback_confirmation",
        "the rebuild confirmation names Reset to default where it reads the image",
        {
            "azerothcore": not_applicable(_WOTLK_DEFAULTS),
            "cmangos": supported(),
            "trinitycore": pending("Task 5 (Reset to default from the image's .dist)"),
        },
        hits=("'cmangos'",),
    ),
    Site(
        "yulon.catalog.native",
        "update_phases",
        "the SQL phases an update of the server to its latest code applies",
        _cmangos_only(_NO_SQL_PLAN, pending("Task 6 (world tables re-import route)")),
        hits=(".cmangos",),
    ),
    Site(
        "yulon.catalog.native",
        "correction_phases",
        "the SQL phases Apply database corrections offers (T129)",
        _cmangos_only(_NO_SQL_PLAN, pending("Task 6 (world tables re-import route)")),
        hits=(".cmangos",),
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
            "trinitycore": supported(
                "its checkout is a folder inside the server dir; the catalog refuses `.`"
            ),
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
            "trinitycore": not_applicable(
                "TrinityCore sets its servers through its conf table; its override template "
                "carries no environment block"
            ),
        },
        hits=(".azerothcore", ".azerothcore"),
    ),
    Site(
        "yulon.catalog.composegen",
        "built_here",
        "the family block whose Dockerfile, confs and folder binds this app writes: MAKE_JOBS, "
        "CORE_DIR, the conf table folder_target/folder_settings/conf_texts read (T169), "
        "preflight's job count and client-folder rules, and the conf table a compose repair "
        "edits (T179 Task 3)",
        {
            "azerothcore": not_applicable(
                "its checkout ships its own Dockerfile and its image makes its confs"
            ),
            "cmangos": supported(),
            "trinitycore": supported("T179 Tasks 2 and 3"),
        },
        hits=(".cmangos", ".cmangos", ".trinitycore"),
    ),
    Site(
        "yulon.catalog.composegen",
        "entry_tokens",
        "the TrinityCore Dockerfile's CHECKOUT and CMAKE_OPTIONS tokens",
        {
            "azerothcore": not_applicable("its checkout ships its own Dockerfile"),
            "cmangos": not_applicable("its Dockerfile templates spell the cmake defines"),
            "trinitycore": supported("T179 Task 2"),
        },
        hits=(".trinitycore", ".trinitycore", ".trinitycore"),
    ),
    Site(
        "yulon.catalog.composegen",
        "folder_confs",
        "the base template's per-service folder tokens and the conf each service reads",
        {
            "azerothcore": not_applicable(
                "it has no conf table, so no folder is bound whatever the map"
            ),
            "cmangos": supported("REALMD_FOLDERS / MANGOSD_FOLDERS"),
            "trinitycore": supported("AUTHSERVER_FOLDERS / WORLDSERVER_FOLDERS (T179 Task 2)"),
        },
        hits=(".trinitycore",),
    ),
    # -- the engines' own names -------------------------------------------------------
    Site(
        "yulon.catalog.families.azerothcore",
        "AzerothCoreInstaller",
        "the AzerothCore engine's own `family` name",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("the name the AzerothCore engine answers to"),
            "trinitycore": not_applicable("the name the AzerothCore engine answers to"),
        },
        hits=("'azerothcore'",),
    ),
    Site(
        "yulon.catalog.families.cmangos",
        "CmangosInstaller",
        "the CMaNGOS engine's own `family` name",
        _cmangos_only("the name the CMaNGOS engine answers to"),
        hits=("'cmangos'",),
    ),
    Site(
        "yulon.catalog.catalog",
        "Accounts",
        "`accounts.scheme` defaults to AzerothCore's",
        {
            "azerothcore": supported("the default is its scheme"),
            "cmangos": supported("its entries declare mangos_srp6 / mangos_sha"),
            "trinitycore": supported(
                "CatalogEntry refuses any scheme but trinitycore or null on its entries"
            ),
        },
        hits=("'azerothcore'",),
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
        hits=("'azerothcore'", "'cmangos'"),
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
        hits=("'cmangos'",),
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
        hits=(".azerothcore",),
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
        hits=(".cmangos",),
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
        hits=("'azerothcore'", "'cmangos'", ".cmangos", ".cmangos"),
    ),
    Site(
        "yulon.bot_population",
        "_table",
        "the conf table holding the bot-count keys",
        _cmangos_only(
            "AzerothCore's count is in the override's environment",
            pending("Task 5 (Bots: count in playerbots.conf)"),
        ),
        hits=(".cmangos", ".cmangos"),
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
        hits=(".azerothcore", ".azerothcore"),
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
        hits=("'azerothcore'", "'cmangos'"),
    ),
    Site(
        "yulon.install_wiring",
        "import_gate_for",
        "the one-shot import probe/reset pair (keyed on import_service)",
        {
            "azerothcore": supported(),
            "cmangos": not_applicable("CMaNGOS's import is the engine's marker-gated SQL plan"),
            "trinitycore": not_applicable(
                "TrinityCore's import is the engine's marker-gated SQL plan (CmangosInstaller's, "
                "inherited)"
            ),
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
        hits=("'azerothcore'", "'cmangos'", ".cmangos", ".cmangos"),
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
        hits=("'azerothcore'", "'cmangos'"),
    ),
    Site(
        "yulon.reset_defaults",
        "_from_image",
        "defaults read from the server image",
        _cmangos_only(_WOTLK_DEFAULTS, pending("Task 5 (Reset to default from the image's .dist)")),
        hits=("isinstance CmangosInstaller",),
    ),
    Site(
        "yulon.reset_defaults",
        "install_keys",
        "the keys the install table writes, which win over a carry-over",
        _cmangos_only(_NO_CONF_TABLE, pending("Task 5 (Tuning: Reset to default)")),
        hits=(".cmangos", ".cmangos"),
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
        hits=("'cmangos'",),
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
        hits=("'azerothcore'", "'azerothcore'", "'trinitycore'"),
    ),
    Site(
        "yulon.controller_wow_wotlk.accounts",
        "create_account",
        "create_account's default scheme, for callers that pass none",
        {
            "azerothcore": supported("the default is its scheme"),
            "cmangos": supported("its callers pass the entry's scheme"),
            "trinitycore": pending("Task 5 (the Centurion controller passes its scheme)"),
        },
        hits=("'azerothcore'",),
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
        hits=("'azerothcore'", "'trinitycore'"),
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
        hits=("'azerothcore'", "'trinitycore'"),
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
        hits=("'azerothcore'", "'trinitycore'"),
    ),
    # -- per-game controller packages that read their CMaNGOS block ---------------------
    Site(
        "yulon.controller_wow_tbc.docker_ctl",
        "<module>",
        "the TBC controller binds its CMaNGOS block",
        _cmangos_only(_CMANGOS_CONTROLLER),
        hits=(".cmangos",),
    ),
    Site(
        "yulon.controller_wow_tortoise.game",
        "<module>",
        "the Tortoise controller's FAMILY constant",
        _cmangos_only(_CMANGOS_CONTROLLER),
        hits=("'cmangos'",),
    ),
    Site(
        "yulon.controller_wow_vanilla.repair",
        "sql_plan",
        "the Vanilla controller's SQL plan",
        _cmangos_only(_CMANGOS_CONTROLLER),
        hits=(".cmangos",),
    ),
    Site(
        "yulon.controller_wow_tortoise.game",
        "cmangos",
        "the Tortoise controller's CMaNGOS block",
        _cmangos_only(_CMANGOS_CONTROLLER),
        hits=(".cmangos", "FAMILY", "FAMILY", "FAMILY", "FAMILY"),
    ),
    Site(
        "yulon.controller_wow_tortoise.poolreset",
        "_with_value",
        "the Tortoise pool reset's bot conf table",
        _cmangos_only(_CMANGOS_CONTROLLER),
        hits=(".cmangos",),
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
        "the catalog tile's glyph per game id (with a fallback)",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": not_applicable(_COSMETIC),
        },
        scanned=False,
    ),
    Site(
        "yulon.ui.catalog_view",
        "_CAMPAIGN_SUBTITLES",
        "the catalog tile's subtitle per game id (with a fallback)",
        {
            "azerothcore": supported(),
            "cmangos": supported(),
            "trinitycore": not_applicable(_COSMETIC),
        },
        scanned=False,
    ),
    Site(
        "yulon.ui.widgets.dadcraft_decorations",
        "DadcraftCampaignCard",
        "the campaign card's particles and art per game id (with a plain fallback)",
        {
            "azerothcore": supported(),
            "cmangos": supported("TBC and Vanilla; Tortoise draws the plain card"),
            "trinitycore": not_applicable(_COSMETIC),
        },
        scanned=False,
    ),
    Site(
        "yulon.controller",
        "Controller.wait_ready",
        "the base controller's ready check, which defaults to AzerothCore's log markers",
        {
            "azerothcore": supported(),
            "cmangos": supported("each CMaNGOS controller overrides it from native.ready"),
            "trinitycore": pending("Task 5 (the Centurion controller reads native.ready)"),
        },
        scanned=False,
    ),
    Site(
        "yulon.catalog.native",
        "StagedInstaller.adopt_gate",
        "adopting an install this app did not make (T19): the marker gate",
        {
            "azerothcore": not_applicable(
                "AzerothCore imports through a compose one-shot and writes no marker to adopt"
            ),
            "cmangos": supported("CmangosInstaller overrides it with its import gate"),
            "trinitycore": supported(
                "TrinityCoreInstaller inherits CmangosInstaller's, over its own SQL plan"
            ),
        },
        scanned=False,
    ),
    # -- movement maps in the background (T179 Task 4) ------------------------------------
    Site(
        "yulon.catalog.families.mmaps",
        "background_block",
        "which entries make their movement maps as a job after the server is up",
        {
            "azerothcore": not_applicable(_MMAPS_IN_THE_INSTALL),
            "cmangos": not_applicable(_MMAPS_IN_THE_INSTALL),
            "trinitycore": supported("`TrinityCoreMmaps.background` (Task 4)"),
        },
        hits=(".trinitycore",),
    ),
    Site(
        "yulon.catalog.native",
        "StagedInstaller.after_ready",
        "what a family starts once its server is up, after an install or a rebuild",
        {
            "azerothcore": not_applicable(_MMAPS_IN_THE_INSTALL),
            "cmangos": not_applicable(_MMAPS_IN_THE_INSTALL),
            "trinitycore": supported("TrinityCoreInstaller starts its movement-map job"),
        },
        scanned=False,
    ),
    Site(
        "yulon.catalog.native",
        "StagedInstaller.before_rebuild",
        "what a family stops before a rebuild, an update or a return to the tested commit",
        {
            "azerothcore": not_applicable(_MMAPS_IN_THE_INSTALL),
            "cmangos": not_applicable(_MMAPS_IN_THE_INSTALL),
            "trinitycore": supported("TrinityCoreInstaller stops a running movement-map job"),
        },
        scanned=False,
    ),
    Site(
        "yulon.purge",
        "Uninstaller._real_stop_background_jobs",
        "Uninstall removes a background job before the containers (found by its record)",
        {
            "azerothcore": not_applicable(_MMAPS_IN_THE_INSTALL),
            "cmangos": not_applicable(_MMAPS_IN_THE_INSTALL),
            "trinitycore": supported("the movement-map job's container, by `.yulon-mmaps.json`"),
        },
        scanned=False,
    ),
)
