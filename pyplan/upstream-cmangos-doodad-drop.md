# Upstream CMaNGOS defect: WMO doodad placements are dropped on case-sensitive filesystems

Written 2026-09-04 by lane D of the Phase 7 gate work. The first nine sections are addressed to a
CMaNGOS maintainer and assume no knowledge of this project. §10 is ours: whether Yu'lon should work
around the defect while it is open, and it is a recommendation, not a decision.

Evidence: `pyplan/gates/7.7-win11-gate/buildings-shortfall-measurements.txt` and the file lists
beside it. Recorded in `pyplan/checklist.md` under 7.7.

---

## 1. In one paragraph

`vmap_extractor` writes each WMO's interior doodad models to `Buildings/` under the **raw** name
found in the WMO's `MODN` chunk (`INNBED.M2`), and then looks the same model up under the
**case-normalised** name (`Innbed.m2`) when it comes to write that doodad's placement into
`Buildings/dir_bin`. On NTFS or a case-insensitive APFS the lookup resolves anyway and the
placement is written. On ext4 it misses, and `Doodad::ExtractSet()` does `if (!input) continue;` —
the placement is discarded, silently, and the run finishes with `Work complete. No errors.`

Same client, same source commit, two filesystems, two different servers, no message either way.
Measured on WoW 1.12.1 with `cmangos/mangos-classic` at `8ec338a1`: the Linux extraction wrote
**429,016** placements where the Windows one wrote **503,722**. **74,706 placements, 14.8%,
dropped without a word.**

---

## 2. What a player sees — stated conservatively

Nothing disappears and nothing looks wrong. The client draws the world from its own local data,
which is complete, and client-side collision is unaffected: a player still cannot walk through the
bed. The loss is entirely server-side, in the collision/line-of-sight geometry the core loads from
`vmaps`, and it is confined to **M2 models placed inside WMOs** — building and dungeon interiors.
Terrain is untouched, the building shells are untouched (the same 814 `.wmo` files were present on
both sides), and outdoor doodads placed from ADT `MDDF` records are untouched, because
that code path is correct (§4).

What is actually affected, in decreasing order of confidence:

* **Line of sight.** `vmaps` are the store the core answers `IsInLineOfSight()` from. A prop that
  should block a spell or an aggro check does not block it, so a caster can be hit through a
  bookshelf, and mobs notice players they should not be able to see. Concentrated in dungeon
  interiors — of the 296 models that lost every one of their placements, the list reads like the
  furniture manifest of a dungeon: `Scholme_Bookshelf`, `Scholme_Bookshelflarge`, `Uldamantable01`,
  `Wc_Cairn` and the Wailing Caverns druid props, `Blackrockbloodmachine01`–`04`, `Elfbed01`–`03`,
  `Wardrobedwarvenaverage02`, braziers and candelabras by the dozen. One of them is
  `Auctioneercollision.m2` — a model that exists for nothing but collision, so its loss is the
  whole of its purpose.
* **NPC and bot pathing.** `contrib/mmap/src/TerrainBuilder.cpp:561-573` builds the navmesh by
  loading `vmaps` for each tile, so a doodad missing from `vmaps` is missing from `mmaps` too.
  Creatures and bots then path as though the props were not there, and route through a bookshelf
  the client draws them clipping into.
* **Server-side height.** `GetHeight()` also consults `vmaps`. We did not measure a specific
  in-game consequence and are not claiming one.

Honest scale: this is a degradation, not a breakage. A server built from a Linux extraction runs,
is playable, and most players would never name what is wrong — but 14.8% of the world's placed
collision geometry is absent from it, permanently, and nobody was told.

---

## 3. Affected trees

Read out of the checkouts, not assumed:

| repo | commit read | `wmo.cpp` `MODN` handler | `ExtractSingleModel()` |
|---|---|---|---|
| `cmangos/mangos-classic` | `8ec338a1704e7dcb1c0213eb7ed58f9231ade40f` | line 85, defective | `gameobject_extract.cpp:9`, defective |
| `cmangos/mangos-tbc` | `f82e7d679c283b66bc2adc1b751aa1275e655673` | line 85, byte-identical | byte-identical |

`mangos-wotlk` was not read and is presumed to carry the same file. AzerothCore's `vmap4extractor`
descends from the same code and was **not** checked; anyone relying on that lineage should read its
equivalent of these two functions before assuming it is clean.

---

## 4. The two functions, and the one that gets it right

Three call sites hand a model path to `ExtractSingleModel()`. Its own header states the
precondition:

