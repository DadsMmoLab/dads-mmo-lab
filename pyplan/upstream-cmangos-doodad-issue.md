# Upstream issue text: vmap_extractor drops WMO doodad placements on case-sensitive filesystems

Drafted 2026-09-05 by lane `doodad` from `pyplan/upstream-cmangos-doodad-drop.md` §1–§9, for the
owner to post to `cmangos/mangos-classic` (and to `cmangos/mangos-tbc`, which carries the same
code). **Not posted by this project.** Everything between the markers is the issue body; the
patch inline is byte-for-byte
`pylauncher/catalog/installers/shared/cmangos/patches/vmap-extractor-doodad-name-case.patch`,
which `test_the_issue_docs_fenced_diff_is_the_shipped_patch_byte_for_byte` now asserts rather
than promises. On the day this page was first drafted it was not: the fence held an earlier
revision of the patch, anchored at `@@ -58,6 +58,7 @@ std::set<std::string> gameobjectFiles;`
against a declaration block that has since moved, and `git apply --check` refused it on BOTH of
the commits this page names while accepting the shipped file (measured on m910q, 2026-09-05;
`pyplan/gates/doodad-2026-09-05/apply-check.txt`).

It was refused a second time, for a second reason, until later that day. The equality was
asserted through `read_text()`, so it went on holding while this file sat on a Windows checkout
with CRLF line endings, where the FENCE came to 4,017 bytes against the shipped patch's 3,943 LF
ones (the document around it was 13,780 bytes on that checkout and 13,534 after the pin — a
different pair of numbers, and the review of 2026-09-05 was right that setting one against the
other reads as a mismatch of one object) — and `git apply --check` on the fence
extracted from THAT copy exits 1, `error: patch failed:
contrib/vmap_extractor/vmapextract/gameobject_extract.cpp:24`, on the two pinned commits and on
both trees' current `origin/master` (`9b682be6`, `46d9a78d`), where the LF form exits 0 on all
four (`pyplan/gates/doodad-2026-09-05/fence-eol-apply-check.txt`). The committed blob was always
LF, so what GitHub serves has always applied; the copy on the poster's own disk had not. This
file is now pinned `text eol=lf` in `.gitattributes`, and the equality is asserted on bytes.

