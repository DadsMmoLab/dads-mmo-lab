# TBC extraction: native Windows vs Linux — the finished comparison

Measured 2026-09-04. Windows: `yulon-win11-gate`, `C:\gate\tbc-server\data`, produced by the
scheduled task `\dml-tbc-install`. Linux: `m910q`, `/home/pk/tbc-7.4c/data`, the 7.4c run.
Every count below was taken by walking the directory on the machine that owns it, not read out
of a progress file or quoted from an earlier note:

```
win:  Get-ChildItem -Path C:\gate\tbc-server\data\<dir> -Recurse -File -Name   # then counted
lin:  cd ~/tbc-7.4c/data/<dir> && find . -type f -printf '%P\n'                # then counted
```

The `mmaps` stage finished. When the counts were taken the install had reached
`Step 12 of 12 (100%): ready` and was waiting on the world server, so every extraction output
below is final.

## 1. The counts

| folder      | Windows | Linux | equal? | Windows − Linux |
|-------------|--------:|------:|:------:|----------------:|
| `dbc`       |     185 |   185 | equal  |               0 |
| `maps`      |   3 586 | 3 586 | equal  |               0 |
| `Cameras`   |      13 |    13 | equal  |               0 |
| `Buildings` |   5 431 | 7 171 | **no** |         −1 740 |
| `vmaps`     |   8 607 | 8 099 | **no** |         **+508** |
| `mmaps`     |   2 820 | 2 819 | **no** |             **+1** |

`dbc`, `maps` and `Cameras` matched as *sets*, not merely as totals: diffing the two name lists
gave 0 only-Windows and 0 only-Linux names in each.

The two known inequalities held. `mmaps` finished at 2 820 against Linux's 2 819 — so the
finished mmaps **agree with** the earlier finding's direction: Windows produced more, Linux is
the side that lost data. It disagrees with nothing.

## 2. `Buildings` −1 740: case folding, and it cost nothing

| kind                       | Windows | Linux |
|----------------------------|--------:|------:|
| `.m2`, mixed case          |   2 181 | 3 921 |
| `.M2`, ALL-CAPS            |   2 044 | 2 044 |
| `.wmo`                     |   1 204 | 1 204 |
| `dir_bin`, `temp_gameobject_models` | 2 | 2 |

Case-folded to lower case, both directories held **5 431 unique names** — the Windows file count,
to the file. The Linux listing contained 1 740 case-fold collision groups, every one of size
exactly 2, each an ALL-CAPS `.M2` beside a mixed-case `.m2`
(`ABBEYSHELF01.M2` / `Abbeyshelf01.m2`, `AHN_QIRAJ_DOORPLUG.M2` / `Ahn_Qiraj_Doorplug.m2`).
The Windows listing had **zero** collision groups and zero names absent from Linux. The 5 431
figure was predicted before the run and then confirmed to the file.

Why two spellings existed on Linux at all is section 4.

## 3. `vmaps` +508: the difference is one kind of file

The 8 607 / 8 099 directories were sorted by what actually produced each file:

| file kind                | Windows | Linux | delta |
|--------------------------|--------:|------:|------:|
| `*.vmtile`               |   1 790 | 1 790 |     0 |
| `*.vmtree`               |      72 |    72 |     0 |
| `*.wmo.vmo`              |   1 028 | 1 028 |     0 |
| `*.m2.vmo`               |   3 921 | 3 921 |     0 |
| `temp_gameobject_models` |       1 |     1 |     0 |
| bare `*.m2`              | **1 795** | **1 287** | **+508** |

The `.vmo` sets were not merely equal in count — diffed by name they were identical, 0 only-Windows
and 0 only-Linux, in both the `.m2.vmo` and `.wmo.vmo` families. `temp_gameobject_models` parsed to
**1 508 entries on both** machines in `Buildings/` (41 308 bytes each, identical) and 1 508 again in
`vmaps/` (77 508 bytes each), so `TileAssembler::exportGameobjectModels()` found every game-object
model it looked for on *both* platforms. The whole `vmaps` difference sat in the bare `.m2` files.

## 4. The mechanism, from the source and from the bytes

### 4a. Two spellings, written by two code paths

`contrib/vmap_extractor/vmapextract/wmo.cpp`, the `MODN` handler, takes the copy **before** it
fixes the name:

```cpp
std::string path = ptr;              // copy of the RAW MPQ path
char* s = GetPlainName(ptr);
fixnamen(s, strlen(s));              // mutates ptr's buffer, not `path`
fixname2(s, strlen(s));
...
if (ExtractSingleModel(path, fixedName, failedPaths))   // the UNFIXED copy is passed
```

`ExtractSingleModel()` then derives its output name from what it was handed:

```cpp
std::string extension = GetExtension(GetPlainName(origPath.c_str()));
if (extension == ".mdx" || extension == ".MDX" || ...)
    { origPath.erase(origPath.length() - 2, 2); origPath.append("2"); }
fixedName = GetPlainName(origPath.c_str());
std::string output = std::string(szWorkDirWmo) + "/" + fixedName;
if (FileExists(output.c_str())) return true;
```