```c
// vmapexport.h:53
/* @param origPath = original path of the model, cleaned with fixnamen and fixname2
 * @param fixedName = will store the translated name (if changed)
 * @param failedPaths = Set to collect errors
 */
bool ExtractSingleModel(std::string& origPath, std::string& fixedName, StringSet& failedPaths);
```

**`adtfile.cpp` obeys it**, and its own comment says why the order matters:

```c
// adtfile.cpp:148-156, MMDX handler
while (p < buf + size)
{
    fixnamen(p, strlen(p));
    char* s = GetPlainName(p);
    fixname2(s, strlen(s));
    string path(p);                         // Store copy after name fixed
                                            //          ^^^^^
    std::string fixedName;
    ExtractSingleModel(path, fixedName, failedPaths);
```

**`wmo.cpp` does not.** The copy is taken one line too early — before the two functions mutate the
buffer — so the path handed on is the raw one:

```c
// wmo.cpp:85-105, MODN handler
else if (!strcmp(fourcc, "MODN"))
{
    char* ptr = f.getPointer();
    char* end = ptr + size;
    DoodadData.Paths = std::make_unique<char[]>(size);
    memcpy(DoodadData.Paths.get(), ptr, size);   // raw names, kept for ExtractSet()
    while (ptr < end)
    {
        std::string path = ptr;                  // <-- COPY TAKEN BEFORE THE FIX

        char* s = GetPlainName(ptr);
        fixnamen(s, strlen(s));                  // fixes the buffer; `path` already holds the raw name
        fixname2(s, strlen(s));

        uint32 doodadNameIndex = ptr - f.getPointer();
        ptr += path.length() + 1;

        std::string fixedName;
        if (ExtractSingleModel(path, fixedName, failedPaths))   // <-- RAW path passed
            ValidDoodadNames.insert(doodadNameIndex);
    }
}
```

`ExtractSingleModel()` then names the output file from whatever it was given, applying neither
function itself:

```c
// gameobject_extract.cpp:9-33
bool ExtractSingleModel(std::string& origPath, std::string& fixedName, StringSet& failedPaths)
{
    ...
    // ".MDX" -> ".M2" by erase(len-2, 2) + append("2")   =>  INNBED.MDX -> INNBED.M2
    fixedName = GetPlainName(origPath.c_str());          // line 26 — raw plain name

    std::string output(szWorkDirWmo);
    output += "/";
    output += fixedName;                                 // Buildings/INNBED.M2

    if (FileExists(output.c_str()))
        return true;
```

And the reader, running over the same `MODN` bytes that `memcpy()` preserved in their raw form,
re-derives the **fixed** spelling and opens that:

```c
// model.cpp:223-260, Doodad::ExtractSet()
    char ModelInstName[1024];
    sprintf(ModelInstName, "%s", GetPlainName(&doodadData.Paths[doodad.NameIndex]));  // INNBED.MDX
    uint32 nlen = strlen(ModelInstName);
    fixnamen(ModelInstName, nlen);          // Innbed.mdx
    fixname2(ModelInstName, nlen);
    ...                                      // ".mdx"/".mdl" -> ".m2"   =>  Innbed.m2

    char tempname[...];
    sprintf(tempname, "%s/%s", szWorkDirWmo, ModelInstName);
    FILE* input = fopen(tempname, "r+b");    // Buildings/Innbed.m2
    if (!input)
        continue;                            // <-- the placement is dropped, silently
```

**The writer writes `INNBED.M2` and the reader asks for `Innbed.m2`.** That is the whole defect.

Witnessed on the Linux box, in a `Buildings/` directory the extractor had just filled:

```
$ ls Buildings/Innbed.m2
ls: cannot access 'Buildings/Innbed.m2': No such file or directory
$ ls -l Buildings/INNBED.M2
-rw-r--r-- 1 pk pk 840 ... Buildings/INNBED.M2
```

*Adjacent, not part of this report and not investigated:* at `model.cpp:251-252` the `.mdx` → `.m2`
shortening writes a terminating `'\0'` into `ModelInstName` without decrementing `nlen`, and `nlen`
is what the record at the end of the function declares and writes. We did not test what the loader
makes of the extra byte, and we are not claiming it is a bug.

---

## 5. Why it is silent

Two separate silences, and either one alone would have made this visible:

* `Doodad::ExtractSet()`'s `continue` prints nothing and counts nothing. 74,706 dropped placements
  produced zero output lines.
* The extractor's existing "some models could not be extracted" warning does not fire, because from
  its point of view nothing failed to extract. The model file was written. It was written under a
  name nobody later asked for.

The Linux run's own summary was `Extract for VMAPs05. Work complete. No errors.`

---

## 6. Minimal reproduction — one Linux box, one extraction, no third-party tooling

