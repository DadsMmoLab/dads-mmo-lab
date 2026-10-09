"""Login keys, tokens and every spelling of the home folder leave a support file (T595).

A real user's zip said its passwords and its home folder were gone, and still carried
the realm's `UPDATE account SET sessionkey = '<80 hex>'` lines (the credential the world
server accepts for that login) and 322 copies of `C:\\\\Users\\\\<name>`, the home folder as a
Python repr writes it. Everything here is synthetic: shaped like that leak, built at
runtime, never a real person's value.
"""

from __future__ import annotations

import random
import secrets
import string
import zipfile
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest

from yulon import platform
from yulon.catalog.catalog import load_catalog
from yulon.support import bundle
from yulon.support.redact import MASK, Redactor, Unredactable, home_of
from yulon.support.sources import InstallFacts, LiveLog, Sources

WIN_HOME = "C:\\Users\\Zephyrine"
NAME = "Zephyrine"


def _hex(n: int = 40) -> str:
    return secrets.token_hex(n)


# ---------------------------------------------------------------- session keys and tokens


def test_a_realm_session_key_is_masked_whatever_follows_it() -> None:
    key = _hex()
    line = (
        f"UPDATE account SET sessionkey = '{key}', last_ip = '203.0.113.9', "
        "last_attempt_ip = '203.0.113.9' WHERE username = 'ALICE'"
    )
    out = Redactor.build([]).redact(line)
    assert key not in out and key[-12:] not in out
    assert "last_ip = '203.0.113.9'" in out  # the rest of the statement is left readable
    assert "WHERE username = 'ALICE'" in out


@pytest.mark.parametrize("column", ["sessionkey", "session_key", "SessionKey", "sha_pass_hash"])
def test_the_session_and_hash_columns_are_masked_by_name(column: str) -> None:
    value = _hex(20)
    out = Redactor.build([]).redact(f"UPDATE account SET {column}='{value}' WHERE id = 7")
    assert value not in out and MASK in out


def test_the_srp_verifier_and_salt_columns_are_masked_in_an_sql_line() -> None:
    v, s = _hex(32), _hex(32)
    out = Redactor.build([]).redact(f"UPDATE account SET v = '{v}', s = '{s}' WHERE id = 3")
    assert v not in out and s not in out


def test_a_hex_secret_in_an_insert_is_masked_though_no_column_names_it() -> None:
    digest, other = _hex(20).upper(), _hex(32)
    line = f"INSERT INTO account VALUES (4, 'BOB', '{digest}', X'{other}', 0)"
    out = Redactor.build([]).redact(line)
    assert digest not in out and other not in out
    assert "'BOB'" in out


def test_a_commit_hash_outside_sql_is_left_alone() -> None:
    commit = _hex(20)
    text = f"checked out '{commit}' of the core"
    assert Redactor.build([]).redact(text) == text


@pytest.mark.parametrize(
    "template",
    [
        "api_key = {s}",
        'access_token: "{s}"',
        "refresh-token={s}",
        "client_secret = {s}",
        "{{'sessionkey': '{s}'}}",
        '{{"token": "{s}", "x": 1}}',
        "Authorization: Basic {s}",
        "Authorization: Bearer {s}",
        "Proxy-Authorization = {s}",
        "GET /soap?token={s}&x=1 HTTP/1.1",
        "GET /a?apikey={s} HTTP/1.1",
        "see https://admin:{s}@127.0.0.1:7878/ for SOAP",
        '"auth": "{s}"',
        '{{"identitytoken": "{s}"}}',
        "docker login registry.example -u bob -p {s}",
        "docker login registry.example -u bob --password {s}",
        "['docker', 'login', '-u', 'bob', '-p', '{s}']",
        "mysql -uroot -p{s} acore_auth",
        "run: -uroot -p{s}",
        ".account create ALICE {s}",
        ".account set password ALICE {s} {s}",
        "<password>{s}</password>",
        "<SessionKey>{s}</SessionKey>",
        "MYSQL_PWD={s}",
        "Cookie: sessionid={s}",
        "Set-Cookie: session={s}; Path=/; HttpOnly",
        "cookie: a=1; auth={s}; theme=dark",
        "SessionId = {s}",
        "JSESSIONID={s}",
        "Session key: {s}",
        "{{'sessionkey': b'{s}'}}",
        "session_key_auth = {s}",
    ],
)
def test_credentials_are_masked_in_the_shapes_a_log_prints_them(template: str) -> None:
    secret = "Zq" + secrets.token_hex(10) + "Wv"
    out = Redactor.build([]).redact(template.format(s=secret))
    assert secret not in out, out
    assert secret[:8] not in out and secret[-8:] not in out, out


