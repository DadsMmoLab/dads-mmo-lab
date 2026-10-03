"""The theme's ticks, radios and arrows, as the themed widgets draw them (T193).

Every test here renders a real widget under `apply_dadcraft_theme(holder,
width=...)` at the three window widths the launcher is checked at and reads
pixels out of the rectangles the style itself reports. Nothing compares a
constant with itself: the colours come from `theme.COLOR_*`, the geometry from
`style().subElementRect` / `subControlRect`, and the pixels from `grab()`.

The holder paints pure blue behind everything. Blue is a colour the theme never
uses, so a pixel is "the widget" exactly when it is not blue, and an edge
blended with blue can never pass for one of the theme's colours.
"""

from __future__ import annotations

import math
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, qInstallMessageHandler
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QVBoxLayout,
    QWidget,
)

from tests.conftest import process_events
from yulon import resources
from yulon.ui import theme

WIDTHS = (960, 1280, 1920)
"""The window widths of 960x640, 1280x800 and 1920x1080."""

THEME_IMAGES = (
    "check.svg",
    "check-disabled.svg",
    "radio-dot.svg",
    "radio-dot-disabled.svg",
    "arrow-down.svg",
    "arrow-down-disabled.svg",
    "minus.svg",
    "minus-disabled.svg",
    "plus.svg",
    "plus-disabled.svg",
)

_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")
_BACKDROP = "QWidget#theme-probe { background-color: #0000FF; }"
"""Test-only backdrop. Blue appears nowhere in the theme (asserted below)."""


def _theme_colours() -> set[str]:
    return {
        value.lower()
        for name, value in vars(theme).items()
        if name.startswith("COLOR_") and isinstance(value, str)
    }


def _near(colour: QColor, hex_value: str, tolerance: int = 24) -> bool:
    want = QColor(hex_value)
    return (
        abs(colour.red() - want.red()) <= tolerance
        and abs(colour.green() - want.green()) <= tolerance
        and abs(colour.blue() - want.blue()) <= tolerance
    )


def _is_backdrop(colour: QColor) -> bool:
    """More blue than anything else by over half the way: the backdrop, or mostly it."""
    return colour.blue() - max(colour.red(), colour.green()) >= 128


def _pixels(image: QImage, rect: QRect) -> Iterator[QColor]:
    for y in range(rect.top(), rect.bottom() + 1):
        for x in range(rect.left(), rect.right() + 1):
            yield image.pixelColor(x, y)


def _count(image: QImage, rect: QRect, test: Callable[[QColor], bool]) -> int:
    return sum(1 for colour in _pixels(image, rect) if test(colour))


@pytest.fixture
def themed(qapp: object) -> Iterator[Callable[..., QWidget]]:
    """Put widgets in a themed holder at a window width; tear the holders down after.

    The theme goes on an inner widget and the blue backdrop on the window around
    it: Qt keeps the first sheet's background decision when a second sheet is
    set on the same widget before it is shown (measured, PySide6 6.11), so the
    two are never stacked on one widget. The window takes the keyboard focus, so
    no indicator is drawn with its focus ring.
    """
    holders: list[QWidget] = []

    def make(width: int, *widgets: QWidget, qss: str | None = None) -> QWidget:
        holder = QWidget()
        holder.setObjectName("theme-probe")
        holder.setStyleSheet(_BACKDROP)
        holder.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        inner = QWidget(holder)
        QVBoxLayout(holder).addWidget(inner)
        layout = QVBoxLayout(inner)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        if qss is None:
            theme.apply_dadcraft_theme(inner, width=width)
        else:
            inner.setStyleSheet(qss)
        holder.resize(420, 60 + 70 * len(widgets))
        holder.show()
        holder.setFocus()
        process_events(30)
        holders.append(holder)
        return holder

    yield make
    for holder in holders:
        holder.close()
        holder.deleteLater()
    process_events(10)


def _indicator(button: QCheckBox | QRadioButton, holder: QWidget) -> tuple[QImage, QRect]:
    """The button's indicator rectangle, in the holder's grab."""
    option = QStyleOptionButton()
    button.initStyleOption(option)
    element = (
        QStyle.SubElement.SE_RadioButtonIndicator
        if isinstance(button, QRadioButton)
        else QStyle.SubElement.SE_CheckBoxIndicator
    )
    rect = button.style().subElementRect(element, option, button)
    rect.translate(button.mapTo(holder, QPoint(0, 0)))
    return holder.grab().toImage(), rect


