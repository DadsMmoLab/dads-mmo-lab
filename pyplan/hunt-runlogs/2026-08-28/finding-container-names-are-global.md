# Two installs of the same game can never coexist — hardcoded container names

Found independently on **both** yulon-arch and yulon-ubuntu, round 3. On Ubuntu it stopped a fresh
install into a **brand-new, empty folder**, because an unrelated install elsewhere on the host still
had stopped containers holding the names:

    Error response from daemon: Conflict. The container name "/ac-client-data-init" is already in use

## The template contradicts itself
`catalog/installers/wow-wotlk/native/base.yml.tmpl`:

    :48    name: {{PROJECT_NAME}}          <- the compose PROJECT is parameterised per install
    :52    container_name: ac-database     <- ...and then the container names are pinned globally
    :119   container_name: ac-db-import
    :145   container_name: ac-client-data-init
    :207   container_name: ac-authserver
    :231   container_name: ac-worldserver

`composegen.py:332` substitutes a real per-install `PROJECT_NAME`. Docker would then scope container
names to that project automatically — **`container_name:` is precisely what overrides that scoping**
and makes the five names global to the host. The file does the work to be unique and then discards it.

## The DNS argument for keeping them does not hold
The env strings depend on the name resolving, e.g.
`AC_LOGIN_DATABASE_INFO: "ac-database;3306;root;...;acore_auth"` (`:125-132`). But the **service
names are already identical to the container names** — `ac-database`, `ac-db-import`,
`ac-client-data-init`, `ac-authserver`, `ac-worldserver` (`:51,118,144,206,230`). Compose DNS on the
`ac-network` resolves a service by its service name regardless of `container_name`, so those five
lines are redundant for inter-container addressing.

## Why it is still not a one-line deletion
`catalog.json`'s `containers` map names them **literally** (`"db": "ac-database"`,
`"world": "ac-worldserver"`, ...), and that map is how the entire controller finds its containers —
console, accounts, backups, maintenance, status, port checks. Delete `container_name:` and every
container becomes `<project>-ac-worldserver-1`, and the controller stops finding anything.

So the real change is: resolve containers by **project + service** rather than by literal name, and
make `catalog.json`'s `containers` map service names rather than container names. That is a design
decision touching every controller module, not a template edit.

**This is the third finding in this run whose obvious fix is wrong** — alongside setting
`reinstall=True` (which would arm [[finding-fast-path-would-delete-a-good-build]]) and changing the
compose-file guard before installs are resumable.

## Scope
The same five literal names appear in the compose the bash script path uses (from the cloned
AzerothCore tree), so the collision happens on both the script and native paths. The template above
is ours and is the one we control.

Workaround used on both boxes, to keep testing: `docker rm` the stale stopped containers — no `-v`,
volumes for both projects confirmed intact afterwards.