def test_a_bare_80_digit_hex_is_a_session_key_and_other_digests_are_not() -> None:
    key = _hex(40)
    out = Redactor.build([]).redact(f"K={key} then {_hex(20)} {_hex(32)} {_hex(64)}")
    assert key not in out and out.count(MASK) == 1, out


def test_a_cookie_line_keeps_its_names_and_attributes_and_loses_every_value() -> None:
    secret = "Zq" + secrets.token_hex(10) + "Wv"
    out = Redactor.build([]).redact(f"Set-Cookie: sid={secret}; Path=/x; Expires=Fri; Secure")
    assert out == f"Set-Cookie: sid={MASK}; Path=/x; Expires=Fri; Secure"


def test_a_bearer_word_in_prose_survives() -> None:
    for text in ("Basic setup is done", "a token count of 3", "token: ok"):
        assert Redactor.build([]).redact(text) == text


def test_mysql_error_text_and_ordinary_settings_are_untouched() -> None:
    text = "ERROR 1045 (28000): Access denied for user 'root'@'x' (using password: YES)"
    assert Redactor.build([]).redact(text) == text


def test_secret_masking_is_linear_on_one_huge_line() -> None:
    import time

    blobs = [
        "sessionkey " * 20000 + "docker login " * 5000 + "-uroot " * 5000,
        "C:\\Users\\a b c d e f g " * 20000,
        "C:/Users/" + "%41" * 100000,
        "Set-Cookie: " + "a=b; " * 40000,
        "UPDATE x " * 30000 + "\n" + "mysql " * 30000,
        "\\\\" * 100000 + "wsl.localhost",
        "\\" * 100000 + "zephyrin",
        "C:\\Users\\" + "%41%20" * 100000,
        "C:\\Users\\" * 100000,
        "C:\\Users\\a " * 100000,
    ]
    redactor = Redactor.build(["Known12345"], home=Path(WIN_HOME), also_home=["/home/penguin"])
    for blob in blobs:
        started = time.monotonic()
        redactor.redact(blob)
        assert time.monotonic() - started < 4.0, blob[:30]


# ---------------------------------------------------------------- the home folder


def _spellings(path: str) -> list[str]:
    """The ways a log writes a Windows path, from the one the ticket found outward."""
    win = path
    fwd = path.replace("\\", "/")
    tail = fwd.split(":", 1)[1]
    return [
        win,
        fwd,
        win.replace("\\", "\\\\"),  # a Python repr: the leak in the ticket
        win.replace("\\", "\\\\\\\\"),  # a repr of a repr / JSON of a repr
        fwd.replace("/", "\\"),
        "/mnt/c" + tail,
        "/mnt/C" + tail,
        "/c" + tail,
        "\\\\wsl.localhost\\Ubuntu-24.04\\mnt\\c" + tail.replace("/", "\\"),
        "\\\\wsl$\\Ubuntu\\mnt\\c" + tail.replace("/", "\\"),
        "file:///" + fwd,
        "file:///" + quote(fwd, safe="/"),
        quote(win, safe=""),  # C%3A%5CUsers%5C...
        quote(fwd, safe=""),
        win.lower(),
        win.upper(),
        win.replace("C:", "c:"),
    ]