def _tick_pixels(image: QImage, rect: QRect) -> tuple[int, int]:
    """(pixels of the tick colour, pixels of the amber fill) inside the indicator."""
    tick = _count(image, rect, lambda c: _near(c, theme.COLOR_BG_DARK))
    amber = _count(image, rect, lambda c: _near(c, theme.COLOR_GOLD_BRIGHT))
    return tick, amber


# --------------------------------------------------------------- checkboxes


@pytest.mark.parametrize("width", WIDTHS)
def test_a_ticked_checkbox_shows_a_tick_and_an_unticked_one_does_not(themed, width: int) -> None:
    """C16/A14: ticked used to be an amber square and nothing else.

    Mutation: drop the `image:` from the checked rule -- the box is all amber,
    no tick-coloured pixel is left, and the first assertion fails.
    """
    ticked, unticked = QCheckBox("Keep my characters"), QCheckBox("Keep my characters")
    ticked.setChecked(True)
    holder = themed(width, ticked, unticked)

    image, rect = _indicator(ticked, holder)
    tick, amber = _tick_pixels(image, rect)
    assert tick > 20, f"no tick drawn on the ticked box at {width}: {tick} tick px"
    assert amber > rect.width() * rect.height() / 2, f"no amber majority at {width}: {amber}"

    image, rect = _indicator(unticked, holder)
    _, amber = _tick_pixels(image, rect)
    assert amber == 0, f"an unticked box shows amber at {width}"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_disabled_ticked_checkbox_does_not_look_unticked(themed, width: int) -> None:
    """C16/A14: `:disabled` came after `:checked`, so a disabled ticked box looked empty.

    That is the uninstall dialog's "Keep my characters" while a job runs -- the
    one box whose state decides whether a database survives. The fix is the
    `:checked:disabled` rule, which wins on specificity (two pseudo-classes
    against one), not on where it sits in the sheet.

    Mutation: delete the `:checked:disabled` rule and the muted tick disappears
    again.
    """
    ticked, unticked = QCheckBox("Keep"), QCheckBox("Keep")
    ticked.setChecked(True)
    for box in (ticked, unticked):
        box.setEnabled(False)
    holder = themed(width, ticked, unticked)

    def muted(box: QCheckBox) -> int:
        image, rect = _indicator(box, holder)
        return _count(image, rect, lambda c: _near(c, theme.COLOR_TEXT_MUTED))

    assert muted(ticked) > 20, f"the disabled ticked box shows no tick at {width}"
    assert muted(unticked) == 0, f"the disabled unticked box shows a tick at {width}"


@pytest.mark.parametrize("width", WIDTHS)
def test_an_unticked_checkbox_border_is_the_muted_token(themed, width: int) -> None:
    """The rim of an empty box is `COLOR_TEXT_MUTED`, so it reads on the dark sheet."""
    box = QCheckBox("Keep")
    holder = themed(width, box)
    image, rect = _indicator(box, holder)
    mid_left = image.pixelColor(rect.left(), rect.center().y())
    assert mid_left.name() == QColor(theme.COLOR_TEXT_MUTED).name()


# ------------------------------------------------------------------- radios


