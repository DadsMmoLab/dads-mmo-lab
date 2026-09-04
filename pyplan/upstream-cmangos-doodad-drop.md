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
