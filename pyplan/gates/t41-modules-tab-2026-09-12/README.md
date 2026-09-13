# T41 — the Modules tab says nothing about what is installed

Pressed on `yulon-win11` against a real WotLK install, 2026-09-12.

* **App**: `Yulon-v0.8.5-DeckTest-windows-x64.zip`, downloaded from the release
  and run as `yulon.exe` — the binary a player runs, not a source checkout.
  Title bar reads `Dad's MMO Lab — Yu'lon 0.8.5-Public`.
* **Install**: `C:\Users\pk\wow-server-playerbots`, the
  `mod-playerbots/azerothcore-wotlk` fork at `413bea61a`, `ac-worldserver`
  healthy. Left rail: `WoW WotLK — wow-server-playerbots`.
* **On disk in `modules/`**: `mod-playerbots`, and `mod-transmog` — the latter
  installed earlier the same evening through Yu'lon's own `apply_module()`,
  which reported `clone …` and `activate env/dist/etc/modules/transmog.conf`
  and left `Transmogrification.Enable = 1` on disk.

`modules-tab-v0.8.5-before.png` is the Modules tab in that state. Every row is
`[module] <name> — <description>`, in catalog order, with no mark of any kind.
Two of those modules are installed on this very server and neither row differs
from the 39 that are not.

That is the report, reproduced: "It detected my ancient months old install of
wotlk. None of modules detected lol."

## After

The same install, the same two modules on disk, with the fix applied to the
checkout on the box and `reload_modules()`'s row-building run against it:

```
installed on disk : ['mod-playerbots', 'mod-transmog']
rows total        : 42
rows marked       : 2
    ✓ [module] Transmogrification
    ✓ [module] mod-playerbots — installed here — not in this game's catalog
```

41 rows became 42: `mod-playerbots` is installed and the catalog has never
heard of it, so it gets a row of its own rather than being left out. The rest
are unmarked, which is the half that makes the mark mean something.

After the review round made the read per-family, the same install answers:

```
per-family on disk:
   ale      ['bmah']
   keg      ['bmah']
   mod      []
   module   ['mod-playerbots', 'mod-transmog']
rows 42, marked 3
    ✓ [module] Transmogrification
    ✓ [keg] Black Market Auction House
    ✓ [module] mod-playerbots — installed here — not in this game's catalog
```

`bmah` is the find: a keg installed in `ale_scripts/`, which the first pass —
reading `modules/` for every family — could never have marked at all. It is
also why accounting is per FOLDER: ale and keg share `ale_scripts/`, so the
first per-family pass listed `bmah` twice, once matched as a keg and once as an
uncatalogued ale.
