# T41 — the Modules tab lists the catalog and never says which modules are installed

**Status:** OPEN — replicated on a live install; fix and test next
**Filed:** 2026-09-12 by the lead, from `#-yulon` (2026-09-12 evening) and `#tech-support-live`.
**Hand:** the lead. Unit for the fix; the replication ran against a real WotLK install on `yulon-win11`.

## The report

A player who pointed Yu'lon at an install he already had:

> It detected my ancient months old install of wotlk 🤗
> **None of modules detected lol.**

And Lac, the same evening:

> I can't get any modules to work, I have tried transmog and lootpet

Baerthe: "Yeah I think our module system is still a bit buggy." The owner: "Ill look
into it, I havent tested the module system enough."

## Root cause (read, then measured)

`ControllerView.reload_modules()` (`ui/controller_view.py:5509`) fills the list from the
manifest **store** and nothing else:

```python
for kind in FAMILY_FILES:
    items = list(store.load_all(kind))
    for manifest in items:
        item = QListWidgetItem(f"[{manifest.type}] {manifest.name} — {manifest.description}")
```

That is the CATALOG — what this game *could* install. Nothing in the tab reads
`<server_dir>/modules/`. The only call that does, `module_updates()`, sits behind the
"Check for updates" button and costs a `git fetch` per checkout, so it is not what the
list is built from.

So a user who adopts an install with modules already in it sees 41 rows, none of them
marked, and no sign that the modules he has are installed. "None of modules detected" is
exactly what the tab shows him — and it shows the same thing to a user who has just
installed one successfully, which is the other half of "I can't get any modules to work".

**Measured on the live install** (`yulon-win11`, `C:\Users\pk\wow-server-playerbots`, the
`mod-playerbots/azerothcore-wotlk` fork, `ac-worldserver` healthy), driving Yu'lon's own
`apply_module()` at `cc8fc51a` — not a hand-clone and not the GUI:

```
modules/   : ['.gitkeep', 'CMakeLists.txt', 'ModulesLoader.cpp.in.cmake', 'ModulesPCH.h',
              'ModulesScriptLoader.h', 'create_module.sh', 'how_to_make_a_module.md',
              'mod-playerbots']
manifests  : 41 total
```

`mod-playerbots` is on disk; the list shows 41 catalog rows and says nothing about it.

## What is NOT broken, and had to be checked to know that

The same probe ran a real install of `mod-transmog` through `apply_module()`:

```
DONE (2, first run):
   + clone https://github.com/azerothcore/mod-transmog.git → modules\mod-transmog
   + activate env/dist/etc/modules/transmog.conf from conf/transmog.conf.dist
SKIPPED (1):
   - conf: no value in the catalog for Transmogrification.Enable, … — not written
PENDING SQL (2): three db-world files, one db-characters file, left to db-import
rebuild_required: True
```

The clone lands, the conf template is deployed (`Transmogrification.Enable = 1`, read back
off disk), and the SQL is queued for the importer by design. **The install path works.**

The `SKIPPED` line is about the five keys the manifest names with no `default`, not about
the file — a correction worth recording, because on a re-run the "activate" step is absent
(the target already exists) and the skip line alone reads as "the conf was never written".
It was. Three runs of the probe is what made the difference visible: run 1 logged
`2 step(s)`, runs 2 and 3 `1 step(s)`.

## Not this ticket: Lac's build failure

Lac's `InstallerError: the build failed (exit 1)` after 55 seconds is a separate defect and
is still unreproduced. A cold `docker compose build ac-worldserver` with `mod-transmog`
cloned in ran well past 55 seconds on `yulon-win11` and into the runtime stage, so it does
not fail at configure the way his did. T38 is what will make his next failure legible.

## Still owed

- The failing test.
- The fix: the list must say what is installed, including a module on disk the catalog has
  never heard of — `module_updates()`'s docstring already takes that position for its own
  rows ("A module on disk that the store has never heard of still gets a row").
- A review.
- A reply to the two reporters.