@pytest.mark.parametrize("width", WIDTHS)
def test_a_radio_button_is_round(themed, width: int) -> None:
    """A12/B8: the radio's radius was `11*scale` on a 34-px box -- a rounded square.

    Mutation: put the radius back to `max(6, round(11 * scale))` and the drawn
    area is 16% over a disc's (at 960, the 8-px radius is 22% over).
    """
    lan, internet = QRadioButton("LAN"), QRadioButton("Internet")
    holder = themed(width, lan, internet)
    for button in (lan, internet):
        image, rect = _indicator(button, holder)
        assert rect.width() == rect.height() >= theme.TOUCH_TARGET_PX
        drawn = _count(image, rect, lambda c: not _is_backdrop(c))
        disc = math.pi * (rect.width() / 2) ** 2
        assert (
            abs(drawn - disc) / disc < 0.06
        ), f"{button.text()} at {width}: {drawn} px drawn, a disc is {disc:.0f}"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_checked_radio_has_a_gold_dot_and_an_unchecked_one_has_none(themed, width: int) -> None:
    """The checked radio is told apart by a dot at its centre, not by a filled square."""
    lan, internet = QRadioButton("LAN"), QRadioButton("Internet")
    internet.setChecked(True)
    holder = themed(width, lan, internet)

    image, rect = _indicator(internet, holder)
    assert _near(image.pixelColor(rect.center()), theme.COLOR_GOLD_BRIGHT), image.pixelColor(
        rect.center()
    ).name()
    # A dot, not a fill: a quarter of the way in from the rim is the dark well.
    quarter = image.pixelColor(rect.left() + rect.width() // 4, rect.center().y())
    assert _near(quarter, theme.COLOR_BG_INPUT), quarter.name()
    image, rect = _indicator(lan, holder)
    assert _count(image, rect, lambda c: _near(c, theme.COLOR_GOLD_BRIGHT)) == 0


@pytest.mark.parametrize("width", WIDTHS)
def test_a_disabled_checked_radio_keeps_a_muted_dot(themed, width: int) -> None:
    """The radio twin of the disabled-ticked checkbox."""
    lan, internet = QRadioButton("LAN"), QRadioButton("Internet")
    internet.setChecked(True)
    for button in (lan, internet):
        button.setEnabled(False)
    holder = themed(width, lan, internet)

    image, rect = _indicator(internet, holder)
    assert _near(image.pixelColor(rect.center()), theme.COLOR_TEXT_MUTED), image.pixelColor(
        rect.center()
    ).name()
    image, rect = _indicator(lan, holder)
    assert not _near(image.pixelColor(rect.center()), theme.COLOR_TEXT_MUTED)


# --------------------------------------------------------- spin and combo


def _spin_rects(spin: QSpinBox) -> tuple[QRect, QRect]:
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    control = QStyle.ComplexControl.CC_SpinBox
    up = spin.style().subControlRect(control, option, QStyle.SubControl.SC_SpinBoxUp, spin)
    down = spin.style().subControlRect(control, option, QStyle.SubControl.SC_SpinBoxDown, spin)
    return up, down


@pytest.mark.parametrize("width", WIDTHS)
def test_a_spin_box_has_two_pressable_buttons_with_a_visible_glyph(themed, width: int) -> None:
    """A11/B9/C17: the stacked halves were 16 px tall and drew a 1-px dash.

    Each button is a touch-sized square of its own, on either side of the
    number, and each shows its glyph in the body-text colour.

    Mutation: stack them again (both at the right) and they overlap the
    32-px floor or each other; drop the arrow images and the glyph count is 0.
    """
    spin = QSpinBox()
    spin.setRange(1, 80)
    spin.setValue(78)
    holder = themed(width, spin)
    image = spin.grab().toImage()
    up, down = _spin_rects(spin)
    edit = spin.lineEdit().geometry()

    for name, rect in (("up", up), ("down", down)):
        assert rect.width() >= theme.TOUCH_TARGET_PX, f"{name} {rect} at {width}"
        assert rect.height() >= theme.TOUCH_TARGET_PX, f"{name} {rect} at {width}"
        assert not rect.intersects(edit), f"{name} {rect} covers the number {edit}"
        glyph = _count(image, rect, lambda c: _near(c, theme.COLOR_TEXT_PRIMARY))
        assert glyph > 6, f"{name} shows no glyph at {width}: {glyph} px"
    assert not up.intersects(down)
    assert down.right() < edit.left() and up.left() > edit.right(), (down, edit, up)
    assert holder.isVisible()


@pytest.mark.parametrize("width", WIDTHS)
def test_a_disabled_spin_box_greys_its_glyphs(themed, width: int) -> None:
    spin = QSpinBox()
    spin.setRange(1, 80)
    spin.setValue(40)
    spin.setEnabled(False)
    themed(width, spin)
    image = spin.grab().toImage()
    for rect in _spin_rects(spin):
        assert _count(image, rect, lambda c: _near(c, theme.COLOR_TEXT_PRIMARY)) == 0
        assert _count(image, rect, lambda c: _near(c, theme.COLOR_TEXT_MUTED, 16)) > 6


@pytest.mark.parametrize("width", WIDTHS)
def test_a_combo_box_shows_its_arrow(themed, width: int) -> None:
    """A11/C17: `::drop-down` had no `::down-arrow`, so the box drew an empty well.

    Mutation: drop the `::down-arrow` rule and the arrow rectangle has no
    body-text pixel in it.
    """
    combo = QComboBox()
    combo.addItems(["Windowed", "Fullscreen"])
    themed(width, combo)
    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    arrow = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, combo
    )
    image = combo.grab().toImage()
    glyph = _count(image, arrow, lambda c: _near(c, theme.COLOR_TEXT_PRIMARY))
    assert glyph > 6, f"no arrow at {width}: {glyph} px in {arrow}"


# ------------------------------------------------------- tokens and shipping