Suggested title: **vmap_extractor: WMO doodad placements are silently dropped on case-sensitive
filesystems (writer/reader disagree about the model file's case)**

START COPY

## Summary

`contrib/vmap_extractor` writes each WMO's interior doodad models to `Buildings/` under the
**raw** name found in the WMO's `MODN` chunk (e.g. `INNBED.M2`), and then, when writing that
doodad's placement into `Buildings/dir_bin`, looks the same model up under the
**case-normalised** name (`Innbed.m2`). On NTFS and case-insensitive APFS the lookup resolves
anyway. On ext4 it misses, `Doodad::ExtractSet()` does `if (!input) continue;`, and the
placement is discarded with no message. The run ends `Work complete. No errors.`

Measured on WoW 1.12.1 with `mangos-classic` at `8ec338a1` (also reproduced on `mangos-tbc`
`f82e7d67`, whose `wmo.cpp` MODN handler and `ExtractSingleModel()` are byte-identical):

| | ext4 (Linux) | NTFS (Windows) |
|---|---|---|
| `Buildings/dir_bin` size | 31,072,203 B | 36,635,438 B |
| model-name occurrences in it (≈ placements) | **429,016** | **503,722** |
| distinct model names in it | 2,981 | 3,348 |

**74,706 placements — 14.8% — dropped on Linux.** With the patch below, the same Linux box
writes 503,782 (measured 2026-09-05; one more model than Windows, `Razorfen_Leanto03.m2`, see
"Adjacent" at the end).

## What is affected

Nothing the client draws, and nothing in client-side collision. The loss is entirely in the
server-side `vmaps` geometry and confined to **M2 models placed inside WMOs** — building and
dungeon interiors. Terrain, WMO shells and ADT-placed (`MDDF`) doodads are complete, because the
ADT path applies the name fix in the right order (see below).

Consequences, in decreasing order of confidence:

* **Line of sight** (`IsInLineOfSight()` answers from `vmaps`): props that should block a spell or
  an aggro check do not. Of the 296 models that lost *every* placement, the list reads like a
  dungeon furniture manifest — `Scholme_Bookshelf`, `Uldamantable01`, `Wc_Cairn`,
  `Blackrockbloodmachine01`–`04`, `Elfbed01`–`03`, braziers and candelabras by the dozen, and
  `Auctioneercollision.m2`, which exists for nothing but collision.
* **Pathing**: `contrib/mmap/src/TerrainBuilder.cpp` builds the navmesh from `vmaps`, so the
  same props are missing from `mmaps` and creatures path through them.
* **`GetHeight()`** also consults `vmaps`; no specific in-game consequence was measured.

## The defect

`vmapexport.h:53` states `ExtractSingleModel()`'s precondition:

```c
/* @param origPath = original path of the model, cleaned with fixnamen and fixname2
 * @param fixedName = will store the translated name (if changed)
 * @param failedPaths = Set to collect errors
 */
bool ExtractSingleModel(std::string& origPath, std::string& fixedName, StringSet& failedPaths);
```

`adtfile.cpp:148-156` (MMDX) obeys it — `fixnamen`/`fixname2` first, *then* `string path(p);
// Store copy after name fixed`. `wmo.cpp:85-105` (MODN) takes the copy one line too early:

```c
std::string path = ptr;                  // copy taken BEFORE the fix
char* s = GetPlainName(ptr);
fixnamen(s, strlen(s));                  // fixes the buffer; `path` still holds the raw name
fixname2(s, strlen(s));
...
if (ExtractSingleModel(path, fixedName, failedPaths))   // raw path passed
```

`ExtractSingleModel()` (`gameobject_extract.cpp:9-33`) names the output file from what it was
given — `fixedName = GetPlainName(origPath.c_str());` → `Buildings/INNBED.M2`. The reader,
`Doodad::ExtractSet()` (`model.cpp:223-260`), re-derives the name from the same raw MODN bytes,
*applies* `fixnamen`/`fixname2` — `Innbed.m2` — opens that, and on `!input` silently `continue`s.

The writer writes `INNBED.M2` and the reader asks for `Innbed.m2`.

## Minimal reproduction (one Linux box, no comparison run needed)

```sh
# build contrib/vmap_extractor as usual; run it against a 1.12.1 client on ext4; then:
cd Buildings
ls INNBED.M2                                          # present, 840 bytes
ls Innbed.m2                                          # No such file  <- the reader's spelling
strings -a dir_bin | grep -o -i 'innbed\.m2' | wc -l  # 0: placed nowhere under either spelling

# every model that was extracted and placed nowhere, in one pass:
strings -a dir_bin | tr 'A-Z' 'a-z' | grep -o '[a-z0-9_.-]*\.m2' | sort -u > /tmp/placed
ls *.M2 | tr 'A-Z' 'a-z' | sort -u > /tmp/extracted
comm -23 /tmp/extracted /tmp/placed | wc -l
```

## Why it is silent

Two silences, either of which alone would have made this visible: the `continue` prints and
counts nothing (74,706 drops produced zero output lines), and the existing "some models could
not be extracted" warning does not fire, because from its point of view nothing failed — the
model file *was* written, under a name nobody later asked for.

## Proposed fix

The smallest change is in `ExtractSingleModel()`: spell the output file the way the reader will
ask for it. This repairs all three call sites at once and leaves `origPath` — the path used for
the MPQ read — untouched. Both functions are idempotent (`fixnamen` title-cases alphabetic runs
and lower-cases the extension; `fixname2` replaces spaces), so the ADT and GameObjectDisplayInfo
callers, which already pass a cleaned name, see no change. **Verified by running**, not only by
reading: with the patch, the Linux extraction produced 3,913 unique-spelling files (no
`INNBED.M2`/`Innbed.m2` twins) and 503,782 placements, and the added counter reported 0 drops.

The patch also makes the drop audible: `Doodad::ExtractSet()` names each missing model once and
the run's summary prints the total. Built with only the counter (name fix reverted), the same
extraction reported `WARNING: 74722 WMO doodad placements were dropped because their model file
was not found under ./Buildings` over 738 distinct models — which is the report this issue would
have been from the first Linux run.

The alternative — moving `std::string path = ptr;` in `wmo.cpp` below the `fixnamen`/`fixname2`
pair, as `adtfile.cpp:153` does — is one line, but it also changes the path handed to `Model
mdl(origPath)` for the archive read, and `fixname2()` turns spaces into underscores. At least one
1.12.1 model already fails its ADT-path lookup in a way consistent with that (`Could not find
file of model World\Generic\Quilboar\Passive Doodads\Leantos\Razorfen_Leanto03.m2`), so we did
not widen that code path to WMO doodads.

```diff
diff --git a/contrib/vmap_extractor/vmapextract/gameobject_extract.cpp b/contrib/vmap_extractor/vmapextract/gameobject_extract.cpp
index 41bb38249..b8b4b8b0a 100644
--- a/contrib/vmap_extractor/vmapextract/gameobject_extract.cpp
+++ b/contrib/vmap_extractor/vmapextract/gameobject_extract.cpp
@@ -24,6 +24,15 @@ bool ExtractSingleModel(std::string& origPath, std::string& fixedName, StringSet
     // nothing do
 
     fixedName = GetPlainName(origPath.c_str());
+    // Spell the output file the way Doodad::ExtractSet() (model.cpp) will later
+    // ask for it. wmo.cpp's MODN handler hands over the RAW path from the chunk,
+    // so without this a WMO doodad is written as INNBED.M2 and looked up as
+    // Innbed.m2 -- a miss on every case-sensitive filesystem, and the placement
+    // is dropped without a word. Both functions are idempotent, so the ADT and
+    // GameObjectDisplayInfo callers, which already pass a cleaned name, see no
+    // change.
+    fixnamen(&fixedName[0], fixedName.length());
+    fixname2(&fixedName[0], fixedName.length());
 
     std::string output(szWorkDirWmo);                       // Stores output filename (possible changed)
     output += "/";
diff --git a/contrib/vmap_extractor/vmapextract/model.cpp b/contrib/vmap_extractor/vmapextract/model.cpp
index a1d110b01..24d194418 100644
--- a/contrib/vmap_extractor/vmapextract/model.cpp
+++ b/contrib/vmap_extractor/vmapextract/model.cpp
@@ -257,7 +257,16 @@ void Doodad::ExtractSet(WMODoodadData const& doodadData, ADT::MODF const& wmo, u
         sprintf(tempname, "%s/%s", szWorkDirWmo, ModelInstName);
         FILE* input = fopen(tempname, "r+b");
         if (!input)
+        {
+            // The placement is lost when the model is not where the writer put
+            // it. Say so once per model and count every drop: a run that lost
+            // 74,706 placements used to end with "Work complete. No errors."
+            static std::set<std::string> missing;
+            ++DroppedDoodadPlacements;
+            if (missing.insert(ModelInstName).second)
+                printf("Doodad model %s is not in %s; its placements are being dropped\n", ModelInstName, szWorkDirWmo);
             continue;
+        }
 
         fseek(input, 8, SEEK_SET); // get the correct no of vertices
         int nVertices;
diff --git a/contrib/vmap_extractor/vmapextract/vmapexport.cpp b/contrib/vmap_extractor/vmapextract/vmapexport.cpp
index 84ca525e2..299e7f64c 100644
--- a/contrib/vmap_extractor/vmapextract/vmapexport.cpp
+++ b/contrib/vmap_extractor/vmapextract/vmapexport.cpp
@@ -72,6 +72,7 @@ bool hasInputPathParam = false;
 bool hasOutputPathParam = false;
 bool preciseVectorData = false;
 std::unordered_map<std::string, WMODoodadData> WmoDoodads;
+uint32 DroppedDoodadPlacements = 0;
 
 // Constants
 
@@ -549,6 +550,8 @@ int main(int argc, char** argv)
         getchar();
     }
 
+    if (DroppedDoodadPlacements)
+        printf("WARNING: %u WMO doodad placements were dropped because their model file was not found under %s (the models are named above).\n", DroppedDoodadPlacements, szWorkDirWmo);
     printf("Extract for %s. Work complete. No errors.\n", szRawVMAPMagic);
     delete [] LiqType;
     return 0;
diff --git a/contrib/vmap_extractor/vmapextract/vmapexport.h b/contrib/vmap_extractor/vmapextract/vmapexport.h
index 815d9160e..26549e5af 100644
--- a/contrib/vmap_extractor/vmapextract/vmapexport.h
+++ b/contrib/vmap_extractor/vmapextract/vmapexport.h
@@ -42,6 +42,7 @@ const int path_l = 1024;
 extern char szWorkDirWmo[path_l + 512];
 extern const char* szRawVMAPMagic;                          // vmap magic string for extracted raw vmap data
 extern std::unordered_map<std::string, WMODoodadData> WmoDoodads;
+extern uint32 DroppedDoodadPlacements;                     // Doodad::ExtractSet() placements whose model file was missing
 
 uint32 GenerateUniqueObjectId(uint32 clientId, uint16 clientDoodadId);
 
```

## What the fix costs someone who re-extracts

* On a case-insensitive filesystem it changes nothing.
* The on-disk names change (`INNBED.M2` → `Innbed.m2`), and `ExtractSingleModel()`'s
  `FileExists()` early return keys on the new name — so re-running into an existing `Buildings/`
  re-extracts those models and leaves the old all-caps files behind. `dir_bin` and the model
  files must come from the same run: delete `Buildings/`, re-run `vmap_extractor`, then
  `vmap_assembler`, then the mmaps generator (its navmesh is built from `vmaps`).
* The payoff is invisible in a file listing: after the fix `Buildings/` gets *smaller* (5,076 →
  3,913 files, no duplicate spellings) while `dir_bin` gets *larger*. Count placements in
  `dir_bin`, not files.

## How it was found

A Windows and a Linux gate installed the same server and the `Buildings/` file counts disagreed:
5,076 on Linux, 3,913 on Windows. Diffing the sorted listings: 1,163 names on Linux and not
Windows, 0 the other way; all 1,163 are `.m2` and every one has a case-insensitive twin on
Windows (md5 and size identical for all 1,163 pairs). So the file count was a red herring and
the real comparison was `dir_bin`. Of the 367 model names present in the Windows index and not
the Linux one, 296 exist on Linux *only* in all-caps, 71 in both spellings (an ADT elsewhere
happened to write the fixed-name copy; order-dependent), and 0 in neither. All 20 files under
`Data/` and `wmo.MPQ` hashed identically on both boxes; the source commit was read out of each
box's own clone.

## Adjacent, not part of this report

* `model.cpp:251-252`: the `.mdx` → `.m2` shortening writes a terminating `'\0'` into
  `ModelInstName` without decrementing `nlen`, and `nlen` is what the record declares and
  writes. Not tested; not claimed to be a bug.
* With the fix, the Linux index gains one model the Windows one lacks:
  `Razorfen_Leanto03.m2` (60 occurrences) — the model whose plain name carries a space and
  whose ADT-path lookup fails on both platforms. Not investigated.

END COPY