@pytest.mark.parametrize("spelling", _spellings(WIN_HOME))
def test_the_home_folder_is_gone_in_every_spelling_a_log_writes_it(spelling: str) -> None:
    tail = "\\yulon-tortoise\\data" if "\\" in spelling else "/yulon-tortoise/data"
    text = f"[WinError 3] cannot find '{spelling}{tail}' (also {spelling})"
    out = Redactor.build([], home=Path(WIN_HOME)).redact(text)
    assert NAME.lower() not in out.lower(), out
    for scrap in ("users", "mnt", "wsl", "c:", "c%3a"):  # the rest of the path goes with it
        assert scrap not in out.lower(), (scrap, out)
    assert "yulon-tortoise" in out  # the part after the home folder stays


def test_the_ticket_line_exactly() -> None:
    text = (
        "[WinError 3] The system cannot find the path: "
        "'C:\\\\Users\\\\Zephyrine\\\\yulon-tortoise\\\\x'"
    )
    out = Redactor.build([], home=Path(WIN_HOME)).redact(text)
    assert out == "[WinError 3] The system cannot find the path: '~\\\\yulon-tortoise\\\\x'"


@pytest.mark.parametrize(
    "spelling",
    [
        "/home/zephyrine/world",
        "\\\\wsl.localhost\\Ubuntu-24.04\\home\\zephyrine\\world",
        "\\\\wsl$\\Ubuntu\\home\\zephyrine\\world",
        "//wsl.localhost/Ubuntu/home/zephyrine/world",
        "\\\\\\\\wsl.localhost\\\\Ubuntu\\\\home\\\\zephyrine\\\\world",
        "/home/zephyrine/world".replace("/", "%2F"),
        "/home/Zephyrine/world",
    ],
)
def test_a_wsl_home_is_gone_from_its_unc_and_escaped_spellings(spelling: str) -> None:
    out = Redactor.build([], home=Path("/home/zephyrine")).redact(f"cannot open {spelling}!")
    assert "ephyrine" not in out.lower(), out
    assert "ubuntu" not in out.lower(), out
    assert out.endswith("world!") or "world" in out


def test_a_user_name_with_a_space_or_accents_is_gone_url_encoded_and_escaped() -> None:
    home = "C:\\Users\\Zoë Ågård"
    for form in (
        home,
        home.replace("\\", "\\\\"),
        quote(home, safe=""),
        "C:/Users/Zo%C3%AB%20%C3%85g%C3%A5rd",
        "C:\\\\Users\\\\Zo\\u00eb \\u00c5g\\u00e5rd",
    ):
        out = Redactor.build([], home=Path(home)).redact(f"x {form}\\f y")
        assert "Zo" not in out and "gard" not in out.lower() and "g%C3" not in out, (form, out)


def _all_percent(text: str) -> str:
    """Every byte as %HH, letters and digits too: a valid spelling nobody writes by hand."""
    return "".join(f"%{byte:02X}" for byte in text.encode("utf-8"))


def test_a_name_spelled_wholly_in_percent_escapes_is_gone() -> None:
    redactor = Redactor.build([], home=Path("C:\\Users\\Alice Smith"))
    sep = "%5C"
    for form in (
        f"C%3A{sep}Users{sep}{_all_percent('Alice Smith')}{sep}file",
        f"C%3A{sep}Users{sep}%41lice%20Smith{sep}file",
        f"C:\\Users\\{_all_percent('Alice Smith')}\\file",
    ):
        out = redactor.redact(form)
        assert "lice" not in out and "41" not in out and "Smith" not in out, (form, out)
    posix = Redactor.build([], home=Path("/home/alice"))
    for form in ("%2Fhome%2F%61lice%2Ffile", "/home/%61%6C%69%63%65/file"):
        out = posix.redact(form)
        assert "61" not in out and "lice" not in out, (form, out)


