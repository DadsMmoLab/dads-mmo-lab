# Phase 7 — decisions and why

> Companion to `pyplan/roadmap.md` §7. The roadmap says *what* Phase 7 must achieve; this page
> records *how* it was decided and what was rejected, so a reviewer can challenge the reasoning
> instead of only the commits. Written 2026-08-26, before any Phase 7 code, on branch
> `yulon-phase7`; revised the same day after three reviewers and a review of the reviews
> returned 46 findings (all edits to this page and its companions, none reopening a decision).
>
> Method, the same one `phase6-decisions.md` used: the four games' bash installers were mapped
> stage by stage (eight files, 19,451 lines, read in full), the native engine was inventoried for
> what is generic and what is AzerothCore-shaped, three architectures were designed
> independently from three angles, and three judges with different priorities (a maintainer, an
> operator who runs the live gates, and a skeptic hunting the fatal flaw) scored them. The owner
> answered five questions before any of that was turned into a design; those answers are
> recorded first because they frame everything below.
>
> Step ids in this page are the checklist's (`7.1` … `7.10`, with `7.4a/b/c` for the three TBC
> gates). There is one numbering scheme, and it is the checklist's.

---

## The decision

**One typed Python install engine for all four v1 servers on all three platforms, built as two
emulator-family engines on the shared spine that `catalog/native.py` already is; the six bash
installers and their two helpers are deleted, Linux included, one live gate at a time.**

- The **spine** (`StagedInstaller`) owns what is already game-free: the state file and its
  hint semantics, the directory/ownership guard, preflight and Docker provisioning, the
  refuse-not-delete clone safety, the compose marker rules, streaming, cancel copy, keep-awake.
- Two **families** compose the spine's stages into an ordered tuple: `AzerothCoreInstaller`
  (WotLK — today's stages, moved verbatim, names unchanged) and `CmangosInstaller` (TBC, Vanilla
  and Tortoise — one class, three catalog entries).
- Five **stage-kind modules** hold the behaviour the CMaNGOS lineage needs and AzerothCore does
  not: client-folder validation, Dockerfile rendering, client-data extraction, ini-conf patching,
  and an ordered SQL plan with a completion marker. Each is family-neutral and takes typed data.
- **Stage order is Python; stage parameters are data.** A new CMaNGOS-lineage game is a catalog
  entry plus templates and zero Python (Tortoise is the proof). A new emulator lineage is one
  class in `families/`.

`installer_for()` stays the one place that decides, now on `install.native.family` instead of
on `script_platforms`. The `InstallEngine` contract (`preflight()`, `run() -> Iterator[str]`) is
untouched, so the catalog view, log panel and job runner need no changes — the same property
that made 6.2 cheap for the UI.

---

## The owner's five answers (2026-08-25/26)

Asked one at a time, before any design existed. They are decisions, not preferences, and the
rest of this page does not re-litigate them.

| # | Question | Answer |
|---|---|---|
| 1 | What does "to Python" mean for Linux? | **Python everywhere, delete the bash.** One engine for four games × three platforms. The six `install-*.sh` (11,066 lines) and their two helpers go. |
| 2 | Which clients are on disk for live gates? | **All four** — 3.3.5a, 2.4.3, 1.12.1, Turtle 1.18.1 (build 7272). Every game can be live-gated; none ships as "built, never run". |
| 3 | Phase 7 is gated on WotLK passing on all three platforms; macOS has no hardware and Windows is blocked at 9p. | **Linux-first, all four games.** WotLK native on Linux (retire bash) → TBC/Vanilla/Tortoise on Linux → Windows → macOS when a Mac exists. |
| 4 | The scripts end with Steam Deck gaming mode, MY_SERVER.txt, a wow-manage.sh download and a README prompt. | **Gaming mode survives as one small separate script**; the rest is dropped. The installer ends at "the server is up". |
| 5 | Which architecture? (after the judge panel) | **B — family engines on a shared spine, with five grafts** from the losing designs. |

One assumption was stated and not contradicted: `wow-manage.sh` (8,263 lines) is not an
installer. Its job is the Modules tab (`apply.py` + manifests) and it is deleted with the scripts.

---

## What this overturns, by name

The plan docs recorded, in several places, that Linux keeps bash and that the native engine's
stage list stays fixed. Those were sound decisions with one worked example; with four they are
the wrong bet, and this phase overturns them consciously rather than by drift. Every line is
named so the next reader knows the change was on purpose.

| Where | What it said | Now |
|---|---|---|
| `phase6-decisions.md` ## The decision | "Linux staying on its proven bash script until the native path earns its own live gate" | The gate is 7.1, on the three Linux VMs the script was proven on; bash is deleted in 7.2. |
| `phase6-decisions.md` ### What the implementer should NOT build yet | "**No Linux flip.**" and "**No step DSL, no per-game step data.** The stage list is a fixed Python tuple that is exactly WotLK." | The flip happens after gate 7.1. The stage list stays a Python tuple — per *family*, not fixed to WotLK — and the parameters become typed catalog data. No DSL: argv, globs and SQL statements are typed fields on typed blocks, validated at `load_catalog()`. |
| `phase6-decisions.md` ### Scope gate — WotLK first, exclusively | "Phase 7 must not start until Phase 6's WotLK exit criteria (6.5) are fully met on Linux, macOS, and native Windows." | Overturned by answer 3. Phase 6's macOS gate is hardware-blocked and its Windows gate is blocked at ac-db-import on 9p; both stay open items and are reached by 7.7 and 7.8. |
| `phase6-decisions.md` ### Dispatch | "NativeInstaller accepts `ask` and never uses it: nothing on the native path may prompt" | The engine still asks nothing on its own behalf. Exactly two questions pass *through* it, both in provisioning before stage 1 and both via the forwarded `ask`: the docker-group consent and, on Linux, the sudo password. |
| `phase6-decisions.md` ## Fate of the Linux bash path | A three-step retirement: bugfix-only → `YULON_NATIVE_INSTALL=1` side-by-side gate in 6.5 → delete at Phase 7 entry. | The side-by-side flag was never built (the variable exists nowhere in `pylauncher/`). Replaced by gate 7.1: the native engine installs WotLK from a clean checkpoint on Ubuntu, Fedora 44 and Arch — the same three boxes the script passed on 2026-08-21/25. |
| `pyplan/README.md` §9 | "**Full native reimplementation of installers on Linux** — Linux keeps wrapping the existing bash scripts" | Struck through in place with the date; §3 and §5 updated for one engine on all platforms. |
| `roadmap.md` ## Out of scope (L668); §6.2 preamble "Linux takes zero new code paths" (L382); §7 preamble (L543) | Same three statements. | **Not edited.** The roadmap is only modified when explicitly tasked; the replacement text for §7 is Appendix A of this page, ready to apply on the owner's word. |
| `checklist.md` Phase 7 preamble and 7.1–7.4 | "[blocked] on Phase 6's exit criteria"; three `controller_wow_*` packages first. | Scope-change note added and the section rewritten as 7.1–7.10 (the original four lines kept as 7.9/7.10, not deleted); the install engine comes first, the controller packages after, because a controller needs a server to control. |

Two things are **not** overturned: the privilege-transparency rule (no docker-group join, no
`sudoers.d`, no socket `chmod` without explicit, informed consent — asserted on the emitted argv
by `test_provision.py::test_linux_never_joins_the_docker_group_without_consent`, which survives
this phase) and the compose identity rules (well-known container names, per-install project name
and image tags, three compose files, ports in exactly one file). Everything below is built inside
them.

---

## Why, and what was rejected

Three architectures were designed independently and judged. The scores are in Appendix B; the
reasoning that matters is here.

**A — data-driven stage registry — rejected as the shape, kept as five grafts.** One engine,
`catalog.json` carrying an ordered list of stage *kinds* per game with a data block each; zero
Python for three games; cheapest fifth game. Two judges ranked it second, one first. What sank
it: the data blocks are argv, mounts, globs and SQL statements — "an untyped DSL in the exact
place the owner said data, not Python conditionals" (skeptic). Pydantic checks shape, not
meaning: a wrong glob validates and imports nothing. Reviewers read Python better than 60-line
JSON stage lists, and this project's reviewers are the ones who have to. What A did better than
anyone, and what is grafted: per-*tool* extraction with per-tool evidence (a resume redoes only
the interrupted tool); a closed mount vocabulary so no host path is ever spelled in data; token
substitution that *refuses* an unknown token the way `composegen._fill()` refuses a leftover
`{{`; a static test over the bundled catalog that turns a JSON typo into red CI; and the
Tortoise-style read-only client mount as the primary model, not the fallback.

**D — a plugin package per game — rejected.** `catalog.json` naming a Python module path
resolved by `importlib`, per-game facts split across `catalog.json` and a second `install.json`,
and three near-empty `controller_wow_*` packages holding a one-line `build()`. Every judge put it
last: three homes for the same fact, dynamic import as a design primitive, TBC/Vanilla as
copy-paste packages, and the widest blast radius on the proven Server-tab code. Two ideas are
grafted: one shared `catalog/installers/shared/cmangos/` template set parameterised by tokens
instead of three near-identical per-game copies, and an extraction evidence file that records
what the maps were extracted *from*, so pointing a resume at a different client does not skip
extraction on a file count that still passes.

**Keep bash on Linux for the CMaNGOS family, port only for Windows/macOS — rejected.** It is
what the roadmap said, and it is two install paths per game for four games; the bash scripts do
not fit the GUI's contract even today (they hardcode `SERVER_DIR`, ignore the folder picker, and
TBC/Vanilla write `compose.yml` where the view looks for `docker-compose.yml`,
`catalog_view.py:217`/`:335` — checklist Phase 6, found 2026-08-25). Answer 1 settled it.

