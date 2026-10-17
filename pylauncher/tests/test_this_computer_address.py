"""`networking.is_this_computer()`: the rule PLAY uses to tell "this computer" from another one.

The realm address a player types in the launcher either names this computer (where
PLAY starts the server and waits for it) or another one (where PLAY starts only the
game). One rule per fixture: the seams answer the single question under test and
nothing else, so a wrong branch cannot pass by luck.
"""

from __future__ import annotations

import socket

import pytest

from yulon import networking

NO_NAMES: tuple[str, ...] = ()


def _mine(
    address: str, *, own_ips: tuple[str, ...] = (), names: tuple[str, ...] = NO_NAMES
) -> bool:
    return networking.is_this_computer(
        address, can_bind=lambda ip: ip in own_ips, own_names=lambda: names
    )


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "127.0.0.2", "localhost", "LOCALHOST", "::1", "0.0.0.0", "127.0.0.1:3725"],
)
def test_loopback_is_this_computer(address: str) -> None:
    assert _mine(address)


def test_an_address_an_interface_of_this_host_owns_is_this_computer() -> None:
    assert _mine("192.168.0.60", own_ips=("192.168.0.60",))


def test_an_address_with_a_port_is_judged_by_its_host() -> None:
    assert _mine("192.168.0.60:3725", own_ips=("192.168.0.60",))
    assert not _mine("192.168.0.61:3725", own_ips=("192.168.0.60",))


def test_another_computers_address_is_not_this_computer() -> None:
    assert not _mine("192.168.0.60", own_ips=("192.168.0.99",))


def test_a_public_address_that_no_interface_owns_is_not_this_computer() -> None:
    assert not _mine("203.0.113.9")


def test_an_ipv6_literal_is_judged_whole_not_split_at_its_colons() -> None:
    assert _mine("fe80::1", own_ips=("fe80::1",))
    assert not _mine("fe80::2", own_ips=("fe80::1",))


def test_this_computers_own_name_is_this_computer() -> None:
    assert _mine("deck", names=("deck",))
    assert _mine("DECK.local", names=("deck",))
    assert _mine("deck.lan", names=("deck.lan",))


def test_another_computers_name_is_not_this_computer() -> None:
    assert not _mine("server.lan", names=("deck",))
    assert not _mine("deck2", names=("deck",))
    assert not _mine("play.example.com", names=("deck",))


def test_a_name_is_never_looked_up_in_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule runs on the GUI thread (the launcher's banner): it must not wait on a resolver."""
    seen: list[str] = []

    def spy(*args: object, **_kwargs: object) -> object:
        seen.append(str(args[0]))
        raise OSError("no resolver in a test")

    monkeypatch.setattr(socket, "getaddrinfo", spy)

    assert not _mine("a-name-nobody-owns.example")
    assert seen == []


def test_the_default_probe_knows_loopback_is_bindable_and_a_documentation_address_is_not() -> None:
    assert networking.is_this_computer("127.0.0.1")
    assert not networking.is_this_computer("192.0.2.77")  # TEST-NET-1: no host owns it
