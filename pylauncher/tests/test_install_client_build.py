"""The Install press reads the client's build before any compile or extraction (T594).

T576 checked a WotLK client at Set client folder, Make... and Play; the games that extract
their maps from the player's own client (TBC, Vanilla, Tortoise, Centurion) were never asked
at Install, so a wrong client cost a compile and hours of extraction. `gather()` now reads
the exe's version through the same `client_build.refusal()` and a wrong build is a preflight
refusal. The builds are the servers' own accept lists, read from the pinned sources.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pe_fixture import exe as _exe
from tests.pe_fixture import pe as _pe
from tests.pe_fixture import versioned as _versioned
from tests.test_preflight import _client_gather
from yulon import client_build, docker
from yulon import platform as platform_module
from yulon.catalog import preflight
from yulon.catalog.catalog import ClientSpec, load_catalog
from yulon.catalog.families import clientdir


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    client_build.forget_cached_builds()
    monkeypatch.setattr(
        platform_module,
        "bind_tcp",
        lambda host, port, **_kw: platform_module.PortBind(host, port, "free", ""),
    )
    monkeypatch.setattr(docker, "port_holders", lambda *_a, **_kw: docker.PortHolders())


def _client(root: Path, spec: ClientSpec) -> Path:
    client = root / "client"
    (client / clientdir.DATA_DIR).mkdir(parents=True)
    if spec.required_file is not None:
        required = client.joinpath(*spec.required_file.split("/"))
        required.parent.mkdir(parents=True, exist_ok=True)
        required.write_bytes(b"")
    for locale in spec.locales:
        (client / clientdir.DATA_DIR / locale).mkdir()
        (client / clientdir.DATA_DIR / locale / f"locale-{locale}.MPQ").write_bytes(b"")
    return client


def _spec(game: str) -> ClientSpec:
    spec = preflight.client_spec_for(load_catalog().get(game))
    assert spec is not None
    return spec


def _refusals(game: str, client: Path, tmp_path: Path) -> list[preflight.Check]:
    got = _client_gather(load_catalog().get(game), tmp_path / "server", client_dir=client)
    return [c for c in got.client_checks if c.verdict == "refuse"]


# --- the catalog names the builds, each from the server's own accept list ----------------


@pytest.mark.parametrize(
    ("game", "required", "also"),
    [
        ("wow-tbc", 8606, ()),
        ("wow-vanilla", 5875, (6005, 6141)),
        ("wow-tortoise", 5875, ()),
        ("wow-centurion", 12340, ()),
    ],
)
def test_a_game_that_reads_the_clients_files_names_the_builds_its_server_accepts(
    game: str, required: int, also: tuple[int, ...]
) -> None:
    client = load_catalog().get(game).client

    assert client.required_build == required
    assert client.also_builds == also


def test_every_game_with_client_rules_names_a_build() -> None:
    """A game added later that extracts from the player's client cannot skip the check."""
    for entry in load_catalog().games:
        if preflight.client_spec_for(entry) is None:
            continue
        assert entry.client.required_build is not None, entry.id


# --- the install preflight ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("game", "wrong_parts", "needed"),
    [
        ("wow-tbc", (1, 12, 1, 5875), "8606"),
        ("wow-vanilla", (2, 4, 3, 8606), "5875"),
        ("wow-tortoise", (3, 3, 5, 12340), "5875"),
        ("wow-centurion", (3, 3, 3, 11723), "12340"),
    ],
)
def test_a_client_of_another_game_is_refused_by_the_install_preflight(
    game: str, wrong_parts: tuple[int, ...], needed: str, tmp_path: Path
) -> None:
    entry = load_catalog().get(game)
    client = _client(tmp_path, _spec(game))
    _versioned(client, *wrong_parts)

    got = _client_gather(entry, tmp_path / "server", client_dir=client)
    refused = [c for c in got.client_checks if c.verdict == "refuse"]
    report = preflight.evaluate(entry, tmp_path / "server", got)

    assert [c.name for c in refused] == [clientdir.BUILD_CHECK]
    assert not report.ok()
    said = report.message()
    major, minor, patch, build = wrong_parts
    assert f"{major}.{minor}.{patch} ({build})" in said
    assert needed in said and entry.client.version in said