**Containerise the bash — rejected.** Ship each installer's extract/conf/SQL logic as in-image
entrypoint scripts and drive them as compose one-shots, so the engine's existing one-shot/probe
model applies unchanged. Attractive for cross-platform (no host tools at all), and it is how
AzerothCore's own `db-import` and `client-data` one-shots work. Rejected because it keeps the
bash's import mechanics — SQL enumerated inside the image by `ls -v` inside `sh -c`, with errors
discarded — exactly where this phase's value is in replacing them with an ordered plan the app can
see into. Shell that *upstream* ships inside its images (extractor drivers, Dockerfile `RUN`
lines) is fine; shell this project writes to orchestrate an install is what answer 1 retires.

**Family engines — chosen.** Stage order in typed Python a reader can jump into; parameters in
typed data blocks a reviewer can diff; the pure parts (`conf.patch`, `sqlplan.expand`,
`clientdir.validate`, `extract.evidence`) need no seam and are tested byte for byte. The honest
cost, named by the maintainer judge: a fifth *lineage* costs a class, and the family's data
schema lives in `catalog.py` — which is the schema module, so that is where a schema belongs.

---

## Architecture and module layout

Three layers, all under `pylauncher/yulon/catalog/` unless said otherwise.

| Layer | Module | Owns | Must never |
|---|---|---|---|
| Spine | `native.py` — `StagedInstaller`, `Stage`, `StageContext`, `Seams`, `InstallState` | State file + hint semantics; guard (empty-or-ours, foreign containers, foreign *family*); preflight lines + Docker provisioning; generic clone/compose/build/start-db/up/ready stage bodies; `_pump`, `_check_run`, `_held_awake`, `_record_error` | Name a game or a family; prompt for its own decisions |
| Families | `families/__init__.py` — `FAMILIES: Mapping[str, type[StagedInstaller]]` | The one place a family id becomes a class | Anything else |
| | `families/azerothcore.py` — `AzerothCoreInstaller` | WotLK's stage tuple: today's bodies moved verbatim; the compose one-shot client-data stage; the import stage bound to the injected `acore_*` probe | Import a `controller_*` package (the probe is injected by the caller, as today) |
| | `families/cmangos.py` — `CmangosInstaller` | The CMaNGOS-lineage stage tuple and thin wrappers that pull each typed data block off the entry and call a stage-kind module | Contain a game literal — a test asserts, case-sensitively and over code only (docstrings and comments stripped via `ast`), that none of `tbc`, `vanilla`, `classic`, `tortoise`, `8606`, `5875`, `7272`, `Avg Diff`, `tw_char` appears |
| Stage kinds | `families/clientdir.py` | Pure `validate(client_dir, spec) -> tuple[Check, ...]` | Prompt; read a build number it cannot verify |
| | `families/dockerfile.py` | Render `Dockerfile.tmpl` + `dockerignore.tmpl` with the generated-file marker; refuse an unmarked file | Run a build |
| | `families/extract.py` | One `docker run --rm` per tool: client `:ro` at `/client`, `data/` rw at `/out`, `-u uid:gid` on Linux; per-tool completion records + count evidence; the mmaps stage | Write into the client folder; `sudo chown` |
| | `families/conf.py` | Pure `patch(text, table, tokens) -> str` over ini `Key = value` lines; `materialise()` copies `.conf.dist` out of the image once via `docker create`/`cp`/`rm` | Re-copy over a file the user has edited |
| | `families/sqlplan.py` | `expand()` (globs, natural sort, gzip flag), `apply()` (ordered phases over `docker exec -i`, per-phase error policy), the completion marker, `MarkerGate` (probe + reset) | Hide an error; drop a schema that holds player data |
| Wiring | `yulon/install_wiring.py` | `installer_for_app(entry)`: the AC probe/reset wiring that `main.py:65-80` and `ui/controller_view.py:104-108` each hand-write today, including the fixed-password fallback both carry; **and the CLI harness `_main()`**, moved here from `catalog/installer.py` because it must wire the probe and `catalog/` may not import `controller_wow_wotlk` | Contain UI |

Supporting changes: `docker.py` gains `run_container(ContainerRun)`, `copy_from_image()`,
`exec_stdin()`, and **`wait_ready()` changes signature to take a `ReadySpec`** — its four direct
callers in `test_docker.py` (`:166`, `:204`, `:215`, `:481`) are updated; `wait_ready_for()` keeps
its signature as a wrapper so `controller.py`, `docker_ctl.py` and their tests are untouched.
`platform.py` gains the Linux `keep_awake()` body (`systemd-inhibit`), SELinux facts +
`relabel_for_containers()`, `container_user_args()` — **the one home for the uid:gid policy;
`composegen.container_user()` and `git.ContainerGit._user_args` are rewritten to call it** — and
the sudo-password transport inside `_run_steps()`. `catalog.py` gains the family blocks (below);
`composegen.py` takes its image prefix, built-service list and extra tokens from the entry
instead of module constants, and reads `password.value` where it read `db_root_password`.

Deleted in 7.2: the six `install-*.sh`, `dml-start.sh`, `wow-manage.sh` (eight files, 19,451
lines of bash); `installer.Installer`, `PromptRule`, `PROMPT_RULES`, `make_responder`,
`bash_available`, `host_package_manager`, `NO_BASH_HELP`; `Install.script`, `script_platforms`,
`script_variants`, `db_root_password`, `db_root_password_file`; the script-path tests including
`test_installer.py::test_no_installer_escalates_privileges_without_asking` (a grep over the
scripts — the argv-level assertion in `test_provision.py` is the one that matters and stays).
Kept: `InstallOptions` (minus `reinstall`), the three error types, the `InstallEngine` protocol,
`installer_for()`, and `runner.interact()`'s pty + `ask_marker` transport (now used by
provisioning, not by a rule table).

Templates: `catalog/installers/shared/cmangos/{base,override,build}.yml.tmpl` — one set,
tokenised by container prefix, image, schema names, mounts — plus a per-game `Dockerfile.tmpl`
and `dockerignore.tmpl` under `catalog/installers/<game>/native/` (named by
`install.native.dockerfile_dir`). WotLK's three templates change by the `{{BIND_LABEL}}` token on
every host bind line and nothing else.

One bash file survives: `catalog/installers/steam-deck/setup-gaming-mode.sh` (answer 4). It is
written once, from the WotLK variant, and does less than the six it replaces: it takes the server
dir, the game id and the ready regex (from catalog data) as arguments; it starts the stack with
`compose up -d`, waits for the regex, launches the client, and stops with `compose stop` (not
`down`) when the client exits; it never stops another game's containers — the Server tab owns
that. It creates the Steam shortcut instructions and nothing else.

---

## The stage model

A stage is data plus a bound callable, not a name in an if-chain:

```python
@dataclass(frozen=True)
class Stage:
    name: str                                     # what .yulon-install.json records
    run: Callable[[StageContext], Iterator[str]]  # a bound method on the family
    recorded: bool = True                         # False == today's NEVER_RECORDED
    cancel_note: str = ""                         # what a Stop costs HERE, and only here

@dataclass(frozen=True)
class StageContext:
    server_dir: Path
    client_dir: Path | None
    state: InstallState
    cancel: threading.Event | None
    secrets: Secrets                              # repr-safe holder; .db_password
```

`Secrets` is resolved by the spine **before** the context is built — it reads `.db_password` if
present, otherwise generates the value — so a frozen context can carry it; the `db-password`
stage's job is the refusal check and persisting the file, not producing the value.