def test_another_account_is_masked_whole_with_several_spaces_or_escapes() -> None:
    redactor = Redactor.build([], home=Path("/home/zephyrine"))
    for text, gone in (
        ("C:\\Users\\John van Doe\\x", ["van", "Doe", "John"]),
        ("C:/Users/John van Doe/x", ["van", "Doe"]),
        ("'C:\\Users\\Mary Ann Lee'", ["Ann", "Lee", "Mary"]),
        ("C%3A%5CUsers%5CJohn%20van%20Doe%5Cx", ["van", "Doe"]),
        ("C:\\Users\\%4Aohn\\x", ["ohn"]),
    ):
        out = redactor.redact(text)
        for word in gone:
            assert word not in out, (text, out)
    # one word of prose after a bare profile is not swallowed
    assert redactor.redact("C:\\Users\\Bob and then more").endswith("and then more")


def test_the_short_8_3_alias_of_a_long_user_name_is_gone() -> None:
    home = "C:\\Users\\Zephyrine-Quillfeather"
    out = Redactor.build([], home=Path(home)).redact("C:\\Users\\ZEPHYR~1\\AppData")
    assert "ZEPHYR" not in out


def test_the_user_name_alone_is_masked_where_it_is_a_path_component() -> None:
    redactor = Redactor.build([], home=Path(WIN_HOME))
    out = redactor.redact("D:\\backup\\Zephyrine\\saves and /srv/Zephyrine/x and Zephyrine\\y")
    assert NAME not in out, out
    # ... but a word that merely contains it, or the name in prose, is not a path.
    assert redactor.redact("Zephyrinest said hi") == "Zephyrinest said hi"
    assert redactor.redact("/srv/Zephyrinestuff/x") == "/srv/Zephyrinestuff/x"


def test_a_generic_user_name_is_not_masked_everywhere() -> None:
    redactor = Redactor.build([], home=Path("/home/root"))
    assert redactor.redact("/var/root/x /opt/user/y") == "/var/root/x /opt/user/y"


def test_another_windows_account_seen_from_wsl_is_masked_by_structure() -> None:
    redactor = Redactor.build([], home=Path("/home/zephyrine"))
    for text in (
        "/mnt/c/Users/Someone Else/dml",
        "C:\\\\Users\\\\Quillfeather\\\\x",
        "C:/Users/Quillfeather/x",
        "\\\\wsl.localhost\\Ubuntu\\mnt\\d\\Users\\Quillfeather\\x",
    ):
        out = redactor.redact(text)
        assert "Quillfeather" not in out and "Someone" not in out, (text, out)
    # the shared profiles are not anyone's home
    assert redactor.redact("C:\\Users\\Public\\Documents") == "C:\\Users\\Public\\Documents"
    assert redactor.redact("C:\\Users\\Default\\NTUSER") == "C:\\Users\\Default\\NTUSER"


def test_the_extra_homes_of_a_wsl_install_are_masked_too() -> None:
    redactor = Redactor.build([], home=Path(WIN_HOME), also_home=["/home/penguin"])
    out = redactor.redact("a /home/penguin/yulon and \\\\wsl$\\U\\home\\penguin\\y")
    assert "penguin" not in out


def test_home_of_reads_a_home_out_of_an_install_folder() -> None:
    assert home_of("/home/penguin/yulon-wotlk") == "/home/penguin"
    assert home_of("\\\\wsl.localhost\\Ubuntu\\home\\penguin\\yulon") == "/home/penguin"
    assert home_of("C:\\Users\\Zephyrine\\yulon-wotlk") == "C:\\Users\\Zephyrine"
    assert home_of("/mnt/c/Users/Zephyrine/yulon") == "C:\\Users\\Zephyrine"
    assert home_of("/srv/yulon") is None


def test_a_longer_name_sharing_the_prefix_and_the_text_after_a_home_survive() -> None:
    redactor = Redactor.build([], home=Path("/home/zephyrine"))
    assert redactor.redact("/home/zephyrine2/x") == "/home/zephyrine2/x"
    assert redactor.redact("/home/zephyrine.old/x") == "/home/zephyrine.old/x"
    assert redactor.redact("/home/zephyrine/x and more") == "~/x and more"