def _rules(qss: str) -> dict[str, str]:
    """Selector text -> body, for every rule in a generated sheet."""
    out: dict[str, str] = {}
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", qss, flags=re.S)):
        out[" ".join(match.group(1).split())] = match.group(2)
    return out


T193_SELECTORS = (
    "QCheckBox::indicator, QRadioButton::indicator",
    "QRadioButton::indicator",
    "QCheckBox::indicator:checked, QRadioButton::indicator:checked",
    "QCheckBox::indicator:checked",
    "QRadioButton::indicator:checked",
    "QCheckBox::indicator:checked:disabled",
    "QRadioButton::indicator:checked:disabled",
    "QComboBox::down-arrow",
    "QComboBox::down-arrow:disabled",
    "QSpinBox, QDoubleSpinBox",
    "QSpinBox::up-button, QDoubleSpinBox::up-button",
    "QSpinBox::down-button, QDoubleSpinBox::down-button",
    "QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, "
    "QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover",
    "QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed, "
    "QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed",
    "QSpinBox::up-arrow, QDoubleSpinBox::up-arrow",
    "QSpinBox::down-arrow, QDoubleSpinBox::down-arrow",
    "QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled",
    "QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled",
)
"""Every rule T193 added or changed. Older literals elsewhere in the sheet stay."""


@pytest.mark.parametrize("width", WIDTHS)
def test_the_new_rules_and_images_use_only_theme_colours(width: int) -> None:
    """No new colour (T193 constraint): rules and SVGs draw from `COLOR_*` alone.

    Mutation: type any hex into one of the SVGs or one of these rules -- even one
    `theme.py` already uses as a bare literal -- and this names it.
    """
    rules = _rules(theme._build_qss(theme.scale_for_width(width)))
    missing = [s for s in T193_SELECTORS if s not in rules]
    assert not missing, missing
    used = {h.lower() for s in T193_SELECTORS for h in _HEX.findall(rules[s])}
    for name in THEME_IMAGES:
        used |= {h.lower() for h in _HEX.findall((resources.theme_images_dir() / name).read_text())}
    assert used, "nothing here paints"
    assert used <= _theme_colours(), sorted(used - _theme_colours())
    assert "#0000ff" not in _theme_colours(), "the test backdrop must stay a non-theme colour"


def test_every_image_the_sheet_names_ships_in_the_theme_images_dir() -> None:
    folder = resources.theme_images_dir()
    assert folder == resources.bundle_root() / "yulon" / "ui" / "theme_images"
    assert sorted(p.name for p in folder.glob("*.svg")) == sorted(THEME_IMAGES)
    named = set(re.findall(r'url\("([^"]+)"\)', theme._build_qss(1.0)))
    assert named == {(folder / name).as_posix() for name in THEME_IMAGES}, named


def _spec_datas(spec: Path, root: Path) -> list[tuple[Path, Path]]:
    """The `(source, destination)` pairs of the spec's own `datas = [...]` list.

    Read from the syntax tree and evaluated for the one call the list uses,
    `os.path.join`, with `ROOT` as the spec defines it (`pylauncher/`). A string
    grep would pass on a comment, or on a tuple whose two halves disagree.
    """
    import ast

    def value(node: ast.expr) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id == "ROOT":
            return str(root)
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "os.path.join"
            and not node.keywords
        ):
            return str(Path(*(value(arg) for arg in node.args)))
        raise AssertionError(f"unexpected spec expression: {ast.unparse(node)}")

    tree = ast.parse(spec.read_text())
    lists = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and [ast.unparse(t) for t in node.targets] == ["datas"]
        and isinstance(node.value, ast.List)
    ]
    assert len(lists) == 1, "the spec no longer starts `datas` as one list literal"
    pairs = []
    for item in lists[0].elts:
        assert isinstance(item, ast.Tuple) and len(item.elts) == 2, ast.unparse(item)
        pairs.append((Path(value(item.elts[0])), Path(value(item.elts[1]))))
    return pairs


def test_the_pyinstaller_spec_carries_the_theme_images() -> None:
    """A tick that draws from a checkout and not from a release is no tick at all.

    The spec lists its data by hand (see `tests/test_party.py`), so the folder
    has to be added by hand too -- from the folder `theme_images_dir()` reads in
    a checkout, to the place it reads in a frozen build.

    Mutation: delete the spec's `theme_images` tuple, or point either half of it
    anywhere else, and no pair matches.
    """
    pylauncher = Path(__file__).resolve().parents[1]
    pairs = _spec_datas(pylauncher / "build" / "pylauncher.spec", pylauncher)
    assert resources.bundle_root() == pylauncher  # a checkout, not a frozen build
    wanted = (
        resources.theme_images_dir(),
        resources.theme_images_dir().relative_to(resources.bundle_root()),
    )
    assert wanted in pairs, pairs