`StagedInstaller.stages() -> tuple[Stage, ...]` is the one abstract method. `run()` keeps
today's shape: preflight lines → guard → keep-awake → `for stage in self.stages(): yield
f"--- {stage.name}"; state = yield from self._run_stage(stage, ctx)`. `_run_stage` calls
`stage.run(ctx)` and writes the state file only when `stage.recorded`. Preflight and guard are
not stages: they are the spine's own, so a family can neither forget them nor record them.

Rules carried over unchanged from 6.2, because each cost the Rust launcher an evening:

- **The state file is a hint.** Every recorded stage re-checks disk evidence before skipping;
  the evidence per stage is in the tables below.
- `install_id` is a hash of the absolute server dir; a copied folder is refused, not adopted.
- A failure mid-stage records nothing; `last_error` is written only into a state file that
  already exists. A preflight refusal therefore leaves **no** state file on a clean box.
- `start-db`, `up`, `ready` are never recorded: a resume must end by actually starting and
  verifying the server. The CMaNGOS family adds `db-password` to that list: its evidence is the
  file, and a state file must never be the thing that claims a secret exists.

Three rules are new:

- **Stage names are validated per entry.** `read_state(server_dir, valid=<the entry's stage
  names>)` drops unknown names, replacing the global `STAGE_ORDER` filter. `with_stage()` orders
  `completed` by the entry's tuple.
- **The state file records `family`.** A catalog edit that moves a game between families is a
  guard refusal ("this folder was installed as `cmangos`, the catalog now says `azerothcore`"),
  never a reinterpretation. `version` stays 1: the new key is additive and an old file without it
  is read as its `game_id`'s current family.
- **WotLK's stage names are pinned by a test** — `clone-core`, `clone-modules`,
  `generate-compose`, `build`, `client-data`, `start-db`, `import`, `up`, `ready` — because a state
  file written by the 6.3 Windows partial install (2026-08-25) exists and must still read. (Design
  B as drafted merged the two clone stages into `clone-sources`; two judges caught the false
  premise "no native install has ever finished" and the merge was dropped for AzerothCore. The
  CMaNGOS family, which has no state files in the wild, uses one `clone-sources` stage.)

---

## The two families, stage by stage

### `AzerothCoreInstaller` (wow-wotlk)

Today's `native.py` bodies, moved. Evidence rules and cancel notes unchanged.

| Stage | Recorded | Evidence before skipping | Cancel note |
|---|---|---|---|
| `clone-core` | yes | `.git` present and `origin` matches the source URL → update instead | — |
| `clone-modules` | yes | same, per module under `modules/` | — |
| `generate-compose` | yes | marker rule; upstream's tracked-and-unmodified `docker-compose.yml` is the one replaceable unmarked file | — |
| `build` | yes | `docker image inspect` on every declared image ref; None re-runs | `BUILD_CANCEL_NOTE` (today's) |
| `client-data` | yes | always re-run; the entrypoint's `data-version` file is the check | `DOWNLOAD_CANCEL_NOTE` (today's) |
| `start-db` | no | returns when the container is up | — |
| `import` | yes | the injected `acore_*` probe's five branches (unchanged) | `IMPORT_CANCEL_NOTE` (today's) |
| `up` | no | — | — |
| `ready` | no | `ReadySpec(auth="{{REALM_HOST}}:{{WORLD_PORT}}", world="ready...")` from catalog data through the same `fill()` as everything else — the same two strings, now read from the entry | — |

### `CmangosInstaller` (wow-tbc, wow-vanilla, wow-tortoise)

| Stage | Kind module | Recorded | Evidence before skipping | Cancel note |
|---|---|---|---|---|
| `clone-sources` | spine `_clone_checked` per source, to `source.dest` | yes | per source, `.git` + `origin` match; a nested checkout (`src/mangos-tbc/src/modules/Bots`) is verified by its own remote and left alone by the outer update | — |
| `db-password` | family | no | `.db_password` exists → the spine already loaded it; nothing to do. Absent **and** this project's `db-data` volume exists → **refuse** (the volume has a password this install no longer knows; the message names both). Absent and no volume → write the generated value, 0600 | — |
| `write-dockerfile` | `dockerfile` | yes | marker + text equal → skip; marked and different → rewrite; unmarked → refuse | — |
| `generate-compose` | spine | yes | marker rule, as AC; in `generated` password mode the plan's `dotenv` carries `DB_ROOT_PASSWORD`, and `write_plan()` merges it into `.env` through `composegen.write_dotenv()` — `.env` has exactly one writer | — |
| `build` | spine | yes | `images_built([<prefix>server:<tag>])` | as AC |
| `extract` | `extract` | yes | per **tool**: a completion record for that tool in `data/.yulon-extract.json` (tool name, argv hash, exit 0, finished time) **and** every `produces` entry satisfied (dir has ≥ N files) **and** the file's stage-level facts match (plan hash, client path, size + mtime of `required_file`) → that tool is skipped; the stage records only when all tools pass | "Finished tools are kept; only the tool that was interrupted runs again." |
| `mmaps` | `extract` | yes | same three-part rule for the one tool; else wipe `data/mmaps` and regenerate. `required: false` (Tortoise) turns a shortfall into a warning | "Map generation restarts from the beginning; the extracted maps it reads are kept." |
| `conf` | `conf` | yes | each patched key reads back equal → skip; a file that exists is patched in place, never re-copied; a missing file is copied out of the image once | — |
| `start-db` | spine | no | as AC | — |
| `import` | `sqlplan` | yes | `MarkerGate.probe()` five branches (below) | "Databases left half-written are detected and cleared before the import runs again." |
| `up` | spine | no | — | — |
| `ready` | spine | no | `ReadySpec` from data: world regex, optional auth regex, fatal regex, restart-loop threshold, timeout | — |

The completion record is what makes the cancel note true: a tool killed after it has already
written more files than its threshold (`ad` after 100 of ~700 dbc; `MoveMapGen` after 500 of
thousands) would otherwise pass the count gate on resume and be skipped with a partial set.

Tortoise fits this tuple without a switch: its compile moves *inside* its Dockerfile (the
script compiled into a host `install/` tree with a `docker run`; that was one more stage kind
for one game, and the image is where a compiled server belongs). Its extractors come out of the
image at `/opt/tortoise/bin/` exactly as TBC's come out at `/opt/mangos/bin/tools/`.

---

## Per-game data

### `catalog.py` additions

All `_Strict` (extra keys forbidden, frozen). Field names are final; the JSON below uses them.

- `EmulatorSource(Source)`: `dest: str` — where the clone lands, relative to the server dir
  (`"."` for AzerothCore's core, `"src/mangos-tbc/src/modules/Bots"` for CMaNGOS playerbots).
  Replaces the index coupling of "sources[0] is the core, the rest go under modules/".
- `Source.rev: str | None` — an optional commit pin, honoured by `CloneSpec`. Lands in 7.3 so
  it exists before the Vanilla gate; Tortoise is pinned the day 7.6 passes.
- `Install.password: PasswordPlan` — `{"mode": "fixed", "value": "password"}` or
  `{"mode": "generated", "file": ".db_password", "prefix": "tbc"}`. Replaces
  `db_root_password` / `db_root_password_file`.
- `Install.platforms` loses its `min_length=1`: an empty list is the honest state of an entry
  between 7.2 and its own gate (see Dispatch, below).
- `NativeInstall.family: Literal["azerothcore", "cmangos"]`; `images: tuple[str, ...]`
  (the built service keys); `image_prefix: str`; `dockerfile_dir: str | None` (the AC family
  leaves it `None`); `db: DbFacts` (`image`, `client` (`mysql`|`mariadb`), `user`, `charset`);
  `ready: ReadyMarkers` (`world`, `auth: str | None`, `fatal: str | None`, `timeout_s`,
  `restart_loop`); `templates`, `soap_port` and the floors stay where they are.
- `NativeInstall.azerothcore: AzerothCoreData | None` — `world_env` only. `Containers.db_import`
  and `Containers.client_data` stay exactly where they are: `container_spec()` reads
  `db_import` into `ContainerSpec.import_service`, which `main.py`, `controller_view.py` and
  `docker.repair_import()` branch on, and the Repair button is on the not-touched list.
- `NativeInstall.cmangos: CmangosData | None` — `client: ClientSpec`, `dockerfile:
  DockerfileSpec`, `extract: ExtractPlan`, `mmaps: MmapPlan`, `conf: ConfPatchTable`,
  `sql: SqlPlan`. A model validator requires exactly the block the `family` names.
- `Install.script`, `script_platforms`, `script_variants`, `db_root_password`,
  `db_root_password_file` deleted (7.2).

### Dispatch, and the interim between 7.2 and each game's gate

`installer_for()` returns the family engine for every entry. The platform refusal stays where it
is (`_preflight_lines()` → `unsupported_platform_message()`), and an entry with `native: None`
gets the existing "its catalog entry has no `install.native` section" refusal. In 7.2 the three
CMaNGOS entries set `platforms: []` — the tile's Install button is disabled with the 6.1 wording,
unchanged — and keep it empty through 7.3 (their `native`/`cmangos` blocks land, unused); 7.4c,
7.5 and 7.6 restore `["linux"]` as each passes. **A release cut in that window has no CMaNGOS
install path at all**; that is the honest state, and it is named in the blast-radius section.

### wow-tbc, abbreviated where a shape repeats

```json
{
  "id": "wow-tbc", "name": "WoW TBC", "status": "beta",
  "emulator": {
    "name": "CMaNGOS mangos-tbc + cmangos/playerbots + tbc-db",
    "sources": [
      {"repo": "cmangos/mangos-tbc", "dest": "src/mangos-tbc"},
      {"repo": "cmangos/playerbots", "dest": "src/mangos-tbc/src/modules/Bots"},
      {"repo": "cmangos/tbc-db", "dest": "src/tbc-db"}
    ]
  },
  "install": {
    "platforms": ["linux"],
    "default_server_dir": "wow-tbc-server",
    "requires_client_dir": true,
    "password": {"mode": "generated", "file": ".db_password", "prefix": "tbc"},
    "native": {
      "family": "cmangos",
      "templates": "shared/cmangos",
      "dockerfile_dir": "wow-tbc/native",
      "image_prefix": "yulon.local/cmangos-tbc-",
      "images": ["server"],
      "min_ram_gb": 6, "warn_ram_gb": 8,
      "min_data_root_gb": 20, "warn_data_root_gb": 30,
      "min_server_dir_gb": 20, "warn_server_dir_gb": 30,
      "db": {"image": "mariadb:11", "client": "mariadb", "user": "mangos", "charset": "utf8mb4"},
      "ready": {"world": "Avg Diff:", "auth": null, "fatal": null, "timeout_s": 600, "restart_loop": 4},
      "cmangos": {
        "client": {"required_file": "Data/expansion.MPQ", "min_mpq": 6, "mpq_depth": "recursive",
                   "locale_mpq_required": true, "near_client_warn_gb": 8},
        "dockerfile": {"make_jobs": 2},
        "extract": {
          "image": "server", "ulimit_stack_unlimited": false,
          "tools": [
            {"name": "dbc and maps", "argv": ["/opt/mangos/bin/tools/ad", "-i", "/client", "-o", "/out"],
             "produces": {"dbc": 100, "maps": 100}},
            {"name": "vmap extract", "argv": ["/opt/mangos/bin/tools/vmap_extractor", "-d", "/client/Data"],
             "produces": {"Buildings": 100}},
            {"name": "vmap assemble", "argv": ["/opt/mangos/bin/tools/vmap_assembler", "Buildings", "vmaps"],
             "produces": {"vmaps": 100}}
          ]
        },
        "mmaps": {"argv": ["/opt/mangos/bin/tools/MoveMapGen", "--silent", "--threads", "2"],
                  "min_files": 500, "required": true},
        "conf": {
          "source_dir": "/opt/mangos/etc",
          "files": {
            "mangosd.conf": {
              "LoginDatabaseInfo": "\"{{DB_HOST}};3306;{{DB_USER}};{{DB_PASSWORD}};{{AUTH_DB}}\"",
              "WorldDatabaseInfo": "\"{{DB_HOST}};3306;{{DB_USER}};{{DB_PASSWORD}};{{WORLD_DB}}\"",
              "CharacterDatabaseInfo": "\"{{DB_HOST}};3306;{{DB_USER}};{{DB_PASSWORD}};{{CHAR_DB}}\"",
              "LogsDatabaseInfo": "\"{{DB_HOST}};3306;{{DB_USER}};{{DB_PASSWORD}};{{LOGS_DB}}\"",
              "DataDir": "\"/opt/mangos/data\""},
            "realmd.conf": {"LoginDatabaseInfo": "\"{{DB_HOST}};3306;{{DB_USER}};{{DB_PASSWORD}};{{AUTH_DB}}\""},
            "aiplayerbot.conf": {"AiPlayerbot.MinRandomBots": "1600", "AiPlayerbot.MaxRandomBots": "2000",
                                 "AiPlayerbot.RandomBotAccountCount": "400"},
            "ahbot.conf": {"AuctionHouseBot.Chance.Sell": "75",
                           "AuctionHouseBot.Loot.Creature.Normal": "90,100,30,40",
                           "...": "the other nine tuples, verbatim from the script"}
          }
        },
        "sql": {
          "create": ["mangos", "realmd", "characters", "logs"],
          "phases": [
            {"name": "realmd base", "into": "realmd", "files": ["src/mangos-tbc/sql/base/realmd.sql"], "on_error": "fail"},
            {"name": "characters base", "into": "characters", "files": ["src/mangos-tbc/sql/base/characters.sql"], "on_error": "fail"},
            {"name": "logs base", "into": "logs", "files": ["src/mangos-tbc/sql/base/logs.sql"], "on_error": "fail"},
            {"name": "world content", "into": "mangos", "files": ["src/tbc-db/Full_DB/TBCDB_*.sql.gz"], "gzip": true, "on_error": "fail"},
            {"name": "content updates", "into": "mangos", "files": ["src/tbc-db/Updates/*.sql"], "sort": "natural", "on_error": "warn"},
            {"name": "ACID", "into": "mangos", "files": ["src/tbc-db/ACID/*.sql"], "on_error": "warn"},
            {"name": "dbc data", "into": "mangos", "files": ["src/mangos-tbc/sql/base/dbc/original_data/*.sql", "src/mangos-tbc/sql/base/dbc/cmangos_fixes/*.sql"], "on_error": "warn"},
            {"name": "core updates", "into_each": {"mangos": "src/mangos-tbc/sql/updates/mangos/*.sql", "realmd": "src/mangos-tbc/sql/updates/realmd/*.sql", "characters": "src/mangos-tbc/sql/updates/characters/*.sql", "logs": "src/mangos-tbc/sql/updates/logs/*.sql"}, "sort": "natural", "on_error": "warn"},
            {"name": "spell_template hotfix", "into": "mangos", "statements": ["ALTER TABLE spell_template ADD COLUMN IF NOT EXISTS EffectBonusCoefficient1 FLOAT NOT NULL DEFAULT '0', ..."], "on_error": "warn"},
            {"name": "playerbots characters", "into": "characters", "files": ["src/mangos-tbc/src/modules/Bots/sql/characters/*.sql"], "on_error": "warn"},
            {"name": "playerbots world", "into": "mangos", "files": ["src/mangos-tbc/src/modules/Bots/sql/world/*.sql", "src/mangos-tbc/src/modules/Bots/sql/world/tbc/*.sql"], "on_error": "warn"},
            {"name": "expansion unlock", "into": "realmd", "statements": ["ALTER TABLE account MODIFY COLUMN expansion TINYINT(3) UNSIGNED NOT NULL DEFAULT 1", "UPDATE account SET expansion = 1"], "on_error": "fail"}
          ],
          "verify": [
            {"db": "mangos", "query": "SELECT COUNT(*) FROM item_template", "min": 10000},
            {"db": "mangos", "query": "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='mangos' AND table_name LIKE 'ai_playerbot%'", "min": 10}
          ],
          "player_data": [
            {"db": "characters", "table": "characters"},
            {"db": "realmd", "table": "account", "exclude_usernames": ["ADMINISTRATOR", "GAMEMASTER", "MODERATOR", "PLAYER"]}
          ],
          "marker_db": "mangos"
        }
      }
    }
  },
  "containers": {"db": "tbc-db", "auth": "tbc-realmd", "world": "tbc-mangosd"},
  "ports": {"auth": 3724, "world": 8085, "db": 3306},
  "databases": {"auth": "realmd", "characters": "characters", "world": "mangos", "extra": ["logs"]},
  "client": {"version": "2.4.3", "build": 8606, "notes": ["..."]}
}
```

Every value above **except the three extract `argv` lines** is lifted from `install-wow-tbc.sh`
(stage map, 2026-08-25): the eleven AHBot tuples, the six `spell_template` columns, the bot
counts, the thresholds; the phase order is the script's phases 1–11 in the script's order. The
argv lines are the CMaNGOS tools' documented flags — no script in this repo calls them directly
(TBC runs `ExtractResources.sh a`) — and are confirmed at 7.4b against `--help` inside the built
image.

### wow-vanilla — the same block with these deltas

`required_file: "Data/dbc.MPQ"`, `min_mpq: 5`, `mpq_depth: 1`, `locale_mpq_required: false`;
`ulimit_stack_unlimited: true` and a `retry` record on the vmap tools (`when_log_matches:
"Segmentation fault|core dumped"`, re-run `vmap_extractor` + `vmap_assembler`); bot counts
600/800/200 plus the three `AiPlayerbot.SyncLevel*` keys with `match_commented: true`; ACID is
`src/classic-db/ACID/acid_classic.sql`; no `dbc data` phase; no `expansion unlock` phase;
`ClassicDB_*.sql.gz`; playerbots `world/classic`; verify `min: 17000`; build 5875.

`mpq_depth` is an integer `find -maxdepth` value or `"recursive"`: TBC's script searched with
no depth limit, Vanilla's with `-maxdepth 1`, Tortoise's with `-maxdepth 2`.

### wow-tortoise

One source `Penqle/tortoise-wow` → `src/tortoise-wow`; `db.image "mariadb:10.6"`; password prefix
`tortoise`; `client {required_file: null, min_mpq: 5, mpq_depth: 2}` (build 7272;
`required_file: null` disables that one rule — `Data/` and the MPQ count still apply); extract
tools `mapextractor -i /client -o /out -e 3`, `vmapextractor -d /client/Data/`, `vmap_assembler
/out/Buildings /out/vmaps` at `/opt/tortoise/bin/`; mmaps `MoveMapGen --silent
--doNotFilterDeepWater --offMeshInput /opt/tortoise/src/tools/mmap/offmesh.txt --settingsInput
…` with `required: false`; conf source `/opt/tortoise/etc` with `Database.AutoUpdate.Path`,
`DataDir`, `GameType`, `GM.LoginState`, `GM.StartLevel`, `AutoHonorRestart`,
`AutoRestart.MaxServerUptime`, `ForcePinAccountRank`, and `CharacterDatabase.Info` set to
`tw_char` (the shipped `.dist` says `tw_chars`; spelled as an ordinary patch, not a special case);
sql `create: []` (upstream's `create_databases.sql` creates them) with phases
`create_databases.sql` (fail, schema-less), `GRANT` statements, `src/tortoise-wow/sql/base/*.sql`
into `tw_world` (fail, `sort: name`), and `REPLACE INTO realmlist (...) VALUES (1, 'Tortoise WoW',
'{{REALM_HOST}}', {{WORLD_PORT}}, 0,0,0,0,0, '{{CLIENT_BUILD}}')`; ready world `World
initialized|MaNGOS.*started up successfully|Ready to login`, fatal `Correct \*.map files not
found|Could not open|Database .* not found`; `marker_db: "tw_world"`; ports 3724/8090, and the DB
port published **on `127.0.0.1` only, as WotLK's template does** — not the script's
all-interfaces `3306:3306`. The Dockerfile compiles the `COPY`'d source with the script's cmake
flags (`USE_EXTRACTORS=ON … USE_ANTICHEAT=OFF ALLOW_TURTLE_ADDONS=ON`) and installs `sql/` into
the image at `/opt/tortoise/sql` — replacing the script's host bind mount of `./src/sql` — for
`Database.AutoUpdate.Path`. **AutoUpdate is unverified:** the script set the key and never reached
`ready`, so nobody has seen mangosd apply an update; gate 7.6 asserts the update tables exist
after first boot, and if they do not, `sql/database_updates/*` becomes two more plan phases and
the reliance is dropped. Status stays `wip` until a 7272 client logs in.

### wow-wotlk

Gains `family: "azerothcore"`, `images: ["worldserver", "authserver", "db-import",
"client-data"]`, `image_prefix: "yulon.local/ac-wotlk-"`, `password: {mode: fixed, value:
"password"}`, `db: {image: "mysql:8.4", client: "mysql", user: "root"}`, `ready: {auth:
"{{REALM_HOST}}:{{WORLD_PORT}}", world: "ready..."}`, source `dest`s `"."` and
`"modules/mod-playerbots"`, and the `azerothcore` block (`world_env`). `containers.db_import`,
`containers.client_data` and `native.soap_port` do not move. Loses `script`, `script_variants`,
`script_platforms` — the last is the Linux flip, one JSON key.

### Templates

`catalog/installers/shared/cmangos/base.yml.tmpl` names services after their containers
(`tbc-db`, `tbc-realmd`, `tbc-mangosd`) — the AzerothCore convention — so `ContainerSpec.services`
keeps its default and `start_database`'s `compose up -d --no-deps <db>` works unchanged; the one
price is that the conf DB host is the container name, not the script's `db`, and that is the
`DB_HOST` token. Its database healthcheck is mariadb's own `healthcheck.sh --connect
--innodb_initialized` (shipped in the official 10.6 and 11 images; the Tortoise script used it on
10.6), proved on mariadb:11 by the 7.3 primitives gate.

Tokens available to every template: `PROJECT_NAME`, `DB_PORT`, `AUTH_PORT`, `WORLD_PORT`,
`IMAGE_PREFIX`, `IMAGE_TAG`, `BUILD_CONTEXT`, `CONTAINER_USER`, `BIND_LABEL`, `DB_IMAGE`,
`DB_HOST` (= `containers.db`), `DB_USER`, `AUTH_DB`, `WORLD_DB`, `CHAR_DB`, `LOGS_DB`,
`CONTAINER_PREFIX`, `CORE_DIR`, and `DB_PASSWORD` — which renders the `fixed` value and is
**refused** in `generated` mode, where templates must use `${DB_ROOT_PASSWORD:?Yu'lon .env is
missing}` (that is what the no-password-text invariant checks). Family extras: `SOAP_PORT` and
`ENVIRONMENT` (the AzerothCore override's env block), `CLIENT_BUILD`, `MAKE_JOBS`. Conf values,
SQL statements and `ready.*` strings take the same `{{TOKEN}}` grammar plus `REALM_HOST` — one
grammar, one `fill()`, which refuses an unknown token (a value between a template and a conf table
cannot become a silent literal). The three-file contract is asserted for every game: ports in
exactly one file, the build overlay never auto-loaded, no `{{` survives rendering, generated-
password templates contain no password text, every referenced template exists, services are named
after containers.

`BIND_LABEL` goes on **every `./…:` host bind line and never on a named volume**: eight lines in
WotLK's templates today (seven in `base.yml.tmpl`, one in `override.yml.tmpl`), and the invariants
test counts them so the number cannot drift. It is a uniform `:z`, not the Fedora script's `:Z` on
`./modules`: under the engine's compose that mount is shared by `ac-db-import` and
`ac-worldserver`, and `:Z` on a shared mount locks the other service out — the script's own
comment says so.

---

## The stage kinds in detail

### Client folder

`InstallOptions.client_dir` — collected by the catalog view for `requires_client_dir` entries
since Phase 3 and never read by the native engine — is required in preflight for any entry
whose family block has a `ClientSpec`. `clientdir.validate()` returns preflight `Check`s, not
"Continue anyway?" prompts: **refuse** when the folder, `Data/`, or `required_file` is missing;
**warn** when fewer than `min_mpq` MPQs are found at `mpq_depth`, when `locale_mpq_required` and
no MPQ sits at depth 2, on the repack heuristic (`realmlist.wtf` at the root and no locale dir),
and under `near_client_warn_gb` free on the client's volume. The build number is reported, not
checked: nothing in the scripts read it either, and the extraction count gate is the real check.
`preflight.gather()` bind-probes the client dir with `docker.bind_mount_ok()` as it probes the
server dir (no ancestor walk needed — the folder is populated), so a client outside Docker
Desktop's file-sharing list is refused before a two-hour build.

### Extraction

The client is mounted **read-only** at `/client`; output goes to `<server_dir>/data` at `/out`;
each tool runs in its own `docker run --rm` with cwd `/out`; on Linux the container runs
`-u uid:gid` (`platform.container_user_args()`), on Docker Desktop as the image's user. This is
the Tortoise script's model (`-i /client -o /out`, `:ro`, `-u`), adopted for CMaNGOS by calling
its tools directly (`ad`, `vmap_extractor`, `vmap_assembler`) instead of the `ExtractResources.sh
a` driver, which `cd`s into the client and writes there as root — after which the TBC script
`mv`'d the folders out and `sudo chown -R`'d them. Consequences: nothing is ever written into the
user's client; no `sudo chown`; an interrupted run leaves only our own partial folders under
`data/`; a client on a read-only, NTFS or exFAT mount works because it is only read.

**This is the first thing the TBC gate has to prove (7.4b).** Fallback, as data: `stage_client:
true` makes the stage lay a symlink farm (`cp -rs /client /work`) on a tmpfs and use `/work` as
cwd — still no writes into the client.

Evidence is per tool and per stage, in `data/.yulon-extract.json`: a completion record per tool
(name, argv hash, exit 0, finished time) and stage-level facts (plan hash, client path, size and
mtime of `required_file`), plus the `produces` counts on disk. A tool is skipped only when all
three agree. A resume after a kill re-runs the tool without a record; a resume pointed at a
different client re-extracts everything even if the counts pass. A tool that exits 0 but leaves a
shortfall is a refusal naming the counts and the expected client build — the script's "Continue
anyway? (server WILL fail to load maps)" becomes a refusal with the same sentence, because the
engine cannot ask.

### Dockerfile and build

Sources are cloned on the host (the spine's guarded clone, per `dest`) and the Dockerfile
`COPY`s them; the SQL trees the scripts baked into the image are therefore host files the import
can stream. One image per CMaNGOS game (`{{IMAGE_PREFIX}}server:{{IMAGE_TAG}}`) serves as
extractor, conf source and runtime, as it did in the scripts; `MAKE_JOBS` comes from data.
`dockerignore.tmpl` renders to `.dockerignore` and excludes `.git` (cmangos' revision string
reads "unknown"; accepted). Context transfer of ~1 GB per build is fine on Docker Engine and
unmeasured over 9p/VirtioFS — 7.4a records the number, and the fallback (clone inside the
Dockerfile) is a template change.

### Conf files

`conf.materialise()` copies `<source_dir>/*.conf.dist` out of the built image once via `docker
create` + `docker cp` + `docker rm` (no shell, host-owned files, works on Desktop), strips
`.dist`, and `conf.patch()` rewrites `^Key\s*=` lines — or `^#\s*Key\s*=` when `match_commented`
is set, which the Vanilla `SyncLevel*` seds relied on — appending `Key = value` when neither
exists. Re-runs patch in place and never re-copy an existing file, so a user's other edits
survive (the scripts overwrote every conf on every run). Files are written 0600 because the DB
password is in them. No SOAP/RA is enabled at install; that is 7.9 controller work.

### SQL plan, marker and probe

**Transport.** `sqlplan.apply()` streams each file from the host checkout on stdin of
`docker exec -i -e MYSQL_PWD <db-container> <client> -u root <schema>`, gzip decompressed by
Python on the way in. No helper container on the compose network, no `docker network ls | grep`,
no `sh -c 'ls -v … 2>/dev/null'`. The password travels in the exec environment, never in argv or
SQL text. `db.client` is the binary the DB image ships (`mariadb` for mariadb:10.6/11), which
settles MariaDB-vs-MySQL for this path.

**One secret.** The generated value is both the container's root password (via `.env`) and the
password of the app user phase 0 creates (`db.user`, `'mangos'@'%'`); the import streams as root,
the emulator connects as `db.user`, exactly as the scripts did with one `DB_PASSWORD`.

**Phases.** An implicit phase 0 creates the databases (`CREATE DATABASE IF NOT EXISTS …
CHARACTER SET <charset>`), the app user and the grants from `create` + `db` — skipped when
`create` is empty (Tortoise's `create_databases.sql` does it). Then each phase in data order:
expanded file list (relative glob, `sort: natural` reproducing `ls -v`, or `name`) or literal
statements, into `into` or `into_each`. `on_error: fail` stops the stage with the file name and
the client's last stderr line. `on_error: warn` logs every failing file by name and continues:
the scripts discarded those errors with `2>/dev/null`; this keeps them visible without changing
the outcome. The plan ships with `fail` everywhere except the phases the scripts hid, and 7.4c is
where each `warn` is either justified by a real, understood error or flipped to `fail`.

**Marker.** At the end of a plan whose `fail` phases all succeeded **and** whose `verify` rules
all pass, the stage writes `CREATE TABLE IF NOT EXISTS <marker_db>.yulon_install (plan_hash
CHAR(16), finished_unix BIGINT)` and a row. A plan whose verify fails writes no marker and the
stage fails naming the rule — design B as drafted demoted verify to a warning and wrote the marker
regardless, and two judges caught that an import whose warn phases all failed would read
`imported` forever.

**Probe** (`MarkerGate`, implements `docker.ImportProbe` and `ResetUnfinished`, the same
five-branch table the AzerothCore stage uses):

| Evidence | State | Action |
|---|---|---|
| none of the entry's schemas exist | `absent` | run the plan |
| a marker row exists (any hash) | `imported` | skip — a different hash is a finished import from an older plan, logged, never `partial` |
| rows in any `player_data` table — characters, or accounts beyond the seeded `exclude_usernames` | `populated` | refuse: somebody's server |
| schemas exist, no marker, no player rows | `partial` | `reset()` drops exactly the entry's schemas, then run |
| the client cannot answer (a volume from a different password) | `unreadable` | refuse, naming the volume and the Remove action |

Design B as drafted treated a plan-hash mismatch as `partial` and counted only characters as
player data; the operator judge showed that an app upgrade touching the SQL plan would then
`DROP realmd` on an install with accounts but no characters yet. Both are fixed above, and the
script's silent stale-volume wipe is a refusal.

Tortoise's remaining updates are applied by mangosd's `Database.AutoUpdate.Path` at first boot,
as in the script and as unverified as in the script (see its entry above); its verify is
deliberately weak (`tw_world` ≥ 150 tables, a realmlist row) and the `ready` stage's fatal regex
is the second line of defence.

### Password

`fixed` (WotLK keeps `"password"` — a contract with backup, console and every guide) is spliced
into the base file as the `${DB_ROOT_PASSWORD:-…}` default exactly as today, through the
`DB_PASSWORD` token. `generated` (TBC/Vanilla/Tortoise) is resolved by the spine before stage 1,
persisted at `.db_password` (0600) by `db-password`, and reaches `.env` through
`generate-compose`'s plan `dotenv` — `composegen.write_plan()` already calls `write_dotenv()`
whenever a plan carries one, and `render()` has never produced one until now (the function
landed 2026-08-24 and has waited for this caller since). The templates spell
`${DB_ROOT_PASSWORD:?…}`, so the generated secret is never written into a compose file.
Tortoise's time-derived, unpersisted password is gone. Plaintext in the CMaNGOS confs is
unavoidable (the emulator reads files) and those are 0600.

### Ready

`docker.wait_ready(ReadySpec)` keeps the measured mechanics — `StartedAt` + `logs --since`,
never `--tail` — and takes its strings from data: world regex, optional auth regex, fatal regex,
the restart-loop threshold (`RestartCount` grew by ≥ N), and the timeout. Logs are read into
Python and matched there, never through a shell pipeline: the Tortoise script (line 713) records
that `pipefail` + `grep -q` SIGPIPEs `docker logs` into false negatives. CMaNGOS realmd's ready
line is unknown to this repo (`auth: null` is honest and weak); 7.4c records it.

---

## Linux: consent, sudo, SELinux, keep-awake, provisioning

**Docker-group consent.** `StagedInstaller.preflight()`/`run()` forward `ask` to
`self._seams.ensure_docker(cancel=cancel, ask=ask)`. That is the whole change: the view already
passes `prompter.ask` (`catalog_view.py:303`, an `InputPrompter` from
`yulon/ui/widgets/prompt.py`), and `platform._settle_docker_group()` already puts the question in
front of the user *before* any privileged step and returns `not-asked` when there is no prompter.
`test_native.py`'s pin flips from "no prompter reaches ensure_docker" to "the prompter reaches it
and nothing else does".

On a clean box the first press is a **two-press flow, and gate 7.1 is written as one**: press 1
→ consent dialog → sudo password (below) → packages installed, `usermod` done → `_wait_docker_ready`
fails because this process is not in the group yet → preflight raises with the report's re-login
step ("log out and back in, then press Install again"). No state file exists at that point — a
preflight refusal writes none — and the second press is cheap because `docker_ready()` now answers
true and provisioning is skipped entirely, not because of any record. A declined join ends with
the declined step. No `sudoers.d`, no socket `chmod`, ever.

**sudo password, asked once.** `_ensure_docker_linux` runs each package step as its own
`sudo -n <cmd>`; `docker_engine_commands()` yields two or three of them plus the `usermod`, and
sudo's default per-tty timestamp would re-prompt on every fresh pty. The bash path asked exactly
once — `sudo -v` on one pty (`installer.py:556-558`) — and the clean Fedora/Arch gates depend on
that. So `platform._run_steps()` gains this transport: on the first "a password is required" from
`sudo -n`, it opens **one** pty, runs `sudo -v` there with the random `SUDO_PROMPT` marker,
answers from `ask`, and runs every remaining privileged step and the `usermod` on that same pty,
so the ticket the one answer bought covers them. `test_provision.py` asserts `ask()` is called at
most once per `ensure_docker()`. It is the second and last question on the path, it lives in
provisioning, and it is engine-neutral.

**buildx before gate 7.1.** The apt list already carries `docker-buildx` because the Ubuntu gate
found `docker.io` ships no BuildKit plugin; the dnf list (`moby-engine docker-compose`) and the
pacman list (`docker docker-compose`) do not, and the scripts that passed on Fedora and Arch
installed it explicitly (`docker-buildx-plugin`, `docker-buildx`). `docker.build_staged()` runs
`compose … build`, so gate 7.1 on those boxes would be the first build there without it. Add
`docker-buildx` to both lists before the gate, and record whether Fedora's `docker-compose`
package provides the `docker compose` plugin the engine invokes.

**SELinux.** Preflight facts `selinux_enforcing` and `fs_type(server_dir)`; composegen token
`BIND_LABEL` = `:z` when enforcing and the filesystem is not in the deny-list the WotLK script
carried (exfat, ntfs, ntfs3, fuseblk, msdos, vfat, cifs, smb2, nfs, nfs4, 9p), else empty — so
every template renders byte-identical off SELinux, which the byte-assertions in
`test_composegen.py` and the compose-config fixture (below) prove. `generate-compose`
additionally calls the `relabel` seam — a Python port of the Fedora script's
`selinux_label_for_containers` and all four of its callees (`chcon -Rt container_file_t` on the
bind roots, no sudo, a warning on failure). The 2026-08-25 Fedora gate proved the *bash*; the port
is proved only by gate 7.1 on Fedora.

**keep-awake.** `platform.keep_awake()`'s Linux branch spawns `systemd-inhibit
--what=idle:sleep --who=Yu'lon --why='installing <game>' sleep infinity` detached and terminates
it on exit — the `caffeinate` shape; a missing binary yields the existing "may go to sleep" line.

**Provisioning scope.** `docker_engine_commands()` (pacman with `steamos-readonly` toggling,
apt, dnf, zypper) stays the whole mechanism. Consciously **not** ported from the scripts, each
becoming a manual step in the report where the distro path fails: Docker CE repo setup, snap
Docker removal, conflicting-package purges, the pacman keyring reset, `steamos-devmode`, the
buildx shadow-binary repair and its curl fallback, the rpm-ostree layering-and-reboot loop,
`diagnose_dep_failure`, `ping github.com`, the Docker Hub HEAD probe. Every one is a host
mutation the app should not perform without a terminal in front of the user, or a probe whose
failure the real operation reports better itself.

---

## What the installer does not do

The install ends at "the server is up and ready" (answer 4). Named so nobody looks for them in
the engine:

- **Account creation** — the scripts printed instructions (three) or piped commands into
  `docker attach` and judged success by the exit code (Tortoise). `accounts.py` (SRP6 over
  `DockerSql`) is app-side for WotLK; the CMaNGOS family's account path is 7.9.
- **`realmlist.wtf` in the client** — the scripts wrote `set realmlist 127.0.0.1` into the
  client and `chmod 444`'d it. That stays `networking.write_client_realmlist()` behind its own
  button: writing into the user's client is an opt-in networking act, not an install stage.
- **The realmlist DB row for WotLK, and `playerbots.conf`** — `dml-start.sh` re-pinned the
  realmlist row by `UPDATE` on every start and copied `playerbots.conf` from its `.dist` before
  `compose up`. No script path on yulon-ubuntu ever ran it: only the default Arch script installs
  it (as the Rust launcher's start hook), and the Ubuntu variant the VM ran does not ship it. The
  2026-08-24 button run reached ready through the app's repair + start after the import race, not
  through `dml-start.sh`. Gate 7.1 is therefore the first uninterrupted native run, and it must
  show the auth log's `127.0.0.1:8085` from AzerothCore's default realmlist row with no `UPDATE`
  issued, and a worldserver that boots its bots from the `modules/` mount without the conf copy
  (AzerothCore's own entrypoint materialises `.dist` files). The realmlist row for LAN/internet
  play is the Networking tab's job through `entry.realmlist`.
- **Stopping another game's containers** — the TBC/Vanilla gaming-mode launchers stopped "the
  other expansion" before starting. Two games share 3724/8085/3306 by design; preflight's port
  scan refuses a listening port, and the Server tab is where a user stops a server.
- **Steam Deck gaming mode** — `setup-gaming-mode.sh`, as specified in the layout section. Out
  of the engine.
- **`wow-manage.sh`** — the Modules tab. Deleted with the scripts.
- **Detecting the LAN IP** — the Networking tab. `REALM_HOST` is `127.0.0.1` at install on
  every game, which is what `ready` expects.

---

## Delivery order and gates

Linux-first (answer 3). Every step has a live gate from a clean checkpoint, announced on the
activity terminal, recorded in `checklist.md` with the measured numbers that replace the
inherited floors. No bash line is deleted before gate 7.1 passes on all three Linux boxes.

**The compose-config reference.** Gate 7.1's "matches the proven install" needs a reference that
exists on a clean checkpoint. Before the checkpoint is restored, the proven yulon-ubuntu install's
`docker compose config --format json` is captured service by service (the 2026-08-24 method) and
committed as `pylauncher/tests/data/wotlk-compose-config.json`; the gate and 7.2's re-run both
diff against that file.

**Where the client runs.** Every VM is a Hyper-V guest; the clients are on the host. A login
therefore always needs the Networking tab's LAN step (the realmlist row + `realmlist.wtf` on the
host's client) after `ready`, and it is part of every gate that says "the client logs in" — 7.1
and 7.4c alike. It is not part of the install.

| Step | What lands | Gate |
|---|---|---|
| **7.1** | Spine + `AzerothCoreInstaller`, Linux native. Extract `StagedInstaller`/`Stage`; move the AC stages with their names; forward `ask`; sudo-password transport in `_run_steps` (asked once); `docker-buildx` on the dnf and pacman lists; SELinux facts + `{{BIND_LABEL}}` + relabel seam; `systemd-inhibit`; `install_wiring.py` (probe wiring + `_main()`); `wait_ready(ReadySpec)`; the compose-config fixture; catalog wow-wotlk gains `family`, `password`, `db`, `ready`, `images`, `image_prefix`, source `dest`s and loses `script_platforms`. | yulon-ubuntu, clean checkpoint, **two presses**: press 1 → consent dialog on screen → sudo dialog once → re-login report; re-login; press 2 → `ready`; record both presses and press 2's wall-clock. Kill during the build, resume skips the compile. `docker compose config` matches the fixture. Auth log shows `127.0.0.1:8085` with no `UPDATE`; bots log in. Account created; client on the host logs in after the LAN step. Then the packaged artifact on clean Fedora 44 (SELinux, password sudo, `moby-engine` + buildx) and clean Arch (pacman + buildx). |
| **7.2** | Delete the bash lineage (the list in the layout section); `platforms: []` on the three CMaNGOS entries; gaming mode → `steam-deck/setup-gaming-mode.sh`; `contribution.md`'s harness paragraph rewritten (`python -m yulon.install_wiring …`, no `sudo -v`); style-guide §3 rows for `catalog/installer.py` (options, errors, protocol, dispatch — never runs a subprocess) and `catalog/catalog.py` (no "install script"). | Full `pytest` + `mypy` + `ruff` + `black`; 7.1's Ubuntu gate re-run from the same checkpoint against the same fixture — nothing else may change. |
| **7.3** | CMaNGOS data model + stage kinds + engine, no real server: `catalog.py` family blocks, `EmulatorSource.dest`, `Source.rev`, `PasswordPlan`, `DbFacts`, `ReadyMarkers`, `ClientSpec`, `ExtractPlan`, `ConfPatchTable`, `SqlPlan`, `dockerfile_dir`; composegen tokens from data; preflight client facts + client bind probe; `families/cmangos.py`; `clientdir`, `dockerfile`, `extract`, `conf`, `sqlplan`; `docker.run_container`/`copy_from_image`/`exec_stdin`; all four catalog entries validate (CMaNGOS `platforms` still `[]`); WotLK templates render byte-identical; static invariants test; style-guide §3 rows for `families/*` and `install_wiring.py`. | Unit suite, plus a **busybox/mariadb:11 primitives gate**: `run_container` (`-u`, `:ro` refuses a write, `/out` ownership), `copy_from_image`, `exec_stdin` + gzip into a throwaway mariadb:11 (proves the `mariadb` client name and `healthcheck.sh --connect --innodb_initialized`), `wait_ready(ReadySpec)` restart-loop detection — gate the tool, not the payload. |
| **7.4a** | wow-tbc through `build`. | yulon-ubuntu: the image builds (2–4 h, record it); a kill + resume skips it; context-transfer time recorded; `.git` excluded and cmake still configures. |
| **7.4b** | wow-tbc extract + mmaps with the 2.4.3 client. | Checksum the client tree before and after — **no file written into it**; per-tool counts pass the data gates; mmaps ≥ 500; kill after `ad`, resume runs only `vmap_extractor` onward (by completion record, not by luck). If a tool refuses the `:ro`/`-i`/`-o` model, switch to the symlink-farm fallback (data change only) and record which. |
| **7.4c** | wow-tbc conf + start-db + import + up + ready; `platforms` → `["linux"]`. | Confs materialised and patched; SQL plan applied with the marker; every `warn` justified or flipped; `Avg Diff:` reached; realmd's ready line recorded into `ready.auth`; interrupted import → `partial` → reset → re-run; a second Install press over the finished install ends in seconds; playerbots visible in the logs; client on the host logs in after the LAN step. |
| **7.5** | wow-vanilla — data + templates only; `platforms` → `["linux"]`. | **With the TBC stack stopped** (shared ports), full install with the 1.12.1 client on yulon-ubuntu, including a forced vmap retry: the harness takes an env-var override that replaces the first `vmap extract` invocation with `sh -c 'echo Segmentation fault; exit 139'`; assert both vmap tools ran twice and the retry ran with `ulimit -s unlimited`. The deliverable is the diff of the two catalog entries — the proof that a second CMaNGOS game costs no Python. |
| **7.6** | wow-tortoise — data + templates; `platforms` → `["linux"]`. | The first extraction this installer has ever run against a 7272 client; boot to `Ready to login`; the update tables exist after first boot (AutoUpdate proven, or the plan gains the update phases); the client connects. `status` promoted from `wip` only then; `Source.rev` pinned the same day. |
| **7.7** | Native Windows, all four. `container_user()` policy for the new kinds' services; client-dir bind probe against a `C:\Program Files (x86)` client; 9p write throughput for extract/mmaps measured; keep-awake on the worker thread. | yulon-win11 from `clean-debloated`: WotLK first — this is where the 6.3 `ac-db-import` blocker (`user: "0:0"` unverified) is closed — then TBC, Vanilla, Tortoise. Widen `platforms` per entry as each passes. |
| **7.8** | macOS, all four — **blocked on hardware**. | The 6.2 gate, then the 7.4–7.6 gates; Phase 6's "genuinely unknown about macOS" list (Docker Desktop settings JSON, `caffeinate`, VirtioFS, `/Users` sharing) is checked against WotLK then TBC. |
| **7.9** | Controllers: `controller_wow_tbc/`, `controller_wow_vanilla/`, `controller_wow_tortoise/` mirroring `controller_wow_wotlk/`; `mysql` → `db.client` in `apply.py`/`maintenance.py`; CMaNGOS-family account creation. (The original 7.1–7.3, moved after install.) | Start/stop/logs/accounts/backup on each installed server, on every platform its install passed on. |
| **7.10** | Cross-server regression pass. (The original 7.4.) | WotLK's 6.5 coverage gate re-run after 7.1–7.9; no server's coverage regresses another's. |

7.1–7.3 are sequential. 7.4a–c are sequential and TBC-only. 7.5 and 7.6 are independent of
each other once 7.4c has passed. 7.7 waits for 7.4c; 7.8 waits for hardware; 7.9 and 7.10 follow.

---

## Blast radius on the proven WotLK path

Named, because 7.1 replaces the only path that has ever built a real server on Linux.

- `native.py`: `NativeInstaller` → `StagedInstaller` + `AzerothCoreInstaller`; the nine stage
  bodies move verbatim with their docstrings and their exact argv; the guard, state file,
  `_pump`, `_check_run`, `_held_awake`, `_record_error`, `_same_repo` are untouched; stage names
  unchanged and pinned; `ask` forwarded; `_READY_REALM_HOST` and docker.py's `"ready..."` literal
  move to catalog data with the same values.
- `composegen.py`: `DEFAULT_IMAGE_PREFIX`/`BUILT_SERVICES` come from the entry with the same
  values, so `built_image_refs()` returns the same strings; the token dict grows (`BIND_LABEL`
  renders empty off SELinux; DB tokens unused by AC templates, which `_fill()` tolerates);
  `render()` reads `password.value` where it read `install.db_root_password`; `container_user()`
  delegates to `platform.container_user_args()` with the same policy; the fixed-password path is
  otherwise unchanged; `write_plan()`'s dotenv branch runs for the first time, not on WotLK.
- Templates: `{{BIND_LABEL}}` on eight bind lines. Off SELinux the rendered files are
  byte-identical, and the committed compose-config fixture is the proof.
- `docker.py`: `wait_ready()`'s signature changes (four `test_docker.py` call sites updated);
  `wait_ready_for()` and everything the Server tab calls are unchanged.
- `catalog.json` wow-wotlk: gains the fields listed above; loses `script*` — the Linux flip.
- `installer.py`: `installer_for()` dispatches by family; `Installer` is deleted in 7.2, after
  gate 7.1 has passed three times; `_main()` moves to `install_wiring.py`.
- `preflight.py`: new facts default to `None`; WotLK's report gains one SELinux line.
- UI: `catalog_view`/`controller_view`/`main.py` call `install_wiring.installer_for_app()`,
  which also owns the fixed-password fallback both files hand-wrote; no widget changes.
- **The release window:** between 7.2 and 7.4c/7.5/7.6 a shipped build has no install path for
  TBC, Vanilla or Tortoise (their tiles say so, with the 6.1 wording). Named, accepted.
- **Not touched:** `controller_wow_wotlk/*` (probe, accounts, maintenance, console),
  `networking.py`, `state.py`, the repair button and `ContainerSpec.import_service`,
  `start_staged`/`stop_staged`/`remove_staged`, `Containers.db_import`/`client_data`, the `.env`
  port-remedy contract.

---

## Tests

Unit, no daemon, in the shape `test_native.py` already uses (a `Recorder` machine double, the
real catalog entries, the real templates):

- `test_spine.py` — state-file round trip and per-entry stage validation; guard refusals
  (copied dir, foreign game, foreign family, non-empty dir, foreign container project); `ask`
  forwarded to `ensure_docker` and to nothing else; `_pump` ordering; `Stage.recorded=False`
  never written; stage tuple uniqueness and preflight/guard exclusion asserted at construction.
- `test_families_azerothcore.py` — the 51 existing tests re-homed, unchanged in intent; **the
  WotLK stage-name tuple pinned**.
- `test_families_cmangos.py` — `Recorder` extended with container runs, image copies and SQL
  calls; a `tmp_path` server dir whose `data/` folders drive the evidence gates; `docker run`
  argv asserted **by field** (mounts `:ro`, `--user`, workdir) — audit by argv, not by string;
  refusal when `.db_password` is gone but the volume exists; conf patched in place on a second
  run; marker → skip; `partial` → reset → run; `populated` → refuse; `warn` continues and names
  the file, `fail` stops; hash mismatch reads `imported`.
- Pure modules: `test_conf.py` (byte-for-byte, `match_commented`), `test_sqlplan.py` (natural
  sort equals `ls -v` on a fixture list, gzip, `into_each`, marker hash stability),
  `test_clientdir.py` (every rule for the three specs, refuse vs warn, `required_file: null`),
  `test_extract.py` (a tool with passing counts but no completion record re-runs; evidence-file
  mismatch forces re-extract; retry trigger), `test_dockerfile.py` (marker + refuse unmarked).
- `test_catalog_invariants.py`, parameterised over all four entries: family block validates,
  `images` equals the build overlay's services, ports in exactly one file, no `{{` survives, no
  password text in generated-password templates, every referenced template exists, every SQL
  plan path is relative and under a source `dest`, every in-image `argv[0]` is absolute,
  services are named after containers, `BIND_LABEL` is on every host bind line and no named
  volume; the family module contains no game literal (case-sensitive, code only via `ast`);
  `yulon.catalog` imports no `yulon.controller_*`.
- `test_provision.py` — `ask()` at most once per `ensure_docker()`; the existing
  never-escalates assertions unchanged.
- Mutation discipline per the project's rule: purge `__pycache__` on both sides before trusting
  a red.

Integration (daemon, throwaway images, never a real server): `run_container` with busybox
(`-u`, `:ro` refusal, `/out` ownership), `copy_from_image` from busybox, `exec_stdin` + gzip into
a throwaway mariadb:11 with its healthcheck, `wait_ready(ReadySpec)` restart-loop detection
against a container that exits on purpose.

Live gates: the table above, one per step.

---

## Risks worth re-reading before 7.4a

- **Read-only extraction is unproven for CMaNGOS.** `ad`/`vmap_extractor` may insist on cwd =
  client, write a log beside `Data/`, or lack `-o`. 7.4b exists to settle this before anything is
  built on it; the fallback is a data change.
- **Host clones as build context** change the CMaNGOS build from what the scripts proved: ~1 GB
  of context per build, `.git` excluded, a nested checkout the outer update's `reset --hard` must
  leave alone. 7.4a measures; the fallback is a template change.
- **CMaNGOS master is unpinned** until `Source.rev` is used. The `spell_template` hotfix and the
  `Updates/ACID` layout are drift-sensitive.
- **`on_error: warn` reproduces the scripts' hidden outcomes on purpose.** A regression in a
  warn-phase file is visible in the log and gated by verify, not a failure on its own.
- **Windows 9p throughput** for tens of thousands of small map files is unmeasured; extract +
  mmaps may take hours there. A named volume for `data/` would fix speed and break "same
  structure on every platform" — decide with a number from 7.7, not before.
- **Deleting bash removes the only Linux path that has produced a running server.** That is
  why gate 7.1 runs on three boxes before 7.2, and Fedora native runs `moby-engine` rather than
  Docker CE — a difference nobody has measured, now with buildx added on purpose.
- **The first run on a clean Linux box is two presses.** The second is cheap because Docker
  answers, not because anything was recorded; the app must not forget the folder between them.
- **Generated passwords live in plaintext in the confs**, 0600 on Linux and ACL-inherited on
  Windows. The refusal when `.db_password` disappears is strict; the message names the file.
- **Two installs of one CMaNGOS game on one box** are refused by the guard exactly as two
  AzerothCore installs are — well-known container names, now for four games.
- **7.9 controllers** for the CMaNGOS family inherit two facts this phase fixes — the client
  binary and the schema names come from catalog data — while `apply.py` and `maintenance.py`
  still say `mysql`. A known follow-up, out of this design.

---

## What the implementer should NOT build yet

- **No third family.** If Tortoise needs one, the design is wrong; find out at 7.6.
- **No stage DSL.** A stage's argv is a typed field on a typed block. If a block starts growing
  conditionals ("when X, argv Y"), that is a family method, not a schema extension.
- **No host git.** Clones stay on `git.ContainerGit`.
- **No named volumes for `data/`** until 7.7 produces a number.
- **No account creation, no SOAP, no realmlist writer in the engine.** See "What the installer
  does not do".
- **No `mysql` → `mariadb` change in `apply.py`/`maintenance.py`.** That is 7.9 and touches the
  proven Server tab.
- **No Docker Desktop provisioning changes.** `ensure_docker()` owns it; the engine calls it
  once, now with `ask`.
- **No `.dockerignore` beyond `.git`**, no ninja progress bar, no clone retries — each deferred
  in Phase 6 and still deferred.

---

## Doc changes this phase makes

- `pyplan/README.md` — document map (this page), §3 (one engine, all platforms, "from Phase
  7"), §5 (the tree: `families/`, the stage kinds, `install_wiring.py`,
  `catalog/installers/shared/`, the deleted scripts, the new test files; the status paragraph
  says which of it does not exist yet), §9 (the Linux line struck through with the date).
  Applied on `yulon-phase7` with this page.
- `pyplan/checklist.md` — Phase 6 exit line annotated (gate lifted, items still owed); Phase 7
  preamble and items rewritten as 7.1–7.10 (the four original lines kept as 7.9/7.10). Applied
  with this page.
- `pyplan/style-guide.md` §3 — in 7.2: the `catalog/installer.py` row (options, errors,
  protocol, dispatch, never runs a subprocess) and the `catalog/catalog.py` row (no "install
  script"); in 7.3: rows for `catalog/families/*` and `install_wiring.py`, and the
  `catalog/native.py` row's "Prompt for anything" becomes "Prompt for its own decisions; forward
  `ask` to exactly one consent seam". **Applied when the modules exist**, not before: a row for a
  file that is not there teaches a reader to discount the table.
- `pyplan/contribution.md` — the CLI-harness paragraph is rewritten in 7.2: the harness is
  `python -m yulon.install_wiring <game> …`, it reaches the same engine as the button, and it
  needs neither cached sudo nor bash.
- `pylauncher/README.md` — the "Installing a server" section and the capability table, per
  step, as each gate records what is *run live*.
- `pyplan/roadmap.md` — **not edited.** Appendix A is the proposed §7 text.
- `pyplan/phase6-decisions.md` — not edited; the overturn table above is the record, and a
  reader of that page is sent here by the checklist note.

---

## Appendix A — proposed `roadmap.md` §7 (not applied)

To replace the Phase 7 block (from its `## Phase 7` heading through the `---` separator before
`## Phase 8`) on the owner's explicit word, in the roadmap's own shape — headers, numbered items,
definitions of done, no narrative.

```
## Phase 7 — One install engine for all four servers (Python everywhere; Linux first)

> Extends the 6.2 native engine to every v1 server and every platform, and retires the bash
> installers. Sequenced Linux-first: each step is gated on a real Linux machine before the next
> begins; Windows and macOS gates follow. See `pyplan/phase7-decisions.md`.

### 7.1 Spine + AzerothCore family on Linux
1. Split `catalog/native.py` into a game-agnostic `StagedInstaller` spine and
   `families/azerothcore.py`, stage names unchanged and pinned by a test. **[style]**
2. Forward `ask` to `platform.ensure_docker()`; add the once-only sudo-password transport to
   provisioning; `docker-buildx` on the dnf and pacman lists; SELinux facts and `{{BIND_LABEL}}`;
   `systemd-inhibit` keep-awake; `install_wiring.py`; a committed compose-config fixture.
3. Dispatch WotLK natively on Linux (`catalog.json`: `family`, no `script_platforms`).
4. _Definition of done:_ WotLK installs through the Install button to `ready` on clean Ubuntu,
   Fedora 44 and Arch, with the consent dialog on screen and the sudo password asked once; a
   mid-build cancel resumes without recompiling; `docker compose config` matches the fixture.

### 7.2 Retire the bash installers
1. Delete the six `install-*.sh`, `dml-start.sh`, `wow-manage.sh`, `installer.Installer`,
   `PROMPT_RULES`, the script-path tests and the `Install.script*` catalog fields; set the three
   CMaNGOS entries' `platforms` to `[]` until their own gates.
2. Extract Steam Deck gaming mode to `catalog/installers/steam-deck/setup-gaming-mode.sh`.
3. _Definition of done:_ `pytest`/`mypy`/`ruff`/`black` green; 7.1's Ubuntu gate re-run from the
   same checkpoint with no other change.

### 7.3 CMaNGOS family — data model, stage kinds, engine
1. Typed catalog blocks (`ClientSpec`, `ExtractPlan`, `ConfPatchTable`, `SqlPlan`, `DbFacts`,
   `ReadyMarkers`, `PasswordPlan`, `EmulatorSource.dest`, `Source.rev`); `families/cmangos.py`;
   the five stage-kind modules; `docker.run_container`/`copy_from_image`/`exec_stdin`. **[style]**
2. _Definition of done:_ all four catalog entries validate; WotLK templates render
   byte-identical; busybox/mariadb:11 primitives pass live.

### 7.4 WoW TBC on Linux
1. Build (7.4a), extract + mmaps with the user's 2.4.3 client (7.4b), conf + import + ready
   (7.4c), each its own live gate; `platforms` restored to `["linux"]` at 7.4c.
2. _Definition of done:_ TBC installs end to end on yulon-ubuntu; nothing is written into the
   client folder; an interrupted extract resumes per tool; an interrupted import is reset and
   re-run; the client logs in.

### 7.5 WoW Vanilla on Linux
1. Catalog data and templates only.
2. _Definition of done:_ full install with the 1.12.1 client; the change set contains no Python.

### 7.6 WoW Tortoise on Linux
1. Catalog data and templates; compile inside the Dockerfile.
2. _Definition of done:_ extraction from a 7272 client, boot to ready, update tables present,
   client connects; `status` promoted from `wip`; source pinned.

### 7.7 Native Windows, all four
1. WotLK first (closes 6.3's `ac-db-import` bind-mount blocker), then TBC, Vanilla, Tortoise.
2. _Definition of done:_ each installs from `yulon-win11`'s clean checkpoint; `platforms` widened
   per entry as it passes.

### 7.8 macOS, all four **[blocked]** on hardware
1. The 6.2 gate, then the 7.4–7.6 gates.
2. _Definition of done:_ as 7.7, on a real Mac.

### 7.9 Controllers for TBC, Vanilla, Tortoise
1. `controller_wow_<game>/` packages mirroring `controller_wow_wotlk/`; the `mysql`→`db.client`
   change in `apply.py`/`maintenance.py`; account creation for the CMaNGOS family.
2. _Definition of done:_ start/stop/logs/accounts/backup on each installed server, on every
   platform its install passed on.

### 7.10 Cross-server regression pass
1. Re-run WotLK's 6.5 coverage gate after 7.1–7.9.
2. _Definition of done:_ no server's coverage regresses another's.

**Phase 7 exit criteria:** all four v1 servers install through one Python engine with zero shell
interaction and are managed by the app on Linux and native Windows, and on macOS once a machine
exists; no `install-*.sh` remains in the repository.
```

---

## Appendix B — the judge panel (2026-08-26)

Three designs, three judges, seven criteria each scored 1–5 (style-guide fit, DRY, testability,
resume correctness, WotLK blast radius, incremental delivery, fifth-game cost; 35 max).

| | A — data stage registry | B — family engines | D — plugin per game |
|---|---|---|---|
| Maintainer | 29 | **31** | 25 |
| Operator (runs the gates) | **29** | 27 | 23 |
| Skeptic | 26 | **29** | 21 |

The operator preferred A for its per-tool extraction evidence and refuse-not-guess validation,
and marked B down for the plan-hash → `DROP` flaw and the false clone-rename premise; both are
fixed in the design above, and the per-tool evidence is grafted. The maintainer and skeptic
preferred B for stage order in typed Python, pure testable kinds, `.env` password handling and
the finest-grained delivery, and marked A down for the DSL. Every judge put D last.

Grafts adopted from A: per-tool extraction and evidence; closed mount vocabulary; token refusal;
static catalog invariants test; read-only client as the primary model. From D: one shared
`cmangos` template set; the extraction evidence file; the stage-name pin; `wait_ready_for()` kept
as a wrapper. Dropped from B as drafted: the clone-stage merge; verify-as-warning; hash-mismatch
as `partial`; characters-only player data.

The written page was then reviewed by three reviewers (maintainer, operator, skeptic) and a
review of the reviews: 46 findings, all confirmed as edits to this page, `README.md` and
`checklist.md`, none reopening a decision. The largest: counts that did not match the tree (six
scripts, eight bind lines, `DB_PASSWORD` in templates), two numbering schemes for one set of
steps, a field move that contradicted the not-touched list, the two-press first run, sudo asked
once, buildx absent from two package lists, and per-tool completion records. All are folded in.