def test_redaction_reaches_a_fixed_point() -> None:
    redactor = Redactor.build(["Known12345"], home=Path(WIN_HOME))
    text = "\n".join(_spellings(WIN_HOME) + [f"sessionkey = '{_hex()}'", "pw Known12345"])
    once = redactor.redact(text)
    assert redactor.redact(once) == once


def test_fuzz_the_home_folder_over_random_names_and_spellings() -> None:
    rng = random.Random(595)
    alphabet = string.ascii_letters + string.digits + "_-. é@&()!+#"
    for _ in range(400):
        name = "".join(rng.choice(alphabet) for _ in range(rng.randint(3, 14))).strip(" .")
        if len(name) < 3 or not any(c.isalnum() for c in name):
            continue
        drive = rng.choice("CDE")
        home = f"{drive}:\\Users\\{name}"
        fwd = home.replace("\\", "/")
        form = rng.choice(
            [
                home,
                home.replace("\\", "\\\\"),
                home.replace("\\", "\\\\\\\\"),
                fwd,
                f"/mnt/{drive.lower()}/Users/{name}",
                quote(home, safe=""),
                quote(fwd, safe="/"),
                f"\\\\wsl.localhost\\Ubuntu\\mnt\\{drive.lower()}\\Users\\{name}",
                home.swapcase(),
            ]
        )
        sep = "\\\\" if "\\\\" in form else ("%5C" if "%5C" in form.upper() else "/")
        text = f"error at '{form}{sep}yulon-x{sep}file' now"
        out = Redactor.build([], home=Path(home)).redact(text)
        for needle in (name, quote(name, safe=""), name.swapcase()):
            if len(needle) >= 3 and needle.lower() in out.lower():
                # a name that is also a stretch of the fixed text is not a leak
                if needle.lower() in "error at  yulon-x file now users mnt wsl.localhost ubuntu":
                    continue
                pytest.fail(f"{name!r} survived in {out!r} (from {text!r})")
        assert "yulon-x" in out


# ---------------------------------------------------------------- fail closed, and the promise

TBC = load_catalog().get("wow-tbc")


def _sources(tmp_path: Path, log_text: str) -> Sources:
    log = tmp_path / "yulon.log"
    log.write_text(log_text, encoding="utf-8")
    return Sources(platform.config_dir(), log, ())


def _seams() -> bundle.Seams:
    return bundle.Seams(
        live_logs=lambda i, s: [],
        docker_version=lambda d: "27",
        now=lambda: datetime(2026, 10, 9, 10, 0, tzinfo=UTC),
    )


def _read(dest: Path) -> dict[str, str]:
    with zipfile.ZipFile(dest) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


class _Unstable(Redactor):
    """A redactor whose output still changes when it is redacted again: something got through."""

    def redact(self, text: str) -> str:
        return text + "x" if "UNSTABLE" in text else super().redact(text)


class _Breaks(Redactor):
    def redact(self, text: str) -> str:
        if "BREAKS" in text:
            raise RecursionError("a pattern gave up")
        return super().redact(text)


def _as(redactor: type[Redactor]) -> Redactor:
    base = Redactor.build([])
    return redactor(**{field.name: getattr(base, field.name) for field in fields(base)})


def test_checked_refuses_a_text_that_redacting_again_would_change() -> None:
    with pytest.raises(Unredactable):
        _as(_Unstable).checked("UNSTABLE")
    with pytest.raises(Unredactable):
        _as(_Breaks).checked("BREAKS")
    assert Redactor.build([]).checked("plain") == "plain"


@pytest.mark.parametrize("redactor", [_Unstable, _Breaks])
def test_a_file_the_redactor_cannot_vouch_for_is_left_out_and_named(
    tmp_path: Path, redactor: type[Redactor]
) -> None:
    word = redactor.__name__[1:].upper()
    sources = _sources(tmp_path, f"{word} raw line with a secret\n")
    dest = tmp_path / "s.zip"
    report = bundle.build(dest, sources, _as(redactor), seams=_seams())
    members = _read(dest)
    assert "app/yulon.log" not in members
    assert not any(word in text for text in members.values())
    assert any(name == "app/yulon.log" and "left out" in why for name, why in report.skipped)
    assert "app/yulon.log  " in members["MANIFEST.txt"]
    assert "system-info.txt" in members  # one bad file never kills the bundle