Given `...\ABBEYSHELF01.MDX` it writes `Buildings/ABBEYSHELF01.M2` — ALL-CAPS. That is where the
2 044 ALL-CAPS `.M2` files came from, and the count was identical on both machines.

`adtfile.cpp`'s `MMDX` handler calls `fixnamen(p, strlen(p))` on the buffer *first* and copies
after, so the same model reached `ExtractSingleModel()` as `Abbeyshelf01.m2` — mixed case. On
Linux that second name was a different file and got written; on Windows `FileExists()` matched the
ALL-CAPS file case-insensitively and the write was skipped. Hence Linux's extra 1 740 files.

### 4b. Where the placements were lost

`Doodad::ExtractSet()` in `model.cpp` — the WMO-interior doodads — builds the lookup name with
`fixnamen`, i.e. the **mixed-case** spelling, and then requires the file to open:

```cpp
sprintf(ModelInstName, "%s", GetPlainName(&doodadData.Paths[doodad.NameIndex]));
uint32 nlen = strlen(ModelInstName);
fixnamen(ModelInstName, nlen);
fixname2(ModelInstName, nlen);
...
sprintf(tempname, "%s/%s", szWorkDirWmo, ModelInstName);
FILE* input = fopen(tempname, "r+b");
if (!input)
    continue;                        // the placement is dropped, silently
```

The model on disk was written ALL-CAPS by 4a. On NTFS `fopen()` found it anyway. On ext4 it found
it only when an ADT reference had independently created the mixed-case twin, and then only for the
WMO placements processed *after* that ADT — which is why the loss is partial and per-name rather
than all-or-nothing.

### 4c. The bytes agree, and they isolate the loss exactly

`Buildings/dir_bin` was parsed record by record on both machines
(`mapID, tileX, tileY, flags, adtId, ID, pos[3], rot[3], scale, [bound[6] if flags&4], nlen, name`).
Both files consumed to zero bytes leftover, so the record layout is right:

| | Windows | Linux |
|---|---:|---:|
| `dir_bin` size | 58 298 617 B | 50 938 121 B |
| placements | **798 812** | **699 970** |
| distinct raw names | 4 679 | 4 263 |

Gap: **98 842 placements (+14.1%)**, Linux the losing side. Zero names and zero placements existed
on Linux but not on Windows.

The decisive split came from a quirk that turns out to be a free label. `Doodad::ExtractSet()`
computes `nlen` from the `.mdx` name and only afterwards shortens the extension to `.m2`, then
writes `nlen` bytes — so **every WMO-interior placement carries a trailing NUL inside its name
field**, and every ADT placement does not. Splitting `dir_bin` on that:

| name form | Windows names | Windows placements | Linux names | Linux placements |
|---|---:|---:|---:|---:|
| plain (ADT doodads + WMO spawns) | 4 263 | **386 324** | 4 263 | **386 324** |
| NUL-terminated (`Doodad::ExtractSet`) | 1 795 | 412 488 | 1 307 | 313 646 |

**The ADT half is equal to the placement on both platforms — 4 263 names, 386 324 placements.
All 98 842 lost placements are inside the WMO-doodad half**, which is exactly the code path in 4b.
That is the strongest form of the claim available here: not "the numbers differ and here is a
plausible cause", but "the half of the file that does not use the broken lookup is byte-for-byte
equal".

Merging the two name forms, the loss decomposed as:

* **416 model names present in the Windows `dir_bin` and absent from the Linux one — 51 520
  placements.** Of those, 298 exist in the Linux `Buildings/` **only** as ALL-CAPS, and 118 exist
  there in mixed case too (their placements were all processed before the twin was created).
* **549 names present on both, with fewer placements on Linux — 47 322 placements.** Worst cases:
  `Diremaulfloorrubble02.m2` 2 655 → 13, `Diremaultrimrubble03.m2` 2 665 → 121,
  `Stalagtite01.m2` 2 266 → 190, `Undead_Torch02.m2` 2 760 → 1 360, `Abbeyshelf01.m2` 1 734 → 388.
* **0 names with more placements on Linux.** 51 520 + 47 322 = 98 842, the whole gap.

### 4d. The bare `.m2` files in `vmaps` are the same NUL, read by the assembler

`TileAssembler::convertRawFile()` writes

```cpp
return model.writeFile(iDestDir + "/" + pModelFilename + ".vmo");
```

When `pModelFilename` came from a NUL-terminated `dir_bin` name, `.c_str()` truncated the path at
the embedded NUL and the `.vmo` suffix never reached `fopen()` — so the file landed as
`vmaps/Abbeyshelf01.m2` instead of `vmaps/Abbeyshelf01.m2.vmo`. Checked rather than assumed: on
Windows the set of bare `.m2` files in `vmaps` was **exactly** the set of NUL-form `dir_bin` names,
1 795 = 1 795, with zero on either side of the diff. Two such files sampled on each machine were
byte-identical to the corresponding `.m2.vmo` (`Durotarrock07.m2`, md5
`99b617b29dce826caa8825d10773685a`, 5 760 B, mtime equal to the nanosecond; `Amethystcrystal03.m2`
on Windows, md5 `F4814861A15ACFBFEE4EC58177DB7E75`, 7 228 B).

