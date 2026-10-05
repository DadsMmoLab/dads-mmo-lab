# T219: copy what changed in the server folder's data into the world-data volume, then
# start the world server. Windows only; tests/test_world_data_sync.py runs this script.
# A template, not a file to run: composegen fills its tokens, doubles every $ for
# compose and writes it into docker-compose.yml as the world server's entrypoint.
#
# The server folder's data (mounted read-only at data-src) stays where the map data
# and pathfinding are made and checked. Before every start Yu'lon writes a
# fingerprint into it, .yulon-world-data: one "<folder> <hash>" line per folder,
# "-" for a folder the server must see empty (pathfinding not finished). The
# volume keeps the lines of what it holds. A folder whose line differs is copied
# again whole into <folder>.yulon-new, swapped in, and only THEN is its line
# written, so a copy cut short by a Stop is copied again at the next start. A
# start Yu'lon did not make (Docker's restart policy, a hand-run compose up) uses
# the last fingerprint written, which is right unless data/ was changed by hand.
#
# The copy is parallel (8 cp at a time) over a folder tree made first: measured
# on yulon-win11 at about 134 s for 20,643 files and 1.12 GB, three times tar's
# speed. Making the folders inside the parallel cp raced, and a losing cp dropped
# its file without an error (2026-10-04). Each listing is written to a file and
# checked before anything is copied from it: in a pipe only the last command's
# status counts, so a listing that failed part-way over the file share would
# otherwise be copied as if whole, and its line written.
#
# A shell as PID 1 ignores SIGTERM unless it traps it, so without the trap a Stop
# during a copy would wait out the whole stop grace. exec makes the server PID 1.
src={{CORE_DIR}}/data-src
vol={{CORE_DIR}}/data
dirs="{{WORLD_DATA_DIRS}}"
fp=.yulon-world-data
job=
stopped() {
  [ -n "$job" ] && kill -TERM "$job" 2>/dev/null
  echo "yulon: stopped while copying the map data; the next start copies the rest"
  exit 143
}
trap stopped TERM INT
failed() {
  echo "yulon: could not copy $1 into the world-data volume: $2" >&2
  exit 1
}
line() {
  [ -f "$2" ] && sed -n "s/^$1 //p" "$2" | head -n 1
}
if [ ! -f "$src/$fp" ]; then
  echo "yulon: no fingerprint ($fp) in the server folder's data, so all of the map data is copied; a start from Yu'lon writes one"
fi
began=$(date +%s)
copied=
for d in $dirs; do
  want=$(line "$d" "$src/$fp")
  have=$(line "$d" "$vol/$fp")
  new="$vol/$d.yulon-new"
  list="$vol/$d.yulon-list"
  rm -rf "$new" "$list.dirs" "$list.files" ||
    failed "$d" "the copy a stop left behind could not be removed"
  [ -n "$want" ] && [ "$want" = "$have" ] && continue
  if [ -f "$vol/$fp" ]; then
    grep -v "^$d " "$vol/$fp" > "$vol/$fp.yulon-new"
    mv -f "$vol/$fp.yulon-new" "$vol/$fp" || failed "$d" "its fingerprint line could not be removed"
  fi
  mkdir "$new" || failed "$d" "no folder could be made in the volume"
  if [ "$want" != "-" ] && [ -d "$src/$d" ]; then
    echo "yulon: copying $d into the world-data volume"
    (
      cd "$src/$d" &&
        find . -type d -print0 > "$list.dirs" &&
        find . ! -type d -print0 > "$list.files" &&
        (cd "$new" && xargs -0 -r mkdir -p < "$list.dirs") &&
        xargs -0 -r -P8 -n200 cp --parents -t "$new" < "$list.files"
    ) &
    job=$!
    wait "$job"
    rc=$?
    job=
    [ "$rc" = 0 ] || failed "$d" "the copy ended with exit code $rc"
    rm -f "$list.dirs" "$list.files"
  fi
  rm -rf "$vol/$d" || failed "$d" "the old copy could not be removed"
  mv "$new" "$vol/$d" || failed "$d" "the new copy could not be moved into place"
  if [ -n "$want" ]; then
    echo "$d $want" >> "$vol/$fp" || failed "$d" "its fingerprint line could not be written"
  fi
  copied="$copied $d"
done
[ -n "$copied" ] && echo "yulon: copied$copied into the world-data volume in $(($(date +%s) - began)) s"
exec "$@"