def test_the_images_load_from_a_folder_with_a_quote_and_spaces(
    themed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yu'lon installs under `...\\Yu'lon\\...`: an apostrophe and, often, spaces.

    The sheet writes `url("<posix path>")` quoted, so neither breaks the parser.
    Proved by pointing the theme at a copy in such a folder, applying it, and
    seeing the tick still drawn with no parse warning.
    """
    odd = tmp_path / "Yu'lon (x86) dir" / "theme images"
    shutil.copytree(resources.theme_images_dir(), odd)
    monkeypatch.setattr(resources, "theme_images_dir", lambda: odd)

    qss = theme._build_qss(1.0)
    assert odd.as_posix() in qss

    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _t, _c, msg: messages.append(msg))
    try:
        box = QCheckBox("Keep")
        box.setChecked(True)
        holder = themed(1280, box, qss=qss)
        image, rect = _indicator(box, holder)
    finally:
        qInstallMessageHandler(previous)

    complaints = [
        m for m in messages if "parse" in m.lower() or "stylesheet" in m.lower() or ".svg" in m
    ]
    assert complaints == [], complaints
    tick, _ = _tick_pixels(image, rect)
    assert tick > 20, f"the tick did not load from {odd}"


# ------------------------------------------------------------ destructive


def _luminance(colour: QColor) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.red())
        + 0.7152 * channel(colour.green())
        + 0.0722 * channel(colour.blue())
    )


def _contrast(a: QColor, b: QColor) -> float:
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _mid_left(button: QPushButton) -> QColor:
    image = button.grab().toImage()
    return image.pixelColor(0, image.height() // 2)


@pytest.mark.parametrize("width", WIDTHS)
def test_an_enabled_destructive_button_has_a_red_edge_a_disabled_one_does_not(
    themed, width: int
) -> None:
    """C22: the danger border was `#6A3034` on the pane -- 1.8:1, the look of `:disabled`.

    At rest an enabled Stop / Uninstall / Remove now carries `COLOR_DANGER`,
    over 3:1 against the pane (WCAG 1.4.11 for a control's edge), and a disabled
    one keeps the muted `COLOR_BRASS_DEEP`, so the two can be told apart.

    Mutation: put `#6A3034` back and the enabled edge is neither the token nor
    3:1.
    """
    live, dead = QPushButton("Stop"), QPushButton("Stop")
    for button in (live, dead):
        button.setProperty("danger", True)
    dead.setEnabled(False)
    themed(width, live, dead)

    edge = _mid_left(live)
    assert edge.name() == QColor(theme.COLOR_DANGER).name(), edge.name()
    ratio = _contrast(edge, QColor(theme.COLOR_BG_CONTAINER))
    assert ratio >= 3.0, f"the red edge is {ratio:.2f}:1 against the pane"
    assert _mid_left(dead).name() == QColor(theme.COLOR_BRASS_DEEP).name()


@pytest.mark.parametrize("enabled", [True, False])
def test_a_checked_box_or_radio_still_differs_when_its_image_cannot_load(
    themed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """The glyph is a file, and a file can be missing from a broken install (T193 review).

    With every image URL pointing at a folder that holds none, a checked box and
    a checked radio must still not look unchecked -- above all when disabled,
    where `:disabled` gives both states the same deep rim and the same well. The
    rim is what tells them apart: muted on a disabled checked one, gold on an
    enabled checked radio.

    Mutation: drop the `border-color` from either `:checked:disabled` rule and
    the disabled pair draws the same rim.
    """
    empty = tmp_path / "no images here"
    empty.mkdir()
    monkeypatch.setattr(resources, "theme_images_dir", lambda: empty)
    qss = theme._build_qss(1.0)
    assert empty.as_posix() in qss

    for kind in (QCheckBox, QRadioButton):
        checked, unchecked = kind("On"), kind("Off")
        checked.setChecked(True)
        for button in (checked, unchecked):
            button.setEnabled(enabled)
        holder = themed(1280, checked, unchecked, qss=qss)

        rims = []
        for button in (checked, unchecked):
            image, rect = _indicator(button, holder)
            rims.append(image.pixelColor(rect.left(), rect.center().y()).name())
        assert rims[0] != rims[1], (kind.__name__, enabled, rims)