This needs neither a second machine nor a comparison run. A model file with **zero** placements in
`dir_bin` is already proof, because the only reason a WMO doodad model is extracted at all is that
some WMO's `MODN` named it and its doodad set placed it.

1. Build `contrib/vmap_extractor` from `cmangos/mangos-classic` (or `mangos-tbc`) as usual.
2. Run it against any 1.12.1 client, on a **case-sensitive** filesystem (ext4 is enough).
3. Then:

```sh
cd <client>/Buildings

# The model was extracted, under the raw all-caps name — the writer's spelling:
ls INNBED.M2                                          # present, 840 bytes
ls Innbed.m2                                          # No such file  <- the reader's spelling

# And it is placed nowhere, under either spelling:
strings -a dir_bin | grep -o -i 'innbed\.m2' | wc -l  # 0

# The same sweep over every all-caps model, in one pass, if one witness is not enough:
strings -a dir_bin | tr 'A-Z' 'a-z' | grep -o '[a-z0-9_.-]*\.m2' | sort -u > /tmp/placed
ls *.M2 | tr 'A-Z' 'a-z' | sort -u > /tmp/extracted
comm -23 /tmp/extracted /tmp/placed | wc -l           # extracted, placed nowhere
```

A model that was extracted and placed nowhere is the dropped placement. Repeat on a
case-insensitive filesystem and `dir_bin` grows; §7 is how we did that.

---

## 7. How it was found

By accident, and then by holding every variable still.

A Windows gate and a Linux gate installed the same server, and the `Buildings/` file counts
disagreed: **5,076 on Linux, 3,913 on Windows**. The first reading was "Windows is short", and it
was backwards. Diffing the two sorted listings instead of comparing their totals:

* 1,163 names in the Linux listing and not the Windows one; **0** the other way.
* All 1,163 are `.m2`. Every one has a case-insensitive twin present on Windows — **1,163 of
  1,163**. Unique case-insensitive names in the Linux listing: **3,913**, the Windows file count to
  the file.
* `md5` and size of all 1,163 pairs, computed on the Linux box where both spellings exist: **SAME
  1,163 / DIFF 0.** NTFS had folded byte-identical duplicates onto their twin, and the Windows run
  had lost no unique bytes at all.

So the file count was a red herring, and the real comparison was the placement index:

| | Linux (ext4) | Windows (NTFS) | difference |
|---|---|---|---|
| `Buildings/dir_bin` | 31,072,203 B | 36,635,438 B | +5,563,235 B (15.2%) |
| distinct model names in it | 2,981 | 3,348 | +367 (11.0%) |
| placements | **429,016** | **503,722** | **+74,706 (14.8%)** |
| `Buildings/temp_gameobject_models` | 24,991 B | 24,991 B | — |

Not a truncated tail: 284 of the 367 Windows-only names first appear *below* Linux's own
end-of-file offset.

The distribution of those 367 is what makes the diagnosis airtight, because no other cause produces
it:

* **296** exist on the Linux box **only** in all-caps (`INNBED.M2`, no `Innbed.m2`). No ADT ever
  referenced them, so no correctly-named copy was ever written, so the lookup could never hit.
* **71** exist in both spellings — an ADT elsewhere in the run happened to write the fixed-name
  copy, and whether the lookup hit depended on whether that happened first. Order-dependent.
* **0** exist in neither spelling. The model was always on disk. Only the name was wrong.

Everything else was held: all 20 files under `Data/` matched byte for byte across the two boxes,
`wmo.MPQ` — the archive this tool reads — hashed identically
(`9933d9ca...fc56edb7`), and the source commit `8ec338a1` was read out of each box's own clone
rather than taken from a pin.

---

## 8. The fix

Make the writer use the spelling the reader asks for. The smallest change that does that, and the
one we would suggest, is in `ExtractSingleModel()` — it repairs all three call sites at once and
leaves `origPath` alone, so the path used for the MPQ read is unchanged:

```diff
--- a/contrib/vmap_extractor/vmapextract/gameobject_extract.cpp
+++ b/contrib/vmap_extractor/vmapextract/gameobject_extract.cpp
@@ -23,6 +23,10 @@ bool ExtractSingleModel(std::string& origPath, std::string& fixedName, StringSet
     // nothing do
 
     fixedName = GetPlainName(origPath.c_str());
+    // The output filename must be spelled the way Doodad::ExtractSet() will later
+    // ask for it (model.cpp), or the placement is dropped on a case-sensitive
+    // filesystem. wmo.cpp's MODN handler passes a path this was not applied to.
+    fixnamen(&fixedName[0], fixedName.length());
+    fixname2(&fixedName[0], fixedName.length());
 
     std::string output(szWorkDirWmo);
     output += "/";
```

