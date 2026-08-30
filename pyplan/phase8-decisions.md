# Phase 8 — uninstall/purge: decisions and why

> Companion to `pyplan/roadmap.md` §8. The roadmap says Phase 8's scope is TBD; this page settles
> one feature inside it — removing an install — and records what was rejected, so a reviewer can
> challenge the reasoning before any code exists.
>
> **Provenance, stated honestly.** Written 2026-08-31 in a single scoping session, prompted by a
> user question ("is there an uninstall/purge function for each of the server options?"). The
> method was NOT `phase6-decisions.md`'s or `phase7-decisions.md`'s: no three independent
> architectures, no three judges. What it rests on is a read of the current code
> (`docker.py`, `composegen.py`, `native.py`, `state.py`, `catalog.json`, `7.2-retire-bash.md`)
> and two decisions the owner answered directly. Anything here not marked as verified is a claim
> awaiting a gate, and this page should be upgraded to the full method before it becomes a plan.
>
> **Status: not scoped into numbered steps, and blocked on Phase 7.** Phase 8 must not begin
> until Phase 7 exits (roadmap §8, ordering rule). This page exists so the feature is not
> forgotten and so Phase 7 does not accidentally make it expensive.

---

## The decision

**One "Uninstall" action per install, scoped to exactly what Yu'lon created: the server folder,
that install's Docker project, and its record in `state.json`. It reaches nothing else on the
machine. An unticked "Keep my characters" checkbox decides whether the database volume survives.
What to remove is discovered by asking Docker about the pinned compose project — not from a
ledger the installer writes.**

---

## The owner's two answers (2026-08-31)

Asked one at a time, before any design existed. They are decisions, not preferences, and the rest
of this page does not re-litigate them.

| # | Question | Answer |
|---|---|---|
| 1 | Beyond the server folder and its Docker objects, what else should purge remove? | **Only the launcher's own state record.** Not firewall rules, not module files in the WoW client, not Docker itself. |
| 2 | Should characters survive an uninstall? | **One button with a "Keep my characters" checkbox**, unticked by default. Not two buttons; not an unconditional wipe. |

Answer 1 is what makes this feature small. Each thing it excludes is excluded for a reason worth
keeping:

- **Firewall rules** — `networking.py` builds *add* commands for ufw/firewalld/netsh. Matching
  *delete* commands are a second sudo path and a live gate per backend, to reverse something that
  costs the user nothing if left. Not worth it.
- **Module files in the WoW client** — the manifests already carry `when: "remove"` and the
  Controller already has a per-module Remove action, so the capability exists. Purge still does
  not call it: the client folder is the user's, Yu'lon did not create it, and deleting inside it
  on the way out is the kind of surprise this project's copy promises never to spring.
- **Docker itself** — other things on the machine may depend on it, and on Windows/macOS
  uninstalling it means driving a vendor installer, not running a command.

---

## What Phase 7 changes about this

The feature is **cheaper after 7.2 than before it**, which is why it is deliberately not being
built now.

`7.2-retire-bash.md` deletes all six `install-*.sh`, `dml-start.sh`, `wow-manage.sh` and the
script-path `Installer`. Every server then installs through the staged native engine with
generated compose. That removes the two largest costs an uninstall feature would have carried:

| Cost before 7.2 | After 7.2 |
|---|---|
| Read ~7,500 lines of bash across three installers to learn what each creates outside its server dir | Gone — the scripts are gone |
| Per-game removal code, because three games install by a mechanism the app does not model | Gone — one implementation over the family spine |
| Guess at volume and image names a script chose | Gone — `composegen` names them |

**Nothing needs to be added to Phase 7 on this feature's account.** An artifact ledger was
considered and rejected (below), so no `InstallState` field, no `STATE_VERSION` bump, and no
stage-level `undo` is owed by the Phase 7 spine.

**One thing Phase 7 should get right anyway**, because it is free now and unfixable later: each
CMaNGOS family template must put the database on a **named volume inside the compose project**,
as WotLK's `db-data` is — never a bind mount to a host path outside the server dir. A bind mount
outside the server dir is the one shape that makes a clean uninstall impossible, and it would be
discovered in Phase 8 with the templates already shipped.

---

## Why, and what was rejected

The real question is how uninstall learns what to remove. Three answers were considered.

**Chosen — ask Docker.** `composegen` pins a compose project name of
`yulon-<game_id>-<install_id>`, where `install_id` is a hash of the install directory's absolute
path, and `docker.pinned_project_name()` reads it back. Every container, volume, network and
image of an install is inside that project. Uninstall scopes every command to it.

- No new state, no schema change, no migration.
- Works on installs made before this feature existed — which no ledger can.
- `install_id` being path-derived is what makes "Keep my characters" actually work: reinstalling
  to the same folder produces the same project name, so the kept volume is found again rather
  than orphaned. **Unverified — owed a test**, and it is the single assumption the checkbox rests
  on.

**Rejected — an artifact ledger in `.yulon-install.json`.** The engine would record every volume,
image and directory it creates; uninstall replays the list backwards. Precise and auditable, and
it is what a from-scratch design would probably pick. Rejected because `InstallState` is a frozen
dataclass with a `version` field, so it needs a `STATE_VERSION` 1→2 bump and a migration; because
every install made before the ledger existed still needs the discovery path, so the ledger is a
second mechanism rather than a replacement; and because two sources of truth for "what does this
install own" is exactly the drift `GENERATED_MARKER` exists to prevent.