@pytest.mark.parametrize(
    ("game", "parts"),
    [
        ("wow-tbc", (2, 4, 3, 8606)),
        ("wow-vanilla", (1, 12, 1, 5875)),
        ("wow-vanilla", (1, 12, 2, 6005)),
        ("wow-vanilla", (1, 12, 3, 6141)),
        ("wow-tortoise", (1, 12, 1, 5875)),
        ("wow-centurion", (3, 3, 5, 12340)),
    ],
)
def test_the_right_client_passes_the_install_preflight(
    game: str, parts: tuple[int, ...], tmp_path: Path
) -> None:
    client = _client(tmp_path, _spec(game))
    _versioned(client, *parts)

    assert _refusals(game, client, tmp_path) == []


def test_an_exe_with_no_version_resource_is_not_refused_at_install(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client(tmp_path, _spec("wow-tbc"))
    _exe(client, "Wow.exe", _pe(None))

    with caplog.at_level("INFO"):
        assert _refusals("wow-tbc", client, tmp_path) == []
    assert any("no version resource" in r.getMessage() for r in caplog.records)


def test_a_folder_the_folder_rules_refused_gets_no_second_build_refusal(tmp_path: Path) -> None:
    """No `Data/`: the one sentence that matters is "not a game client"."""
    client = tmp_path / "client"
    client.mkdir()
    _versioned(client, 3, 3, 5, 12340)

    names = [c.name for c in _refusals("wow-tbc", client, tmp_path)]

    assert names == [clientdir.CLIENT_CHECK]


def test_a_wrong_client_is_refused_before_the_container_probe(tmp_path: Path) -> None:
    probed: list[Path] = []

    def probe(path: Path) -> bool | None:
        probed.append(path)
        return True

    client = _client(tmp_path, _spec("wow-tbc"))
    _versioned(client, 3, 3, 5, 12340)
    _client_gather(
        load_catalog().get("wow-tbc"),
        tmp_path / "server",
        client_dir=client,
        bind_mount_ok=probe,
    )

    assert client not in probed


# --- several accepted builds -------------------------------------------------------------


def test_a_server_that_accepts_several_builds_names_them_all(tmp_path: Path) -> None:
    exe = _versioned(tmp_path, 2, 4, 3, 8606)

    said = client_build.refusal(exe, version="1.12.1", build=5875, also=(6005, 6141))

    assert said is not None
    assert "2.4.3 (8606)" in said and "5875" in said and "6005" in said and "6141" in said


@pytest.mark.parametrize("parts", [(1, 12, 1, 5875), (1, 12, 2, 6005), (1, 12, 3, 6141)])
def test_each_accepted_build_passes(tmp_path: Path, parts: tuple[int, ...]) -> None:
    exe = _versioned(tmp_path, *parts)

    assert client_build.refusal(exe, version="1.12.1", build=5875, also=(6005, 6141)) is None


# --- the catalog refuses a build list that contradicts itself ----------------------------


def test_also_builds_need_a_required_build() -> None:
    from pydantic import ValidationError

    from yulon.catalog.catalog import Client

    with pytest.raises(ValidationError, match="needs a required_build"):
        Client(version="1.12.1", build=5875, also_builds=(6005,))


def test_a_build_listed_twice_is_refused() -> None:
    from pydantic import ValidationError

    from yulon.catalog.catalog import Client

    with pytest.raises(ValidationError, match="repeats a build"):
        Client(version="1.12.1", build=5875, required_build=5875, also_builds=(5875,))
    with pytest.raises(ValidationError, match="repeats a build"):
        Client(version="1.12.1", build=5875, required_build=5875, also_builds=(6005, 6005))