No new include is needed: `ExtractGameobjectModels()` in the same translation unit already calls
both functions at lines 67 and 69.

**The one thing this depends on** is that both functions are idempotent, so the ADT and
GameObjectDisplayInfo callers — which already pass a cleaned path — see no change. Reading them
says they are: `fixnamen()` title-cases each alphabetic run and lower-cases the extension, and a
name already in that form is a fixed point; `fixname2()` replaces `' '` with `'_'`, and there are
none left to replace. That is a claim from reading, not from running, and it is the assertion a
maintainer should test first.

**The alternative fix**, which is arguably the more natural one, is to move `std::string path =
ptr;` in `wmo.cpp` below the `fixnamen`/`fixname2` pair, exactly as `adtfile.cpp:153` already does.
One line moved. We prefer the first because this one *also* changes the path handed to `Model
mdl(origPath)` for the archive read, and `fixname2()` turns spaces into underscores. There is at
least one model in the 1.12.1 archives whose ADT-path lookup already fails in a way consistent with
that — `Could not find file of model World\Generic\Quilboar\Passive Doodads\Leantos\
Razorfen_Leanto03.m2`, printed once on both boxes, with the space kept in the directory and an
underscore in the plain name, which is the exact shape `fixname2()` on a plain name produces. We
did not prove that connection, but it is a reason not to widen the same code path to WMO doodads.

**Separately, and worth taking regardless of which fix lands:** `Doodad::ExtractSet()`'s `continue`
should count what it drops and print the total. Had it done so, this would have been a one-line
report from the first Linux run instead of a two-day cross-platform diff.

### What the fix costs someone who re-runs an extraction

* **On a case-insensitive filesystem it changes nothing.** NTFS and case-insensitive APFS already
  resolved the mismatch, so Windows and most macOS users re-extract for no benefit. The
  beneficiaries are case-sensitive filesystems — which is to say Linux, and every Docker image
  built on one.
* **The on-disk names change.** WMO-only doodad models move from `INNBED.M2` to `Innbed.m2`.
  `ExtractSingleModel()`'s `FileExists()` early-return keys on the new name, so a re-run into an
  existing `Buildings/` re-extracts them and leaves the old all-caps files behind as dead weight —
  in our vanilla measurement that duplicate set was 1,163 files.
* **`dir_bin` and the model files must come from the same run.** A `dir_bin` written before the fix
  names `INNBED.M2`; a fixed extractor writes `Innbed.m2`. Mixing them is worse than either. The
  practical instruction is: delete `Buildings/`, re-run `vmap_extractor`, then `vmap_assembler`,
  then `mmaps_generator` — the last because the navmesh is built from `vmaps`
  (`contrib/mmap/src/TerrainBuilder.cpp:561`). In practice the extractor's own "Your output
  directory seems to be polluted" refusal makes extracting on top unavailable anyway.
* **Measured price of that:** `vmap_extractor` alone took **83 minutes** on our Windows gate box
  (19:32:18 → 20:55:57, `pyplan/gates/7.7-win11-gate/vanilla77.log`) for the 1.12.1 client.
  Assemble and mmaps are on top of that. We have no equivalent figure for the Linux box.
* **And the payoff is invisible in a file listing.** After the fix a Linux `Buildings/` gets
  *smaller* (no duplicate spellings) while `dir_bin` gets *larger*. Anyone checking the fix by
  counting files will read it as a regression. Count placements in `dir_bin`.

---

## 9. Reporting checklist

Everything a maintainer needs is above. If more is wanted, the raw artefacts are:
`buildings-shortfall-measurements.txt` (every number with the command that produced it),
`linux-buildings.txt` / `windows-buildings.txt` (the two sorted listings),
`only-linux.txt` / `only-windows.txt` (the `comm` output, the second one empty),
`collision-pairs.txt` (the 1,163 twin mappings), `pair-compare.txt` (md5+size of every pair),
`win-dirbin-names.txt` / `linux-dirbin-names.txt`, `dirbin-win-only-names.txt` (the 367) and
`dirbin-win-only-capsonly.txt` (the 296 — 295 lines, the file has no trailing newline). All under
`pyplan/gates/7.7-win11-gate/`.

---

## 10. Ours to decide: should Yu'lon work around it?

**Recommendation: yes — carry the patch — but file the report first, and pin the revs before the
patch, because we do not currently pin them at all.**

### The premise needs a correction first