def test_the_manifest_promises_exactly_what_the_redactor_does(tmp_path: Path) -> None:
    sources = _sources(tmp_path, "hello\n")
    bundle.build(tmp_path / "s.zip", sources, Redactor.build([]), seams=_seams())
    manifest = _read(tmp_path / "s.zip")["MANIFEST.txt"]
    for promised in (
        "login session keys",
        "tokens",
        "your home folder",
        "in every spelling",
        "Account names",
        "left out",
    ):
        assert promised in manifest, promised


def test_a_whole_bundle_over_a_leaky_log_carries_no_key_and_no_escaped_home(
    tmp_path: Path,
) -> None:
    key = _hex()
    install_dir = tmp_path / "srv"
    (install_dir / "etc").mkdir(parents=True)
    (install_dir / "etc" / "mangosd.conf").write_text("SOAP.Enabled = 1\n", encoding="utf-8")
    facts = InstallFacts("wow-tbc", "0badc0de", install_dir, None, TBC)
    leak = (
        f"UPDATE account SET sessionkey = '{key}', last_ip = '1.2.3.4' WHERE username = 'ANN'\n"
        "[WinError 3] 'C:\\\\Users\\\\Zephyrine\\\\yulon-tortoise\\\\x'\n"
    )
    log = tmp_path / "yulon.log"
    log.write_text(leak, encoding="utf-8")
    sources = Sources(platform.config_dir(), log, (facts,))
    seams = bundle.Seams(
        live_logs=lambda i, s: [LiveLog("realmd", leak)],
        docker_version=lambda d: "27",
        now=lambda: datetime(2026, 10, 9, 10, 0, tzinfo=UTC),
    )
    redactor = Redactor.build([], home=Path(WIN_HOME))
    dest = tmp_path / "s.zip"
    bundle.build(dest, sources, redactor, seams=seams)
    for name, text in _read(dest).items():
        assert key not in text and key[-12:] not in text, name
        assert NAME not in text, name


def test_a_whole_bundle_over_encoded_homes_and_cookies_carries_none(tmp_path: Path) -> None:
    secret = "Zq" + secrets.token_hex(10) + "Wv"
    leak = (
        "GET C%3A%5CUsers%5C%5A%65phyrine%5Cx\n"
        "C:\\Users\\Other Person\\yulon\n"
        f"Set-Cookie: sessionid={secret}; Path=/\n"
    )
    log = tmp_path / "yulon.log"
    log.write_text(leak, encoding="utf-8")
    dest = tmp_path / "s.zip"
    redactor = Redactor.build([], home=Path(WIN_HOME))
    bundle.build(dest, Sources(platform.config_dir(), log, ()), redactor, seams=_seams())
    members = _read(dest)
    text = "\n".join(members.values())
    for needle in (secret, NAME, "Other", "Person", "%5A%65"):
        assert needle not in text, needle


def test_save_masks_the_home_given_and_the_one_the_install_folders_reveal(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "srv"
    (install_dir / "etc").mkdir(parents=True)
    log = tmp_path / "yulon.log"
    log.write_text("opened /home/penguin/yulon and C:\\\\Users\\\\Zephyrine\\\\x\n", "utf-8")
    facts = InstallFacts("wow-tbc", "0badc0de", Path("/home/penguin/yulon-tbc"), "Ubuntu", TBC)
    sources = Sources(platform.config_dir(), log, (facts,))
    dest = tmp_path / "s.zip"
    bundle.save(dest, sources, seams=_seams(), home=Path(WIN_HOME))
    text = _read(dest)["app/yulon.log"]
    assert "penguin" not in text and NAME not in text, text
