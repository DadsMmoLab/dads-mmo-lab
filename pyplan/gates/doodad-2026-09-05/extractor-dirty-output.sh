#!/bin/bash
# What every claim in `extract.DIRTY_MARKERS`, `DIRTY_OUTPUT_TOOL` and the drop doc's
# "three folders are NOT named" paragraph was read from, and the fence's apply-check
# re-run for this pass. m910q, 2026-09-05. Every line below is a command's own output.
set -u
R=~/yulon-runs/fix8-doodad
CL=~/cmangos-probe/mangos-classic
TB=~/cmangos-probe/mangos-tbc
TO=~/tortoise-server/src/tortoise-wow

echo "== date / box"
date -u '+%Y-%m-%dT%H:%M:%SZ  '"$(hostname)"
echo "lane checkout: $(cd $R && git rev-parse HEAD)  branch $(cd $R && git rev-parse --abbrev-ref HEAD)"

echo
echo "== the revisions catalog.json pins"
"$R"/pylauncher/.venv/bin/python - <<'PY'
import json
d = json.load(open("/home/pk/yulon-runs/fix8-doodad/pylauncher/yulon/catalog/catalog.json"))
for game in d["games"]:
    for src in game.get("emulator", {}).get("sources", []):
        print(f'  {game["id"]:14s} {src["repo"]:28s} {src.get("rev","-")}')
PY

echo
echo "== 1. the dirty-output check: cmangos/mangos-classic 8ec338a1"
(cd $CL && git show 8ec338a1:contrib/vmap_extractor/vmapextract/vmapexport.cpp | sed -n '465,484p')
echo
echo "== 1b. the same eleven lines: cmangos/mangos-tbc f82e7d67"
(cd $TB && git show f82e7d67:contrib/vmap_extractor/vmapextract/vmapexport.cpp | sed -n '515,534p')

echo
echo "== 2. no other shipped tool carries such a check (both revisions, empty = none)"
for pair in "$CL 8ec338a1" "$TB f82e7d67"; do
  set -- $pair
  echo "-- $1 $2"
  (cd "$1" && git grep -n -i -E 'polluted|dirty|empty directory' "$2" -- contrib/mmap contrib/extractor contrib/vmap_assembler; echo "   exit $?")
done

echo
echo "== 3. what ad, vmap_assembler and TileAssembler do with an output folder (mangos-tbc f82e7d67)"
(cd $TB && git show f82e7d67:contrib/extractor/System.cpp | sed -n '96,110p')
echo "-- vmap_assembler main()"
(cd $TB && git show f82e7d67:contrib/vmap_assembler/vmap_assembler.cpp | sed -n '24,50p')
echo "-- TileAssembler: every output opened for writing, none stat()ed first"
(cd $TB && git show f82e7d67:src/game/vmap/TileAssembler.cpp | grep -n 'iDestDir\|mkdir\|stat(' )

echo
echo "== 4. the Tortoise extractor has no such check, at the rev catalog.json pins"
(cd $TO && echo "   clone HEAD $(git rev-parse HEAD)"; echo "   7c0fb278 present: $(git cat-file -t 7c0fb278 2>&1)  (fetched --depth 1 for this reading)")
echo "-- occurrences of polluted / empty directory at 7c0fb278:"
(cd $TO && git show 7c0fb278:tools/vmap_extractor/vmapextract/vmapexport.cpp | grep -c -i -E 'polluted|empty directory')
echo "-- its main(), which goes from processArgv straight to mkdir:"
(cd $TO && git show 7c0fb278:tools/vmap_extractor/vmapextract/vmapexport.cpp | sed -n '465,487p')

echo
echo "== 5. the two real finished installs on this box are in the blocked shape"
for d in ~/tbc-7.4c ~/vanilla-75b; do
  echo "-- $d/data/Buildings: $(ls $d/data/Buildings 2>/dev/null | wc -l) files"
  ls -l $d/data/Buildings/dir $d/data/Buildings/dir_bin 2>/dev/null | sed 's/^/     /'
done

echo
echo "== 6. the fence still applies, re-checked for this pass"
"$R"/pylauncher/.venv/bin/python - <<'PY'
import hashlib, pathlib, re
doc = pathlib.Path("/home/pk/yulon-runs/fix8-doodad/pyplan/upstream-cmangos-doodad-issue.md").read_bytes()
fences = re.findall(rb"^```diff\r?\n(.*?)^```", doc, re.S | re.M)
assert len(fences) == 1
fence = fences[0]
shipped = pathlib.Path(
    "/home/pk/yulon-runs/fix8-doodad/pylauncher/catalog/installers/shared/cmangos/"
    "patches/vmap-extractor-doodad-name-case.patch").read_bytes()
print(f"   doc      {len(doc):6d} bytes, {doc.count(bytes([13])):3d} CR")
print(f"   fence    {len(fence):6d} bytes, md5 {hashlib.md5(fence).hexdigest()}, {fence.count(bytes([13]))} CR")
print(f"   shipped  {len(shipped):6d} bytes, md5 {hashlib.md5(shipped).hexdigest()}")
print(f"   equal    {fence == shipped}")
crlf = fence.replace(b"\n", b"\r\n")
print(f"   as CRLF  {len(crlf):6d} bytes, md5 {hashlib.md5(crlf).hexdigest()}")
pathlib.Path("/tmp/fence-lf.patch").write_bytes(fence)
pathlib.Path("/tmp/fence-crlf.patch").write_bytes(crlf)
PY
for pair in "$CL 8ec338a1" "$TB f82e7d67"; do
  set -- $pair
  W=$(mktemp -d)
  (cd "$1" && git worktree add -q --detach "$W" "$2" 2>/dev/null)
  NAME=$(basename $1)
  for f in lf crlf; do
    OUT=$(cd "$W" && git apply --check /tmp/fence-$f.patch 2>&1)
    ST=$?
    echo "   $NAME $2  $f -> exit $ST"
    [ -n "$OUT" ] && echo "$OUT" | sed 's/^/       /'
  done
  (cd "$1" && git worktree remove --force "$W")
done
echo
echo "== done"
