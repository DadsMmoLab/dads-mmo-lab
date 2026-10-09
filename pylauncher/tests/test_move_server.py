"""T601 level 2: what a whole-server package is gathered from, and the pure steps of an import.

No Docker: a server folder on disk with `.git/HEAD` files where checkouts would be, conf files,
an answers file and Lua scripts; the shipped manifests for the module lookups. Every refusal is
asserted verbatim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import move_server
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.manifest import Manifest, Origin
from yulon.manifest_store import ManifestStore
from yulon.move import PackedSource
from yulon.move_flows import MoveError
from yulon.resources import manifests_dir

CATALOG = load_catalog()
WOTLK = CATALOG.get("wow-wotlk")
TBC = CATALOG.get("wow-tbc")
STORE = ManifestStore(manifests_dir(), "wow-wotlk")
CORE = "a" * 40
BOTS = "b" * 40
TRANSMOG = "c" * 40


def head(folder: Path, sha: str) -> None:
    (folder / ".git").mkdir(parents=True, exist_ok=True)
    (folder / ".git" / "HEAD").write_text(sha + "\n", encoding="utf-8")


def lookup(store: ManifestStore = STORE, extra: dict[tuple[str, str], Manifest] | None = None):
    def load(kind: str, item_id: str) -> Manifest | None:
        if extra and (kind, item_id) in extra:
            return extra[(kind, item_id)]
        try:
            return store.load(kind, item_id)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - the tab's own lookup answers None the same way
            return None

    return load


def wotlk_server(tmp_path: Path) -> Path:
    server = tmp_path / "server"
    server.mkdir()
    head(server, CORE)
    head(server / "modules" / "mod-playerbots", BOTS)
    head(server / "modules" / "mod-transmog", TRANSMOG)
    etc = server / "env" / "dist" / "etc"
    etc.mkdir(parents=True)
    (etc / "worldserver.conf").write_bytes(
        b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\r\n'
        b"Rate.XP.Kill = 3\r\n"
    )
    (etc / "worldserver.conf.dist").write_bytes(b"Rate.XP.Kill = 1\n")
    (server / ".yulon-install.json").write_text("{}", encoding="utf-8")
    (server / ".yulon-folder-id").write_text("0" * 32, encoding="utf-8")
    (server / ".yulon-module-answers.json").write_text('{"modules": {}}', encoding="utf-8")
    return server


def facts(server: Path, entry: CatalogEntry = WOTLK, **kw: object) -> move_server.ServerFacts:
    return move_server.gather_server_facts(
        entry,
        server,
        load_manifest=kw.pop("load_manifest", lookup()),  # type: ignore[arg-type]
        secret_password=kw.pop("secret_password", None),  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ the pack's facts


def test_the_sources_are_packed_at_the_commit_each_checkout_is_on(tmp_path: Path) -> None:
    got = facts(wotlk_server(tmp_path))
    assert [(s.repo, s.dest, s.commit) for s in got.spec.sources] == [
        ("mod-playerbots/azerothcore-wotlk", ".", CORE),
        ("mod-playerbots/mod-playerbots", "modules/mod-playerbots", BOTS),
    ]
    assert got.spec.sources[0].catalog_pin == WOTLK.emulator.sources[0].rev


def test_a_module_is_packed_with_its_commit_and_the_server_source_is_not(tmp_path: Path) -> None:
    got = facts(wotlk_server(tmp_path))
    assert [(m.type, m.id, m.origin, m.commit) for m in got.spec.modules] == [
        ("module", "mod-transmog", "catalog", TRANSMOG)
    ]
    assert got.spec.modules[0].repo == STORE.load("module", "mod-transmog").source.repo


def test_the_confs_go_without_the_database_password_and_keep_their_endings(
    tmp_path: Path,
) -> None:
    got = facts(wotlk_server(tmp_path))
    confs = [f for f in got.files if f.kind == "conf"]
    assert [c.target for c in confs] == ["env/dist/etc/worldserver.conf"]
    assert confs[0].data == (
        b'LoginDatabaseInfo = "ac-database;3306;root;{{DB_PASSWORD}};acore_auth"\r\n'
        b"Rate.XP.Kill = 3\r\n"
    )


def test_no_yulon_record_travels_but_the_answers_file(tmp_path: Path) -> None:
    got = facts(wotlk_server(tmp_path))
    targets = [f.target for f in got.files]
    assert ".yulon-module-answers.json" in targets
    assert not [t for t in targets if ".yulon-install" in t or ".yulon-folder-id" in t]
    assert not [t for t in targets if t.endswith(".dist")]


def test_a_generated_password_left_anywhere_refuses_the_pack(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    (server / "env" / "dist" / "etc" / "worldserver.conf").write_bytes(
        b"Rate.XP.Kill = 3\nMyNote = tbc-0123456789abcdef\n"
    )
    with pytest.raises(MoveError) as raised:
        facts(server, secret_password="tbc-0123456789abcdef")
    assert str(raised.value) == (
        "The database password is still in env/dist/etc/worldserver.conf after Yu'lon took it "
        "out of the database lines, so nothing was packed: it must never leave this computer. "
        "Take it out of that file, then pack again."
    )


def test_a_generated_password_in_a_database_line_is_taken_out_and_packs(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    (server / "env" / "dist" / "etc" / "worldserver.conf").write_bytes(
        b'LoginDatabaseInfo = "db;3306;root;tbc-0123456789abcdef;realmd"\n'
    )
    got = facts(server, secret_password="tbc-0123456789abcdef")
    assert b"tbc-0123456789abcdef" not in got.files[0].data


def test_a_module_added_from_a_folder_refuses_the_pack(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    head(server / "modules" / "mod-mine", "d" * 40)
    mine = Manifest(
        id="mod-mine",
        name="My Module",
        type="module",
        game="wow-wotlk",
        origin=Origin(kind="folder", path="/home/me/mod-mine", added="2026-10-01"),
    )
    with pytest.raises(MoveError) as raised:
        facts(server, load_manifest=lookup(extra={("module", "mod-mine"): mine}))
    assert str(raised.value) == (
        "My Module was added from a folder on this computer, so there is nothing the new "
        "computer could fetch it from again. Remove it, or add it from a link instead, then "
        "pack again. Nothing was packed."
    )


def test_a_link_module_carries_its_manifest(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    head(server / "modules" / "mod-linked", "e" * 40)
    linked = Manifest.model_validate(
        {
            "id": "mod-linked",
            "name": "Linked",
            "type": "module",
            "game": "wow-wotlk",
            "source": {"repo": "someone/mod-linked"},
            "origin": {"kind": "link", "added": "2026-10-01"},
        }
    )
    got = facts(server, load_manifest=lookup(extra={("module", "mod-linked"): linked}))
    assert ("module", "mod-linked", "link", "e" * 40) in [
        (m.type, m.id, m.origin, m.commit) for m in got.spec.modules
    ]
    carried = [f for f in got.files if f.kind == "manifest"]
    assert [c.target for c in carried] == ["module/mod-linked"]
    assert Manifest.model_validate_json(carried[0].data) == linked


def test_a_clone_folder_nothing_describes_refuses_the_pack(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    head(server / "modules" / "mod-who", "f" * 40)
    with pytest.raises(MoveError) as raised:
        facts(server)
    assert str(raised.value) == (
        "modules/mod-who is a module folder Yu'lon cannot name (no description of it, or more "
        "than one), so the new computer could not install it again. Remove it, or add it again "
        "on the Modules tab, then pack again. Nothing was packed."
    )


def test_a_checkout_whose_commit_cannot_be_read_refuses_the_pack(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    (server / "modules" / "mod-playerbots" / ".git" / "HEAD").write_text("ref: refs/heads/x\n")
    with pytest.raises(MoveError) as raised:
        facts(server)
    assert str(raised.value) == (
        "Yu'lon cannot read which commit mod-playerbots/mod-playerbots in modules/mod-playerbots "
        "is on, so the new computer could not build the same server. Nothing was packed."
    )


def test_the_players_lua_scripts_travel_and_yulons_own_do_not(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    lua = server / "env" / "dist" / "etc" / "modules" / "lua_scripts"
    (lua / "mine").mkdir(parents=True)
    (lua / "mine" / "hello.lua").write_bytes(b"print('mine')\n")
    (lua / "laid.lua").write_bytes(b"print('yulon')\n")
    (lua / ".yulon-lua-scripts.json").write_text(
        '{"version": 1, "files": {"env/dist/etc/modules/lua_scripts/laid.lua": "'
        + "0" * 64
        + '"}}',
        encoding="utf-8",
    )
    got = facts(server)
    assert [f.target for f in got.files if f.kind == "lua"] == [
        "env/dist/etc/modules/lua_scripts/mine/hello.lua"
    ]


def test_a_lua_file_reached_through_a_link_is_not_packed(tmp_path: Path) -> None:
    server = wotlk_server(tmp_path)
    lua = server / "env" / "dist" / "etc" / "modules" / "lua_scripts"
    lua.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.lua").write_bytes(b"x")
    (lua / "linked").symlink_to(outside, target_is_directory=True)
    (lua / "file.lua").symlink_to(outside / "secret.lua")
    assert [f for f in facts(server).files if f.kind == "lua"] == []


# ------------------------------------------------------------------ the pinned entry


def packed(entry: CatalogEntry, **commits: str) -> tuple[PackedSource, ...]:
    return tuple(
        PackedSource(
            repo=s.repo,
            dest=s.dest,
            commit=commits.get(s.dest, s.rev or "0" * 40),
            catalog_pin=s.rev,
        )
        for s in entry.emulator.sources
    )


def test_the_pinned_entry_carries_the_packed_commits_and_nothing_else_changes() -> None:
    pinned = move_server.pinned_entry(WOTLK, packed(WOTLK, **{".": CORE}))
    assert pinned.emulator.sources[0].rev == CORE
    assert pinned.emulator.sources[1].rev == WOTLK.emulator.sources[1].rev
    assert pinned.model_copy(update={"emulator": WOTLK.emulator}) == WOTLK
    assert WOTLK.emulator.sources[0].rev != CORE  # the catalog itself is untouched


def test_other_sources_are_refused_with_names() -> None:
    other = (*packed(WOTLK)[:1], PackedSource(repo="x/y", dest="modules/y", commit=CORE))
    with pytest.raises(MoveError) as raised:
        move_server.pinned_entry(WOTLK, other)
    assert str(raised.value) == (
        "This Yu'lon builds WoW WotLK from other sources than the old computer did (the file has "
        "x/y in modules/y; this Yu'lon has mod-playerbots/mod-playerbots in "
        "modules/mod-playerbots), so it cannot build the same server. Install the same Yu'lon "
        "version on both computers, then pack again."
    )


def test_the_pinned_rev_reaches_the_clone(tmp_path: Path) -> None:
    from yulon import git
    from yulon.catalog import native

    pinned = move_server.pinned_entry(WOTLK, packed(WOTLK, **{".": CORE}))
    seen: list[git.CloneSpec] = []
    engine = native.StagedInstaller.__new__(native.StagedInstaller)
    engine.entry = pinned

    def lines(spec: git.CloneSpec, recorded_as: str):
        seen.append(spec)
        yield "cloned"

    engine._clone_lines = lines  # type: ignore[method-assign]
    engine.already_cloned = lambda ctx, recorded_as, existing: False  # type: ignore[method-assign]
    engine.refuse_unowned_checkout = lambda *a: None  # type: ignore[method-assign]
    engine._remote_of = lambda dest: None  # type: ignore[method-assign]
    ctx = native.StageContext(
        server_dir=tmp_path, client_dir=None, state=None, cancel=None, secrets=None
    )  # type: ignore[arg-type]
    list(engine.stage_clone_sources(ctx, pinned.emulator.sources, recorded_as="clone"))
    assert [s.rev for s in seen] == [CORE, WOTLK.emulator.sources[1].rev]


# ------------------------------------------------------------------ the rev rows


def _rel(dest: Path) -> str:
    return dest.relative_to("/srv").as_posix() or "."


def rows(entry: CatalogEntry, commits: dict[str, str], ahead: dict[str, int | None]):
    return move_server.moved_in_revs(
        entry,
        packed(entry, **commits),
        Path("/srv"),
        head_version=lambda dest: f"{commits.get(_rel(dest), 'x')[:7]} · 2026-10-01",
        commits_since=lambda dest, rev: ahead[_rel(dest)],
    )


def test_a_source_on_this_catalogs_pin_gets_no_row() -> None:
    assert rows(WOTLK, {}, {}) == ()


def test_a_source_behind_the_pin_is_recorded_against_the_packed_commit() -> None:
    got = rows(WOTLK, {".": CORE}, {".": 0})
    assert got == (
        move_server.SourceRevRow(
            repo="mod-playerbots/azerothcore-wotlk", built="aaaaaaa · 2026-10-01", pin=CORE, ahead=0
        ),
    )


def test_a_source_past_the_pin_is_recorded_against_this_catalogs_pin() -> None:
    got = rows(WOTLK, {".": CORE}, {".": 12})
    assert got[0].pin == WOTLK.emulator.sources[0].rev
    assert got[0].ahead == 12


def test_an_uncountable_distance_reads_as_past_the_pin() -> None:
    got = rows(WOTLK, {".": CORE}, {".": None})
    assert (got[0].pin, got[0].ahead) == (WOTLK.emulator.sources[0].rev, None)


def test_the_rows_read_on_the_server_tab_as_catch_up_and_as_return() -> None:
    """The rows are read by T588's own function: behind offers the catch-up, past offers none."""
    from yulon.catalog import native

    pin = WOTLK.emulator.sources[0].rev or ""
    pins = (native.CatalogPin("mod-playerbots/azerothcore-wotlk", pin, CORE),)
    behind = rows(WOTLK, {".": CORE}, {".": 0})[0]
    past = rows(WOTLK, {".": CORE}, {".": 3})[0]

    def as_rev(row: move_server.SourceRevRow) -> native.SourceRev:
        return native.SourceRev(row.repo, row.built, pin=row.pin, ahead=row.ahead)

    _rows, moved = native._against_the_catalog((as_rev(behind),), pins)
    assert [p.rev for p in moved] == [pin]
    _rows, moved = native._against_the_catalog((as_rev(past),), pins)
    assert moved == ()
    assert "past the tested pin" in native.commits_past_pin(as_rev(past))