The brief for this page said "we already clone at a pinned rev". We do not, for the trees that
matter. `Source.rev` exists in `yulon/manifest.py:71` (40 hex characters, default `None`) and
`catalog.json` sets it on exactly one entry — `wow-tortoise`, two sources. `wow-vanilla`,
`wow-tbc` and `wow-wotlk` all clone the branch tip with `rev: null`. That `8ec338a1` was the commit
on both gate boxes is because both clones happened within a day of each other, not because anything
held it.

This changes the shape of the workaround. A patch applied to a moving tip breaks the first time
upstream touches those lines — **including when they fix it**, which is the good outcome and would
then fail every install.

### The three options, weighed

**A. Do nothing and wait for upstream.** Costs nothing to implement and leaves every Linux install
we make short 14.8% of its placed collision geometry, silently, for an unbounded time. It also
leaves our two platforms producing measurably different servers from the same inputs, so any
cross-platform check of extraction output stays untrustworthy unless it folds case. Both affected
repos are actively developed — the tip we read was dated 2026-08-30 — which is a reason to expect a
response, not a date to plan against.

**B. Patch the source after clone.** The seam exists and is clean. `families/cmangos.py:217` binds
`Stage("clone-sources", self._clone_sources)`; `_clone_sources` at `:233` delegates to
`NativeInstall.stage_clone_sources()` (`yulon/catalog/native.py:1476`), which walks the entry's
`emulator.sources` and calls `self._clone(git.CloneSpec(...))` at `:1523`. The patch belongs in a
**new stage after it**, not inside that loop — that loop's single job is "clone what the manifest
names", and the family already owns its own stage list. So: a `patch-sources` stage in the CMaNGOS
family, a patch artifact committed in this repo, a state-file record so a resume does not apply it
twice, and a tolerant apply — detect the fix already present and skip; refuse loudly, naming the
file and line, if the context has moved — because that is what makes the stage survive the day
upstream lands their own fix.

**C. Detect the shortfall after extraction and warn.** Cheap and mechanical: compare the distinct
model names in `Buildings/dir_bin` against the model files in `Buildings/`. On our Linux run that
reads 2,981 against 3,913 unique models — 932 extracted models with zero placements, a number that
cannot be innocent — and the manifest already has the shape for a check that refuses or warns
(`yulon/manifest.py`, the mmaps shortfall field). But a warning has no remedy attached: it tells a
user their dungeon interiors are short and offers them nothing to do about it, and it improves no
player's server by one placement.

### Why B

A and C both leave the defect in the product. Linux is our primary platform, the loss is silent and
permanent per install, and the fix is three lines in a file that changes rarely. C is worth building
**as the gate that proves B worked** — not as a shipped warning — because it is the only check that
would have caught this in the first place and the only one that will catch a patch that silently
stops applying.

### What B costs, plainly

1. **Pin `rev` on the two CMaNGOS entries first.** That is a change with its own consequences:
   installs stop picking up upstream fixes until someone bumps the pin, so the pin needs an owner
   and a cadence. It is worth doing on its own merits — an unpinned tip means no two installs are
   the same build — but it is a decision, not a detail.
2. **A real feature, not a one-liner.** New stage, patch artifact, resume-safety, and tests for
   three cases: applies, already applied, does not apply. Comparable in size to any other single
   stage in that family.
3. **No retro-fit.** Existing Linux installs stay deficient. The benefit arrives only on a fresh
   extract — 83 minutes for `vmap_extractor` on the gate box, plus assemble and mmaps.
4. **A standing obligation.** While the patch is carried, any extractor oddity is ours to disprove
   before it is upstream's to explain; and when upstream merges the fix, someone has to notice and
   delete ours.
5. **First action is free.** File the report. If upstream takes it in days, B never ships and we
   have spent nothing but this page. Hold B ready rather than starting it, and pin the revs now.

The owner decides. This lane wrote the page and implemented nothing.

---

## 11. What was done (2026-09-05, lane `doodad`, branch `lane/doodad`)

The owner decided for B, with the pins first and option C as the gate. Recorded here in the
order it happened, with what was measured and where.

**Pins.** Every source of every shipped entry now carries a `rev`, read out of the gate boxes'
own checkouts rather than a branch tip: Vanilla's three from `/home/pk/vanilla-75` on `m910q`
(gate 7.5; core `8ec338a1`, the commit both boxes in §7 were on), TBC's three from
`/home/pk/tbc-7.4c` on `m910q` (gate 7.4c; core `f82e7d67` — the Linux commit, not the Windows
run's `0d2ebc3e`, because 7.4c is the gate with the whole evidence chain on the primary platform
and `gates/7.7-win11-tbc/source-identity.txt` already established that the one commit between
them touches `src/game` only), WotLK's two from `/home/pk/wowserver` on `yulon-ubuntu` (gate
7.1's clean 2026-09-04 run; `gate71-press2.log` prints `AzerothCore revision : 413bea61a85e+`
and names no commit for the module, so both were read off the box). `test_catalog.py` holds the
values (`GATE_PINS`) and, separately, the rule that no shipped source is unpinned.