**Rejected — an `undo` callable on every `Stage`.** Symmetric with the Phase 7 spine and tempting
for that reason. Rejected because it doubles the surface of every stage in both families to serve
one caller, and because most stages have no meaningful inverse: the undo of `clone` is "delete
the folder", not a reverse clone. The asymmetry is real — install is nine stages, removal is four
commands — and the design should say so rather than force a shape onto it.

---

## Architecture

### `yulon/purge.py` — plan, then run

Split in two, because the dialog must show real numbers before anything is touched.

- `plan()` — resolves the pinned project, lists its containers, volumes and images, measures the
  server folder on disk, and reports what it could not determine. Reads only.
- `run()` — executes and streams lines, like every other long action, into the existing job
  runner and log panel. No new UI plumbing.

### Ownership is proved before anything is removed

Reuses `remove_staged()`'s census unchanged: if Docker will not say which project owns a
container, uninstall **refuses** rather than guessing. This is the same rule `GENERATED_MARKER`
enforces for compose files — the engine only ever destroys its own work — and it exists for the
incident in `rust-prior-art.md` §1, where a state file dropped into the wrong directory made the
generator overwrite a real server's compose file and orphan its character volumes. A feature
whose entire job is deletion inherits that rule first, not last.

### A new Docker function, not a flag on the old one

`docker.remove_staged()` keeps its documented "volumes are never touched" guarantee and the test
that asserts `-v` never appears in its argv. Uninstall gets its own entry point. The checkbox
selects between the two paths; it does not weaken either.

This is deliberate: `remove_staged()` is offered on a *running* server under copy that says
characters are unaffected. Adding a data-destroying flag to that function would put one argv away
from each other the safest action in the Controller and the most destructive one.

### Folder deletion has two traps

- **Windows**: git packs and objects are written read-only, so `shutil.rmtree` fails partway and
  leaves a half-deleted checkout. Needs an `onerror` handler that clears the read-only bit and
  retries.
- **WSL-resident installs** (`KnownInstall.wsl_distro`): the folder is inside the distro, not on
  the Windows filesystem. It must be removed inside the distro via `wsl.exe`, on the same path
  `docker.py`'s `wsl_distro=` argument already threads through every call.

### `state.forget()` runs last

It already exists, and the Controller already has a "Forget that record" button. Calling it last
means a failed uninstall leaves the record pointing at a server that is still there — the
recoverable failure — rather than a folder no surface can reach.

### The dialog

```
  Uninstall WoW WotLK
  ---------------------------------------
  This removes ~/wow-server-playerbots
  (48 GB) and this server's containers,
  images and volumes.

  [ ] Keep my characters
      (the database volume is left alone)

  Type the server name to confirm:
  [                    ]

           [ Cancel ]  [ Uninstall ]
```

The size comes from `plan()`. The typed confirmation is the same pattern the restore action
already uses for a destructive step.

---

## What uninstall does not do

Stated as flatly as `phase7-decisions.md` §"What the installer does not do", because the
boundaries are the feature:

- It does not touch the user's WoW client folder, including files a module put there.
- It does not remove firewall rules, or any other host configuration.
- It does not uninstall Docker, WSL, or any dependency Yu'lon provisioned.
- It does not remove backups. Those live to be restored; an uninstall that deleted them would
  make "Keep my characters" pointless in the one case it matters.
- It does not touch another install of the same game in another folder. Different path, different
  `install_id`, different project.
- It does not act when ownership cannot be proved. It refuses and says why.

---

## Open items (unverified — settle during implementation, not now)

1. **Image removal flag.** `--rmi local` removes only images with no custom tag, and `composegen`
   sets `image: yulon.local/ac-wotlk-…`, which is a custom tag. So `--rmi local` probably does
   **not** remove the built worldserver image. Likely `--rmi all`, or an explicit prefix-scoped
   `docker image rm`. Verify against a real install before writing the argv into a test.
2. **The kept volume is found on reinstall.** The claim under "Keep my characters" above. Owed a
   test that installs, uninstalls with the box ticked, reinstalls to the same path, and finds the
   characters.
3. **Folder size on disk** for a 40–50 GB checkout, measured fast enough for a dialog. A naive
   recursive walk over an AzerothCore checkout may not be.
4. **Cancel semantics.** Every other long action can be cancelled. A half-cancelled deletion is
   worse than a finished one, so uninstall may be the one action that refuses to cancel once
   past a point. Decide it deliberately.

---

## Cost and gates

Post-7.2, over all four servers at once: **roughly 2–3 days**, dominated by tests rather than
code. Pre-7.2 the same feature was estimated at about a week, most of it auditing bash.

Held to the same bar as every other phase — a destructive action earns no lighter gate:

- Unit tests, including the argv assertions on both Docker paths.
- A live gate per platform on the VMs (owner's standing rule: never install, compile or run a
  server on the laptop — VM or test box only): install → uninstall
  with the box ticked → reinstall → characters present; then install → uninstall unticked →
  nothing of the project remains.
- The gate runs on WotLK and on one CMaNGOS game, since the families are the two shapes.

**Definition of done:** every v1 server can be uninstalled from the Controller on Linux, macOS and
native Windows; the ticked box provably preserves characters across a reinstall to the same
folder; and an install whose ownership cannot be proved is refused rather than removed.