# ------------------------------------------------------------------ laying a conf


def test_a_laid_conf_gets_this_machines_password_and_keeps_its_endings() -> None:
    got = move_server.lay_conf(
        b'LoginDatabaseInfo = "db;3306;root;{{DB_PASSWORD}};realmd"\r\nRate = 3\r\n',
        None,
        (),
        "tbc-feedfacefeedface",
    )
    assert got == b'LoginDatabaseInfo = "db;3306;root;tbc-feedfacefeedface;realmd"\r\nRate = 3\r\n'


def test_the_machine_keys_take_the_value_this_install_wrote() -> None:
    packed_conf = (
        b'LoginDatabaseInfo = "old-host;3306;root;{{DB_PASSWORD}};realmd"\n'
        b"WorldServerPort = 8085\n"
        b"AiPlayerbot.MinRandomBots = 200\n"
    )
    installed = (
        b'LoginDatabaseInfo = "new-host;3306;root;tbc-1111111111111111;realmd"\n'
        b"WorldServerPort = 8095\n"
        b"AiPlayerbot.MinRandomBots = 500\n"
    )
    keys = move_server.machine_keys(TBC, "etc/mangosd.conf")
    assert "WorldServerPort" in keys and "AiPlayerbot.MinRandomBots" not in keys
    got = move_server.lay_conf(packed_conf, installed, keys, "tbc-1111111111111111")
    assert got == (
        b'LoginDatabaseInfo = "new-host;3306;root;tbc-1111111111111111;realmd"\n'
        b"WorldServerPort = 8095\n"
        b"AiPlayerbot.MinRandomBots = 200\n"
    )


