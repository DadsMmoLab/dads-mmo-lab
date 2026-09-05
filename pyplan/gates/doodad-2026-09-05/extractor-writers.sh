#!/usr/bin/env bash
# Who WRITES what into Buildings/, at the two revisions catalog.json pins.
#
# The round before this one read the CHECK in vmapexport.cpp's main() and wrote
# down, in six places, that the extractor writes `dir` as it goes and `dir_bin`
# at the end. This script asks the other question -- which of those two names
# has a writer -- and it is run against clones fetched by SHA, not against a
# server tree that may have moved.
#
# Run on yulon-fedora, 2026-09-05:  bash extractor-writers.sh
set -euo pipefail

CLASSIC=8ec338a1704e7dcb1c0213eb7ed58f9231ade40f   # cmangos/mangos-classic
TBC=f82e7d679c283b66bc2adc1b751aa1275e655673       # cmangos/mangos-tbc
PROBE=$HOME/cmangos-probe9

mkdir -p "$PROBE/classic" "$PROBE/tbc"
( cd "$PROBE/classic" && git init -q . \
  && (git remote add origin https://github.com/cmangos/mangos-classic 2>/dev/null || true) \
  && git fetch -q --depth 1 origin "$CLASSIC" && git rev-parse FETCH_HEAD )
( cd "$PROBE/tbc" && git init -q . \
  && (git remote add origin https://github.com/cmangos/mangos-tbc 2>/dev/null || true) \
  && git fetch -q --depth 1 origin "$TBC" && git rev-parse FETCH_HEAD )

for tree in classic tbc; do
  cd "$PROBE/$tree"
  echo "########## $tree $(git rev-parse FETCH_HEAD)"

  echo "===== 1. every occurrence of a \"/dir\"-suffixed path under contrib"
  git grep -nE '"/dir"' FETCH_HEAD -- contrib || echo "(none)"

  echo "===== 2. who opens dir_bin, and how"
  git grep -n 'dir_bin\|dirfile = fopen\|fclose(dirfile)' FETCH_HEAD -- contrib

  echo "===== 3. who writes temp_gameobject_models"
  git grep -n 'temp_gameobject_models' FETCH_HEAD -- contrib

  echo "===== 4. the order main() does it in"
  git grep -n 'ExtractWmo()\|ParsMapFiles()\|ExtractGameobjectModels()' \
      FETCH_HEAD -- contrib/vmap_extractor/vmapextract/vmapexport.cpp

  echo "===== 5. the other three tools: refusals, overwrites, skips"
  git grep -in 'polluted\|empty directory' FETCH_HEAD \
      -- contrib/mmap contrib/extractor contrib/vmap_assembler || echo "(no refusal in any of the three)"
  git grep -n 'fopen' FETCH_HEAD -- contrib/extractor/System.cpp
  git grep -n 'FileExists(' FETCH_HEAD -- contrib/extractor/System.cpp
  git grep -n 'return 1;' FETCH_HEAD -- contrib/vmap_assembler/vmap_assembler.cpp
  git grep -n 'fopen' FETCH_HEAD -- src/game/vmap/TileAssembler.cpp
  git grep -n 'shouldSkipTile' FETCH_HEAD -- contrib/mmap
  git show FETCH_HEAD:contrib/mmap/src/MapBuilder.cpp \
    | sed -n "$(git grep -n 'bool MapBuilder::shouldSkipTile' FETCH_HEAD -- contrib/mmap/src/MapBuilder.cpp | sed 's/.*MapBuilder.cpp:\([0-9]*\).*/\1/'),+23p"
done