So the `vmaps` +508 is not a second defect. 488 of it is the 488 NUL-form names Windows had and
Linux did not — the same lost WMO doodads — one file each. The remaining 20 are in section 6.

### 4e. The single extra mmtile

The one `mmaps` difference was `2303233.mmtile` (map 230, Blackrock Depths, tile 32/33), present
on Windows and absent on Linux; map 230 had 8 mmtiles on Windows and 7 on Linux, and every other
tile matched. Map 230 is a WMO-only instance: all its `dir_bin` records sit in the single global
spawn 65/65, **1 544 placements on Windows against 1 306 on Linux**. The extra interior doodad
geometry is enough collision for `MoveMapGen` to mesh one more tile. Stated as the measurement it
is: the placement surplus and the extra tile are on the same map and the same stage; the causal
step from "238 more doodads" to "that specific tile" was not separately proven.

## 5. Direction, and the earlier figure

The comparison confirms the earlier finding's direction: **Linux loses placements, Windows does
not.** The number differs from 74 706 because 74 706 was the *Vanilla 1.12.1* lane
(503 722 vs 429 016, `pyplan/gates/7.7-win11-gate/buildings-shortfall-measurements.txt`). The TBC
lane's own number, measured here, is **98 842 (798 812 vs 699 970)**. Same mechanism, bigger
client, larger loss. Nothing was re-measured that contradicted the Vanilla figure.

Windows lost nothing that Linux had: 0 only-Linux names in `dir_bin`, 0 only-Linux names in
`vmaps`, and 0 only-Windows names in `Buildings`. Where Windows has fewer *files* — `Buildings` —
the case-folded name sets are equal at 5 431, so no unique model is missing.

## 6. Still unexplained

* **20 bare `.m2` files.** Linux `dir_bin` held 1 307 NUL-form names but `vmaps` held only 1 287
  bare `.m2` files; Windows's two sets matched exactly. The 20 are `Be_Fountain01.m2`,
  `Be_Loom_01.m2`, `Be_Statueghostlands01.m2`, `Dr_Exodarwall01.m2`, `Dr_Exodarwall02.m2`,
  `Dr_Fountian_Ruined.m2`, `Dr_Signpost_01.m2`, `Durotarbush03.m2`, `Durotarbush04.m2`,
  `Gnomescrew08.m2`, `Goblinrocketcart01.m2`, `Silvermystcrystalbig01.m2`,
  `Silvermystcrystalbig02.m2`, `Smallfirepit01.m2`, `Stormwindvendortent01.m2`, `Taurentotem08.m2`,
  `Terokkartreesmall.m2`, `Terokkartreestump.m2`, `Zangarbushwithered01.m2`,
  `Zangarbushwithered02.m2`. What was ruled out: they are not an aborted conversion loop (their
  positions in the sorted name set are scattered, 145 to 1 288 of 1 307, not a tail); all 20 exist
  in the Linux `Buildings/` in both spellings; all 20 appear in the Linux `dir_bin` in the plain
  form as well as the NUL form, and their `.m2.vmo` was written. Why the second, NUL-form
  conversion produced no file on Linux was not determined.
* **The 118.** Of the 416 names missing from the Linux `dir_bin`, 118 do have a mixed-case file in
  the Linux `Buildings/`. The ordering explanation — every one of their WMO placements was
  processed before the ADT that created the twin — fits and nothing contradicts it, but the WMO
  processing order was not reconstructed to prove it.
* **Whether any of this is visible in play.** Nothing here was tested against a running server.
  The measurement is of files and placement records only.

## 7. Commands, for anyone re-running it

```
# counts
ssh yulon-win11-gate 'powershell -NoProfile -Command "Get-ChildItem -Path '\''C:\gate\tbc-server\data\<dir>'\'' -Recurse -File -Name"'
ssh m910q 'cd ~/tbc-7.4c/data/<dir> && find . -type f -printf "%P\n"'

# dir_bin, parsed record by record (script: dirbin.py, layout in section 4c)
ssh m910q            'python3 /tmp/dirbin.py ~/tbc-7.4c/data/Buildings/dir_bin'
ssh yulon-win11-gate 'powershell -NoProfile -Command "& \"C:\Program Files\Python312\python.exe\" C:\gate\dirbin.py C:\gate\tbc-server\data\Buildings\dir_bin"'

# temp_gameobject_models, parsed as displayId/len/name (+24 B of bounds in the vmaps copy)
```

Source read on `m910q`, `~/tbc-7.4c/src/mangos-tbc`:
`contrib/vmap_extractor/vmapextract/wmo.cpp` (MODN), `.../gameobject_extract.cpp`
(`ExtractSingleModel`), `.../adtfile.cpp` (`fixnamen`, MMDX), `.../model.cpp`
(`ModelInstance::ModelInstance`, `Doodad::ExtractSet`), `src/game/vmap/TileAssembler.cpp`
(`convertWorld2`, `convertRawFile`, `exportGameobjectModels`).