def test_a_tuning_key_the_table_sets_to_a_literal_travels() -> None:
    assert "AiPlayerbot.MinRandomBots" not in move_server.machine_keys(TBC, "etc/aiplayerbot.conf")
    assert move_server.machine_keys(WOTLK, "env/dist/etc/worldserver.conf") == frozenset()


def test_a_placeholder_with_no_password_to_put_there_refuses() -> None:
    with pytest.raises(MoveError) as raised:
        move_server.lay_conf(b"X = a;1;u;{{DB_PASSWORD}};d\n", None, (), None)
    assert str(raised.value) == (
        "A packed conf file needs this server's database password, and Yu'lon could not read "
        "it. Nothing more was changed."
    )


def test_a_database_line_of_a_conf_the_table_does_not_name_takes_this_installs_value() -> None:
    got = move_server.lay_conf(
        b'LoginDatabaseInfo = "old;3306;acore;{{DB_PASSWORD}};acore_auth"\nRate.XP.Kill = 3\n',
        b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\nRate.XP.Kill = 1\n',
        (),
        "password",
    )
    assert got == (
        b'LoginDatabaseInfo = "ac-database;3306;root;password;acore_auth"\nRate.XP.Kill = 3\n'
    )


# ------------------------------------------------------------ GitHub: is the commit still there


class _Get:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.urls: list[str] = []

    def __call__(self, url: str, accept: str) -> bytes:
        self.urls.append(url)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer  # type: ignore[return-value]


def _http_error(code: int) -> BaseException:
    import urllib.error

    return urllib.error.HTTPError("https://api.github.com/x", code, "x", {}, None)  # type: ignore[arg-type]


def test_a_commit_github_does_not_have_is_gone() -> None:
    from yulon.catalog import upstream

    assert upstream.commit_exists("a/b", CORE, get=_Get(_http_error(404))) is False
    assert upstream.commit_exists("a/b", CORE, get=_Get(_http_error(422))) is False


def test_a_commit_github_answers_for_is_there() -> None:
    from yulon.catalog import upstream

    get = _Get(('{"sha": "' + CORE + '"}').encode())
    assert upstream.commit_exists("a/b", CORE, get=get) is True
    assert get.urls == [f"https://api.github.com/repos/a/b/commits/{CORE}"]


@pytest.mark.parametrize("answer", [_http_error(403), _http_error(500), OSError("offline"), b"[]"])
def test_no_clear_answer_is_never_gone(answer: object) -> None:
    from yulon.catalog import upstream

    assert upstream.commit_exists("a/b", CORE, get=_Get(answer)) is None