**The patch.** `catalog/installers/shared/cmangos/patches/vmap-extractor-doodad-name-case.patch`:
§8's `ExtractSingleModel()` fix plus the counter §8 asked for "regardless" — `Doodad::ExtractSet()`
names each missing model once and the run's summary prints the total. Four files, five hunks,
generated by `git diff` from an edited copy of the pinned classic tree. §3 said `model.cpp` was
not compared; it differs between classic and TBC (a commented-out block above `ExtractSet`), which
is why the counter's definition lives in `vmapexport.cpp` beside the other globals and no hunk
anchors on that function's head. `git apply --check` passes on `mangos-classic` `8ec338a1` and
`mangos-tbc` `f82e7d67`, and refuses on `mangos-wotlk` `4cea3890` — whose `ExtractSingleModel()`
already normalises the name and which no shipped entry clones (`wow-wotlk` is AzerothCore).
Tortoise carries no patch: its extractor is the older lineage whose `wmo.cpp` runs `fixnamen()`
over the whole MODN block in place before writer or reader reads a name (`7c0fb278`,
`tools/vmap_extractor/vmapextract/wmo.cpp:98`), so the two agree there. That sentence was a
reading until 2026-09-05, and reading is how the same conclusion was reached about
`mangos-classic` before an extraction disproved half of it — so it was run. On `m910q` that
extractor was built from `7c0fb278` and run against the Turtle client (86 s, exit 0, 5,367 files
in `Buildings/`), and `extract.doodad_placements()` over the output answers
`DoodadCheck(extracted=4041, placed=2675, unplaced=1366, misspelt=0)`: no all-caps `.M2`, no name
with a space, not one file spelled a way the placement index would not ask for
(`pyplan/gates/doodad-2026-09-05/tortoise-doodadcheck.txt`).

**Verified by running, on `m910q`, 2026-09-05, 1.12.1 client, extractor built from `8ec338a1` in
an `ubuntu:22.04` container with the Vanilla Dockerfile's apt list.** Full extraction, not a
subset — it takes 32–35 s on that box, against the 83 minutes §8 quotes for the Windows gate over
9p, so nothing was left unshown:

| build | `Buildings/` files | `dir_bin` bytes | name occurrences (§7's count) | distinct | misspelt files | unplaced models |
|---|---|---|---|---|---|---|
| unpatched | 5,076 | 31,072,203 | 429,016 | 2,981 | 1,464 | 802 |
| patched | 3,913 | 36,639,894 | **503,782** | 3,349 | **0** | 434 |
| counter only (name fix reverted) | 5,076 | 31,072,203 | 429,016 | 2,981 | 1,464 | 802 |

The unpatched numbers are §7's Linux numbers to the byte, which holds the box and the build
constant against the gate. The counter-only build printed `WARNING: 74722 WMO doodad placements
were dropped because their model file was not found under ./Buildings` over 738 distinct
`Doodad model … is not in ./Buildings` lines; the patched build printed none and its summary
carried no warning. Against §7's Windows run: 503,782 placements to 503,722 and 3,349 distinct
names to 3,348 — the one extra is `razorfen_leanto03.m2`, 60 occurrences, the model §8 already
flagged for the space in its path; not investigated and not claimed. "Misspelt" is a model file
whose name is not a fixed point of `fixnamen`+`fixname2` (ported to `extract.reader_spelling()`);
"unplaced" is the case-folded set difference §10's option C described, and the 434 that remain
after the fix are what a correct extraction looks like — the `strings`-and-`comm` recipe in §6
counts them too, so **§10's "932 … a number that cannot be innocent" overstated it**: 802 on this
box, of which 434 are innocent.

**The stage.** `patch-sources`, second in the CMaNGOS family's tuple, bound to
`families/patch.py` — a unified-diff applier of our own rather than `git apply`, so "already
applied" is an answer and not a prompt, the outcome is the same on every platform, and a refusal
names the file and the line and writes nothing (every hunk of every file is resolved before the
first byte is written). Tolerant in two ways only: a hunk found at an offset from its stated
line applies; a hunk whose post-image is already present is skipped — and for a hunk that
removes nothing, the post-image is the question asked FIRST, at its hinted line (defect 2
below, and §12). The record in the state
file is NOT what skips it — a deleted checkout is re-cloned on disk evidence while the record
survives — so the body reads the files on every press. The catalog names the patch per entry
(`CmangosData.patches`, `SourcePatch{file, source, reason}`), and `CatalogEntry` refuses a
`source` that is not one of the entry's own dests.

**Option C**, as the gate. After the extraction tools, `_extract` reads `Buildings/` against
`dir_bin` and yields one line: a warning when any model file is misspelt (the defect's
fingerprint; 1,464 → 0 above), plain counts otherwise. Keyed on misspelt and not on unplaced,
for the reason in the table: 434 unplaced is a clean run, and a warning that fires on every
install teaches users to ignore warnings. A warning and not a refusal, because the install
is not wrong by shape — it boots and plays — and a refusal would take a working server away
over a defect only a rebuild mends; what the line is for is the day the patch silently stops
applying.

**Three defects the lane's own review found before it merged, and what they cost to fix**
(`pyplan/gates/doodad-2026-09-05/`):

1. *The report shipped a patch that does not apply.* The fenced ```` ```diff ```` in the issue
   text was an earlier revision of the patch file beside it — `vmapexport.cpp` anchored at
   `@@ -58,6 +58,7 @@ std::set<std::string> gameobjectFiles;`, against a declaration block that
   has moved — while the paragraph above it said "byte-for-byte". `git apply --check` on the
   fenced version exits 1 on `mangos-classic` `8ec338a1` AND on `mangos-tbc` `f82e7d67`, the two
   commits the issue itself names, while the shipped file exits 0 on both (`apply-check.txt`).
   A maintainer's first act on an issue is to apply the patch. The fence is now the shipped
   bytes and `test_the_issue_docs_fenced_diff_is_the_shipped_patch_byte_for_byte` holds them
   equal — through `read_text()`, which was the half of this that survived; see §12.

2. *An insertion-only hunk re-applied on every press.* `patch.apply()` asked "is the pre-image
   here?" first, and a hunk with no `-` lines has a pre-image of pure context that survives its
   own application — so an insertion at the head or the tail of that context applied again, and
   again (`insertion-only-presses.txt`: three presses, three copies of the inserted line). Every
   hunk this patch ships removes nothing; they escaped only because each `+` block happens to
   land mid-context. For a hunk with no removals the post-image is now asked about first — of
   the hinted line, which took a third pass to get right (§12).

3. *A resume of a pre-lane install patched a source nobody rebuilds.* Every CMaNGOS state file
   on `m910q` records twelve stages and not `patch-sources` (`state-files-m910q.txt`), so the
   first press after this merge ran the new stage and nothing else: it patched the checkout,
   then said `The server is already built; skipping the compile.`, then finished with "installed
   and running" — a source tree carrying the fix, an image without it, vmaps still short their
   14.8%, and not a word about it (`pre-lane-resume.txt`). `_patch_sources` now refuses when the
   patch would change a file AND `build_would_be_skipped()` — the spine's name for
   `stage_build`'s own record-AND-images rule — says the compile is not going to happen. A
   refusal rather than an invalidation, because invalidating the recorded build and extraction
   turns a resume into an unasked-for multi-hour recompile that rewrites `data/` under a running
   server, while refusing changes nothing on disk and leaves Start, Stop and Repair working. The
   price, stated: the install button stops working for that folder until the user acts on the
   sentence. What that sentence SAID was wrong, and is §12.

## 12. The third pass (2026-09-05, same lane): the remedy, the line endings, and the hint

A second review read the round above and sent it back. Two of its findings are the two halves of
one habit — a fix that is right, wearing a sentence nobody followed — and both were re-derived
here before anything was changed.

**BLOCKER: the refusal's remedy dead-ended at the database.** Defect 3's sentence ended "use
“Stop and remove containers…” on the Server tab, delete {server_dir}, and install it again".
Followed verbatim, on `m910q` 2026-09-05, driving `CmangosInstaller.run()` twice around it
against a state file in the real `~/tbc-7.4c` shape: press 1 refused; removing the containers
kept the database volume (`docker.remove_staged()` passes no `-v`, and its own armed warning says
so); deleting the folder took `.db_password` with it, because that file lives inside it;
`composegen.install_id()` is a digest of the ABSOLUTE path, so the reinstall came back to the
same volume name (`ffb3ef7e` before and after the `rmtree`); and press 2 stopped at
`_db_password` — "*that database cannot be opened again: `docker volume rm …` deletes it, and
every character in it*". `_db_password`'s own docstring had refused to send anyone down that
road four days earlier, for exactly this reason.

**The remedy now names the two things this press would skip, and keeps everything else.** Stop
and remove the containers, `docker image rm <this install's image>`, delete
`<server_dir>/data/.yulon-extract.json`, install again. Removing the image turns
`build_would_be_skipped()` False so the compile runs; removing the evidence file is what makes
the fix visible, since `extract.run_plan()` skips a tool that has a record and `run_mmaps()`
reads its own record out of that same file — one deletion re-runs the extraction (each `produces`
folder emptied first) and the movement maps built from it. Measured by FOLLOWING THE SENTENCE:
the test parses the image reference and the file path out of the message, does those two things
and nothing else, and presses again — the compile runs, the four extraction tools and MoveMapGen
run, the checkout ends byte-identical to the patched fixture, the install finishes "installed and
running", `.db_password` is unchanged, the volume set is unchanged, and the import stage says
"They are already imported; leaving them alone." The control beside it removes ONLY the image:
the compile runs, and the maps are skipped with three `already extracted` lines — which is why
the sentence names both.

**What this costs, and the recommendation the owner asked for.** The owner said "do what you
recommend". The recommendation is the refusal as it now stands, and the argument is the price:
the old sentence cost a world (a reinstall into the same folder cannot open the old volume, and
the only way past it deletes every character), while the new one costs a recompile plus an
extraction — hours on the box, nothing on disk that anyone made. It is also proportionate to
where the refusal fires: stage 2 of 13, on a folder whose ONLY missing stage is `patch-sources`.
The alternative the round above rejected (invalidate the record and rebuild automatically) is
still rejected, and now for a second reason: the user pressing Install is not asking for four
hours of compiling, and the remedy is the same work done deliberately. What is NOT offered is a
way to keep the old maps and skip the patch: there is no override, and adding one is a design
question this lane did not open.

**The upstream draft's patch is refused by `git apply` — as a CRLF file, on every tree.** The
second pass fixed the fence's CONTENT and asserted it through `read_text()`, which translates
line endings; so the assertion went on holding while this repository's Windows checkout held the
doc as 13,780 CRLF bytes against the patch's 3,943 LF ones. The fence extracted from that copy
exits 1 on all four trees measured — `mangos-classic 8ec338a1`, `mangos-tbc f82e7d67`, and each
clone's newest `origin/master` (`9b682be6`, `46d9a78d`) — with `error: patch failed:
contrib/vmap_extractor/vmapextract/gameobject_extract.cpp:24`, while the LF form exits 0 on all
four (`fence-eol-apply-check.txt`). The committed blob was always LF, so what GitHub serves has
always applied; the copy in front of the person who posts it had not. The doc is now pinned
`text eol=lf` in `.gitattributes` — the pin its neighbour `*.patch` has carried since the second
pass, for the same reason spelled out beside it — the equality is asserted on BYTES, and a second
test asserts both halves: that the pin is declared, and that the checkout it is reading actually
arrived without a CR in it. Recommendation: the draft is postable now; nothing in this lane posts
it.

**And the order fix of defect 2 had a defect of its own, found the same day.** It asked `_find` for
the post-image, and `_find` searches the whole file once the hint misses — so a post-image
occurring anywhere beat a pre-image sitting exactly at the line the patch named, and that site
was reported as "already carries the fix" and never touched (measured against
`git show HEAD:…patch.py` and the fixed module: `int a;/int b;/int c;/ZZZ/int a;/int b;/int c;/
int d;` → `(0, 1)` and unchanged, against `(1, 0)` and patched). It voided `_find`'s own written
guarantee for exactly the hunk class this patch is made of. The question is now asked AT THE
HINT, with the whole-file question kept one branch lower, after the hinted line has been ruled
out as an unpatched site — which is what keeps an already-applied insertion at an OFFSET from
doubling. That second branch survived its first mutation (`if False:` left the suite green),
because no shipped hunk and no fixture had the shape that needs it: four of the five are applied
AT their hint and the fifth inserts mid-context, which breaks its own pre-image up. It has a test
of its own now, and the mutation kills it.

**The report** is `pyplan/upstream-cmangos-doodad-issue.md`, not posted.

**Not done, said plainly.** No existing Linux install was retro-fitted (§10 cost 3), and after
defect 3 above an existing install is not quietly half-retro-fitted either: it is refused, with
a remedy in the sentence that keeps its database. No gate
re-ran a whole Vanilla or TBC install through the new stage on a VM — the stage was proved on
the pinned trees' bytes through the Python applier, and the extractor's behaviour was proved
on `m910q` with the same patch through `git apply`; the two were shown to produce identical
bytes (`test_patch.py`), which is the join. The first full install after this merge is where
the stage's `Patched …` lines and the extraction's counts line should be read.
