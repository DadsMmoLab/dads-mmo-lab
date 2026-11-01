# Project "Kael" — UI & Theme Architecture Redesign

> **Audience:** engineers and designers maintaining `pylauncher/yulon/ui/`.
> **Scope:** Comprehensive audit of the current PySide6 user interface against `pyplan/style-guide.md`, architectural restructuring of the theme engine, and a Steam Big Picture–inspired UI/UX redesign featuring a three-panel right-aligned module topbar (**Catalog**, **Server**, **Settings**), a two-stage boxart-driven Catalog drill-down with upper-left back navigation, and full controller/keyboard/mouse parity.
> **Precedence:** Subordinate to `pyplan/README.md` on system architecture and bound by all non-negotiable constraints in `pyplan/style-guide.md`.

---

## 1. Executive Summary & Vision

Yu'lon's user interface has grown organically from a four-tile WoW server installer into a multi-game platform supporting seven WoW cores (`wow-wotlk`, `wow-tbc`, `wow-vanilla`, `wow-tortoise`, `wow-centurion`, `wow-unbound`, `wow-coa`) and standalone server runtimes (`ddon`, `dfo`), alongside a 13-theme customization system (`yulon/ui/themes/*.json`) and an SDL2 gamepad navigation engine (`yulon/ui/gamepad.py`).

**Project Kael** realigns the UI and theme layers around three core goals:

1. **Structured Theme Architecture & Full Dark/Light Theme Parity**: Decompose the monolithic `yulon/ui/theme.py` (2,608 lines) into focused single-responsibility modules behind `theme.py`'s public facade, pin `BUNDLED_THEMES` to `yulon/ui/themes/*.json` by test, and fix the rendering bugs where dark themes bypass `ThemeSpec` tokens on catalog cards, tooltips render light-on-light text, and button icons stay hardcoded to Arthas colors.
2. **Steam Big Picture–Inspired Shell & Topbar**: Re-architect `DadcraftHeader` and `TopMenuBar` into a cohesive **Module Topbar** featuring the application title and active realm status (`DadcraftRealmBadge`) on the left, and **three prominent top-level module buttons aligned flush to the right**: **Catalog**, **Server**, and **Settings**.
3. **Two-Level Boxart Catalog with Drill-Down Navigation**: Transform the flat multi-row server grid into a two-stage experience:
   - **Stage 1 (Game Boxart Grid)**: Large, pleasant vertical "boxart" cards representing each supported game franchise (`WoW`, `DDON`, `DFO`, and future additions such as `RS`, `MS`, `MU`).
   - **Stage 2 (Game Server View)**: Clicking or confirming (`A` / `Enter`) a game's boxart replaces the panel with the servers available for that game, anchored by a prominent **Back button (`← Catalog`) in the upper-left corner** (paired with `B` / `Escape` controller back-stack support).

---

## 2. Audit of the Current UI & Theme Implementation (`style-guide.md` Review)

A deep code and test review of `pylauncher/main.py`, `pylauncher/yulon/ui/`, and `pylauncher/tests/` against `pyplan/style-guide.md` reveals concrete architectural, theme-rendering, typing, and UX defects that Kael resolves:

### 2.1 Strict Separation of Concerns (`style-guide.md` §1.1 & §3)

| Module | Current State | Violation / Technical Debt |
|---|---|---|
| `yulon/ui/theme.py` (2,608 lines) | Mixes `ThemeSpec` models, bundled theme baselines, JSON file I/O, temp-dir SVG tinting, a 1,200-line QSS string builder, and Qt spin-box/combo event filters (`_SpinNumberGuard`, `_SpinBoxWatcher`). | Violates single-job-per-module (§3). Token models/loaders (`theme_spec.py`), QSS generation (`theme_qss.py`), and Qt widget event filters (`theme_widgets.py`) belong in focused modules behind `theme.py`'s public facade. |
| `yulon/ui/widgets/dadcraft_decorations.py` (1,606 lines) | Houses `DadcraftRealmBadge`, `DadcraftHeader`, `_RealmTitle`, `DadcraftCampaignCard`, and `format_dadcraft_tooltip`, plus 7 theme-specific procedural `QPainter` backdrops. | Mixes 4 distinct widgets and tooltip HTML formatting in one file; each card runs its own independent 33ms `QTimer` even when scrolled out of view. |
| `yulon/ui/sidebar.py` (484 lines) & `main.py` | Retains `SidebarRail` and `SidebarPins` while `main.py` hides `tabs.tabBar()` and mounts `TopMenuBar` inside `DadcraftHeader`. `window.yulon_sidebar_pins` is aliased to `top_menu`. | Dual-navigation vestige: `QTabWidget` still acts as a hidden left-rail tab bar under the hood while `TopMenuBar` mutates tab visibility dynamically. |
| `yulon/ui/sidebar.py` (`TopMenuBar._sync`) | Dynamically adds and removes `SettingsView` as a hidden tab (`addTab` / `removeTab`) whenever the user enters or leaves Settings so bumper cycling skips it. | Fragile widget reparenting (`setParent(self._tabs.window())`) coupled to tab index math. |

### 2.2 Theme System Bugs & Code Smells Found in Current Code

1. **Dark-Theme Palette Bypass in `DadcraftCampaignCard.paintEvent` & Tile Button QSS (`dadcraft_decorations.py:1395–1606`, `theme.py:2319–2360`)**:
   - In `DadcraftCampaignCard.paintEvent`, `if spec.is_light:` reads `spec.bg_panel`, `spec.bg_container`, `spec.bg_dark`, `spec.border_dark`, and `spec.accent_bright`. However, when `spec.is_light` is `False` (all dark themes: `gruvbox-dark`, `dracula-dark`, `98x-dark`, `ocean-dark`, `jungle-dark`, `floral-dark`, and custom dark themes), `paintEvent` **completely ignores `spec`** and paints hardcoded `#101824` / `#162214` / `#1C1E24` / `#141F18` fills and hardcoded RGB borders.
   - Similarly, in `theme.py` (`lines 2319–2360`), `QFrame[objectName^="catalog-tile-"] QPushButton` hardcodes `"rgba(11, 17, 27, 0.86)"` and `"#E6F0F8"` (Arthas icy-blue colors) whenever `not spec.is_light`, forcing Arthas button colors onto Gruvbox Dark, Dracula Dark, and Floral Dark tiles.
   - Furthermore, `DadcraftCampaignCard` only branches on `"wow-wotlk"`, `"wow-tbc"`, and `"wow-vanilla"`, falling into a single `else:` branch for `"wow-tortoise"`, `"wow-centurion"`, `"wow-unbound"`, `"wow-coa"`, `"ddon"`, and `"dfo"`.
2. **Invisible Light-Theme Text in `format_dadcraft_tooltip()` (`dadcraft_decorations.py:42–90`)**:
   - `format_dadcraft_tooltip()` hardcodes module-level Arthas constants (`COLOR_TEXT_PRIMARY = "#E6F0F8"`, `"common": "#FFFFFF"`, `COLOR_GOLD_LIGHT = "#BDEEFF"`). Because `QToolTip` uses `spec.tooltip_bg` (which is near-white `#FFFBEB` / `#FBF1C7` on light themes like `dracula-light` and `gruvbox-light`), HTML tooltips render near-white text on a near-white background.
3. **Unthemed Static Button Icons (`catalog_view.py`, `sidebar.py`, `single_server_view.py`)**:
   - `CatalogView` (`catalog_view.py:553, 681, 697, 706`), `TopMenuBar` (`sidebar.py:312`), and `SingleServerView` pass hardcoded `#FFF1A8`, `COLOR_TEXT_GOLD`, or `COLOR_GOLD_BRIGHT` into `dadcraft_icon()` once during `__init__` and never re-tint those icons when the user switches themes in Settings.
4. **Unscaled Inline Font Stylesheets in `DadcraftHeader` & `DadcraftRealmBadge` (`dadcraft_decorations.py:209, 436–446`)**:
   - `DadcraftHeader.set_theme()` and `DadcraftRealmBadge._apply_status_style()` call `setStyleSheet(...)` with hardcoded pixel sizes (`font-size: 20px`, `font-size: 13px`, `font-size: 11px`), bypassing `theme.py`'s `scale_for_width(width)` responsive font scaling on window resize.
5. **Incomplete Card Repaint Discovery in `apply_dadcraft_theme()` (`theme.py:2604–2607`)**:
   - `apply_dadcraft_theme(target)` only calls `.update()` on child widgets whose `objectName().startswith("catalog-tile-")`. New custom-painted widgets (such as `GameBoxartCard`) will not repaint on direct `apply_dadcraft_theme(host, theme_name=...)` calls unless discovered there too.
6. **Right-Side Dead-Space Trap in `DadcraftHeader._row` (`dadcraft_decorations.py:390–405, 502–513`)**:
   - `DadcraftHeader._row` places `_realm_title` (`REALM_TITLE_SLOT_PX = 280` with `setRetainSizeWhenHidden(True)`) and `_badge` (`~130px` with `setRetainSizeWhenHidden(True)`) at the **right** end of the header row, and `header.add_action(top_menu)` inserts `top_menu` to the *left* of `_realm_title`. Even when `_realm_title` and `_badge` are hidden on the Catalog and Settings views, they reserve ~410px of empty space on the right side of the header, preventing `TopMenuBar` from ever aligning to the right edge opposite the title.

### 2.3 DRY — Single Source of Truth (`style-guide.md` §1.2 & §4)

1. **Bundled Theme Parity (`THEMES` in `theme.py` vs `yulon/ui/themes/*.json`)**:
   - All 13 bundled themes exist in `yulon/ui/themes/*.json` and in `THEMES` inside `theme.py`. Note that `parse_theme_dict()` (`theme.py:815`) intentionally uses `THEMES.get(theme_id, THEMES[DEFAULT_THEME])` so that if a user edits `<config_dir>/themes/gruvbox-dark.json` and overrides only a subset of keys (or has an invalid token rejected by `_SAFE_CSS_VALUE_RE`), missing keys fall back to `gruvbox-dark`'s own baseline rather than reverting to icy-blue `arthas`. `sync_user_themes()` also relies on `THEMES` when running in environments without bundled data files. Therefore, `BUNDLED_THEMES` must be retained in `yulon/ui/theme_spec.py` and **pinned byte-for-field equal to `yulon/ui/themes/*.json` by an automated test in `tests/test_theme.py`** so the two can never drift.
2. **Hardcoded Game Metadata in `catalog_view.py`**:
   - `_CAMPAIGN_GLYPHS`, `_CAMPAIGN_SUBTITLES`, and `_SERVER_SOFTWARE` are scattered module-level dicts in `yulon/ui/catalog_view.py` rather than a unified, typed catalog presentation table.

### 2.4 Call Down / Signal Up (`style-guide.md` §1.3 & §5)

- `SettingsView` (`yulon/ui/tray_settings.py`) holds a direct reference to `window: QWidget` (`self._window`) and queries `self._window.property("yulon_theme")` and `self._window.width()`. Under §5, `SettingsView` should receive the current theme via a method call (`set_current_theme(theme_id: str)`) and emit `theme_selected = Signal(str)` upward for the window to handle.
- `TopMenuBar` (`yulon/ui/sidebar.py`) reaches into child views via duck-typed `hasattr(view, "entry")` and `view.services.controller.server_dir` instead of reading a typed protocol/method interface.

### 2.5 Game Naming — Acronyms Only (`style-guide.md` §1.4 & §6)

- `yulon/ui/catalog_view.py` (`_CAMPAIGN_SUBTITLES` line 435: `"Turtle WoW Solo"`) and `yulon/ui/widgets/dadcraft_decorations.py` (line 1463 comment: `# Turtle WoW Mystic Jade & Forest`) violate `style-guide.md` §6 (`Never write: Tortoise WoW (Turtle-WoW solo fork) | Always write: WoW Tortoise (wow-tortoise, or Tortoise for short)`).
- User-facing first-run clarity in Stage 2 server descriptions may spell out the expansion once where helpful (`style-guide.md` §6 carve-out), while all Python identifiers, comments, logs, and Stage 1 Boxart titles must strictly use acronyms (`WoW`, `WoW WotLK`, `WoW TBC`, `WoW Vanilla`, `WoW Tortoise`, `DDON`, `DFO`, `RS`, `MS`, `MU`).

### 2.6 Static Typing Discipline (`style-guide.md` §1.6 & §2)

- `yulon/ui/sidebar.py`: `views: list[Any] = [self._tabs.widget(idx) for idx in indices]` uses `Any` where a `Protocol` (`ManagedServerView`) should be used.
- `yulon/ui/widgets/dadcraft_decorations.py`: `DadcraftCampaignCard._particles` is typed as `list[dict[str, float | int]]` with repeated `float(p["x"])` casts during `paintEvent` instead of a `@dataclass(slots=True) class _CardParticle` (mirroring `_FrostMote`).
- `yulon/ui/catalog_view.py`: `move_in: object | None = None` uses `object` instead of a `MoveInProtocol` under `TYPE_CHECKING`. Note: Qt `Signal(str, object, object)` on `CatalogView.installed` / `fresh_install` intentionally uses `object` for `Path | None` arguments because PySide6/Shiboken6 C++ signal marshaling converts `None` poorly when a slot signature is declared as `Signal(str, Path, Path)`; this PySide6 boundary reason should be documented inline rather than changed to `Signal(str, Path, Path)`.

---

## 3. UX & Visual Design Specification (Steam Big Picture Paradigm)

### 3.1 Layout Overview & Spatial Grid

The Kael shell is designed around a **10-foot / handheld-first spatial grid** (8px base unit) that scales cleanly from `MINIMUM_WINDOW_SIZE = (960, 640)` up to `DEFAULT_WINDOW_SIZE = (1280, 800)` (Steam Deck native) and 1080p/1440p/4K desktop monitors:

```text
+-----------------------------------------------------------------------------------+
|  [Emblem] Dad's MMO Lab                              [ Catalog ] [ Server ] [ Settings ] |
|           Yu'lon  •  [● REALM ONLINE: WotLK]                                      |
+-----------------------------------------------------------------------------------+
|  [Optional UpdateBar banner — only visible when an update or notice is active]    |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|                           ACTIVE TOP-LEVEL MODULE PANEL                           |
|                                                                                   |
|                        (Catalog  |  Server  |  Settings)                          |
|                                                                                   |
+-----------------------------------------------------------------------------------+
|  [LB / RB] Switch Module     [D-Pad] Navigate     [A] Select     [B] Back         |
+-----------------------------------------------------------------------------------+
```

### 3.2 Module Topbar (`DadcraftHeader` + Right-Aligned `TopMenuBar`)

Rather than breaking `window.property("header")` and `window.property("top_menu")` (which `main.py`, `test_main.py`, `test_theme.py`, and `main_window_fit_probe.py` rely on), Kael restructures `DadcraftHeader` and `TopMenuBar` so they work together as a unified, prominent horizontal module topbar (`height: 64px` at reference scale):

1. **Left Cluster (Identity & Live Status)**:
   - **Emblem & Title (`left_col`)**: Application emblem and bold title (`Dad's MMO Lab` / `Yu'lon`), left-aligned with `18px` outer margin (`0` stretch factor so it hugs its content instead of swallowing the row).
   - **Active Realm Status (`_realm_title` + `_badge`)**: Moved immediately beside the title cluster on the left side of the header (rather than the far right where their `setRetainSizeWhenHidden(True)` previously trapped ~410px of dead space). When a server is active or followed (`header.follow(badge, title=..., tooltip=...)`), its name and `DadcraftRealmBadge` appear right beside the brand title; when hidden, the central stretch absorbs the space cleanly without shifting the right-aligned module buttons.
2. **Center Stretch**: Flexible spacer (`self._row.addStretch(1)`) separating the left identity/status cluster from the right navigation cluster.
3. **Right Cluster (Three Top-Level Module Buttons in `TopMenuBar`)**:
   - Mounted at the **far right** of `DadcraftHeader._row` (`Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter`), opposite the title, with internal `QHBoxLayout` right-aligned (removing the trailing `row.addStretch(1)` that previously centered the buttons):
     1. **`Catalog` (`TOP_MENU_BUTTON`)** — Browse supported games by boxart and install or attach servers.
     2. **`Server` (`SERVERS_MENU_BUTTON`)** — Switch to the active installed server (or open the installed-servers selector when multiple servers exist).
     3. **`Settings` (`SETTINGS_BUTTON`)** — Theme selection, tray/startup preferences, app updates, and diagnostic logs.
   - **Visual Treatment & Theme Integration**:
     - Large pill/card-tab buttons (`min-height: 38px`, `padding: 8px 20px`, `font-size: _px(14, scale)`, `font-weight: bold`) styled entirely via `_build_qss(scale, theme_name)` using `spec.text_primary`, `spec.text_muted`, `spec.bg_hover`, `spec.accent_brass`, and `spec.accent_bright`.
     - Active module displays a luminous bottom accent bar (`border-bottom: 3px solid spec.accent_bright`), elevated surface fill (`spec.bg_hover`), and bright text (`spec.accent_bright`).
     - Preserves all 7 procedural theme animations in `DadcraftHeader.paintEvent` (`arthas`, `gruvbox`, `98x`, `ocean`, `floral`, `jungle`, `dracula`).

### 3.3 Panel 1: Catalog (Two-Stage Boxart → Server Drill-Down)

`main.build_catalog_tab(window, catalog_view, log_panel)` continues to wrap `catalog_view` (top pane) and `console_tabs` (`LogPanel`, bottom collapsible pane) inside `QSplitter#catalog-splitter`, preserving its `(tabs, update_bar, splitter)` return signature and ensuring an active install log stays visible even if the user navigates within the Catalog. Inside `CatalogView`, a two-stage `QStackedWidget` (`self._stack`) manages the drill-down hierarchy:

#### Stage 1: Game Boxart Grid (`CatalogBoxartGrid` — Stack Index 0)

```text
+-----------------------------------------------------------------------------------+
|  Choose a Game                                   [▶ Play on a friend's server…]  |
|  Select a game below to browse and install supported server emulators.            |
|                                                                                   |
|   +-------------------+     +-------------------+     +-------------------+       |
|   |                   |     |                   |     |                   |       |
|   |                   |     |                   |     |                   |       |
|   |        WoW        |     |       DDON        |     |        DFO        |       |
|   |                   |     |                   |     |                   |       |
|   |   [Boxart Art]    |     |   [Boxart Art]    |     |   [Boxart Art]    |       |
|   |                   |     |                   |     |                   |       |
|   |                   |     |                   |     |                   |       |
|   +-------------------+     +-------------------+     +-------------------+       |
|   | WoW               |     | DDON              |     | DFO               |       |
|   | 7 Servers • 1 Inst|     | 1 Server          |     | 1 Server (Beta)   |       |
|   +-------------------+     +-------------------+     +-------------------+       |
+-----------------------------------------------------------------------------------+
```

- **Boxart Cards (`GameBoxartCard`)**:
  - Vertical poster aspect ratio (~`2:3`, e.g., `220px × 300px` at `1280×800`, scaling down cleanly to a `180px × 240px` floor at `960×640`).
  - **Self-Contained Theme-Aware Vector Boxart**: Per `pyplan/README.md` §3a, `style-guide.md` §7, and `pyplan/write-ledger.md`, Yu'lon never bundles copyrighted game artwork and writes no untracked asset directories on disk. Each `GameBoxartCard` renders rich, self-contained vector/procedural cover art in `paintEvent` using `get_theme(resolve_widget_theme(self))` tokens (`spec.bg_panel`, `spec.bg_container`, `spec.bg_hover`, `spec.accent_bright`, `spec.accent_brass`, `spec.ember`, `spec.is_light`) combined with vector crests from `yulon/ui/icons.py`.
  - **Card Footer Metadata**:
    - Game acronym title (`WoW`, `DDON`, `DFO` — `style-guide.md` §6).
    - Subtitle badge showing supported server count (e.g., `7 Servers`) and an online-green pill (`● 1 Installed`, using `spec.uncommon`) when one or more servers in that group are installed.
  - **Interaction & Focus**:
    - Hovering or focusing via D-pad elevates the card with a `2px solid spec.accent_bright` outer border, `spec.bg_hover` surface lift, and `spec.accent_light` title highlight.
    - Clicking the card (or pressing `A` / `Enter` / `Space`) switches `CatalogView._stack` to **Stage 2** showing only the server tiles belonging to that `GameGroup`.

#### Stage 2: Game Server List View (`CatalogGameServersView` — Stack Index 1)

```text
+-----------------------------------------------------------------------------------+
|  [ ← Back to Catalog ]   WoW — Choose a Server Emulator     [▲ Show install log] |
|  Yu'lon downloads and builds the server for you. Nothing changes until Install.   |
+-----------------------------------------------------------------------------------+
|  +-----------------------------------+   +-----------------------------------+    |
|  | ❄️  WoW WotLK        [Recommended]|   | 🔥  WoW TBC                       |    |
|  |     3.3.5a (build 12340)          |   |     2.4.3 (build 8606)            |    |
|  |                                   |   |                                   |    |
|  | AzerothCore with mod-playerbots.  |   | CMaNGOS TBC with playerbots.      |    |
|  | The most complete server here.    |   |                                   |    |
|  |                                   |   |                                   |    |
|  | [  Install  ]  [ Use existing… ]  |   | [  Install  ]  [ Use existing… ]  |    |
|  | [ Bring from another computer… ]  |   | [ Bring from another computer… ]  |    |
|  +-----------------------------------+   +-----------------------------------+    |
+-----------------------------------------------------------------------------------+
```

- **Upper-Left Back Button (`QPushButton#catalog-back-button`)**:
  - Positioned in the **upper-left corner** of the Stage 2 header row (`← Back`).
  - Styled via QSS with `min-height: _touch(34, scale)`, `padding: 6px 16px`, and a theme-tinted left-arrow icon (`dadcraft_icon("arrow-left", ...)` / `icons.py`).
  - Clicking it (or pressing `B` / `Escape` / `Backspace` when no modal, popup menu, or `InputPrompter` is active) returns `CatalogView._stack` to Stage 1 (`CatalogBoxartGrid`) and restores focus to the `GameBoxartCard` that opened Stage 2.
- **Eager Tile Construction & Contract Preservation**:
  - All server tiles (`DadcraftCampaignCard` with `objectName="catalog-tile-<id>"`) and their action buttons (`install-<id>`, `existing-<id>`, `move-in-<id>`, `adopt-wsl-<id>`) are constructed **eagerly during `CatalogView.__init__`** inside Stage 2's scroll grid and filtered by visibility (`tile.setVisible(entry_group == active_group)`) when a `GameBoxartCard` is selected.
  - Why eager construction is mandatory: `test_main.py`, `test_catalog_view.py`, and `test_theme.py` query `catalog_view.button_for(game_id)` and `catalog_view.findChildren(QFrame)` immediately after `build_window()`, and `catalog_view.installed.emit(...)` updates tile states regardless of which Catalog stage is currently on screen.

### 3.4 Panel 2: Server (`ServerHubView`)

The **Server** topbar button opens the unified server management panel:

1. **Installed Server Switcher Bar (when 1+ servers are installed)**:
   - A clean horizontal sub-header bar at the top of the Server panel displaying:
     - **Server Selector Pill Strip / Dropdown**: Allows switching between installed servers (e.g., `WoW WotLK`, `WoW TBC`, `DDON`) with each server's live status dot (`server_tab_icon(status)`), plus quick `▶ Launcher` and `× Remove` actions.
     - **Single-Server Streamlining**: When only one server is installed (the common case for most players), its `ControllerView` or `SingleServerView` fills the panel immediately without redundant selector chrome.
2. **Embedded Controller Surface**:
   - Hosts the active server's `ControllerView` (with its **Server**, **Console**, **Accounts**, **Characters**, **Bots**, **Modules**, **Tuning**, and **Networking** sub-tabs) or `SingleServerView`.
3. **Zero-Servers Empty State**:
   - Instead of disabling the topbar `Server` button (which breaks predictable 3-stop `LB`/`RB` bumper cycling), clicking `Server` when no servers are installed displays a centered, friendly Big Picture empty-state card:
     - *"No servers installed yet"*
     - *"Pick a game from the Catalog to install a new server or attach an existing folder."*
     - Primary CTA button: **`Browse Catalog`** (switches directly to the Catalog panel).

### 3.5 Panel 3: Settings (`SettingsView`)

The **Settings** topbar button opens a permanent, first-class top-level panel (eliminating the transient tab insertion/removal hack in `sidebar.py`):

- Organized into clean, well-spaced cards inside a responsive single-column or two-column Big Picture layout (`max-width: 960px` centered container with generous `18px` card padding):
  1. **Appearance & Theme Card**:
     - `Color theme:` selector (`QComboBox#theme-selector`), live color swatch strip previewing the active theme's primary tokens (`bg_dark`, `bg_panel`, `accent_bright`, `uncommon`, `danger`), and **`Open themes folder…`** button (`open-themes-folder`).
  2. **System Tray & Startup Card**:
     - `TraySwitches` (`Keep running when Yu'lon is closed`, `Start the tray when I sign in`, and platform explanation note).
  3. **Application & Updates Card**:
     - Current version badge (`Yu'lon vX.Y.Z-Public`) and **`Check for updates`** button (`check-for-updates`).
  4. **Diagnostics & Logs Card (`LogsView`)**:
     - Integrates `LogsView` (redacted log viewer, `Copy last 200 lines`, `Save logs for support…`, `Open log folder`) as a dedicated sub-panel/tab within Settings so the topbar remains strictly three top-level buttons (**Catalog**, **Server**, **Settings**) while preserving 100% of `T93`'s support-bundle functionality and `_busy_reasons()` close guards.

---

## 4. Theme System Architecture & Redesign Integration

To align the theme system with `pyplan/style-guide.md` §1–§4 and ensure all 13 bundled themes (`arthas`, `gruvbox-dark`, `gruvbox-light`, `98x-light`, `98x-dark`, `ocean`, `ocean-dark`, `jungle`, `jungle-dark`, `floral`, `floral-dark`, `dracula-dark`, `dracula-light`) plus custom user `.json` themes render every Kael UI surface accurately, the theme layer is structured as follows:

```text
pylauncher/yulon/ui/
├── theme_spec.py        # ThemeSpec dataclass, BUNDLED_THEMES, JSON parser/validator, user theme sync
├── theme_qss.py         # _build_qss(scale, theme_name), _theme_images_dir_for(), width/touch scaling
├── theme_widgets.py     # Qt style event filters (_SpinNumberGuard, _SpinBoxWatcher)
├── theme.py             # Public facade re-exporting ThemeSpec, THEMES, get_theme, apply_dadcraft_theme
└── themes/              # Canonical JSON files for all 13 bundled themes
```

### 4.1 Zero Static-Default Token Drift in `ThemeSpec`

A critical pitfall in `@dataclass(frozen=True) class ThemeSpec` is adding new fields with static default hex values (which evaluate to `arthas` colors for any `.json` file that omits those fields, breaking light and warm themes). Instead, **Kael uses the existing 48-field `ThemeSpec` contract directly**:

- **Topbar (`DadcraftHeader` + `TopMenuBar`)**:
  - Backdrop gradient: `spec.header_bg_start` → `spec.header_bg_mid` → `spec.header_bg_end`
  - Top rim highlight & frame: `spec.header_top_highlight`, `spec.accent_brass`
  - Ambient animation: `spec.animation` (`arthas`, `gruvbox`, `98x`, `ocean`, `floral`, `jungle`, `dracula`) and `spec.is_light`
  - Right-aligned module buttons (`Catalog`, `Server`, `Settings`): `spec.text_muted` (inactive), `spec.bg_hover` + `spec.accent_light` (hover), `spec.accent_bright` bottom bar + text (active/checked), `spec.tab_disabled` (disabled).
- **Stage 1 `GameBoxartCard` & Stage 2 `DadcraftCampaignCard`**:
  - Base card gradient: `spec.bg_panel` → `spec.bg_container` → `spec.bg_dark` in **both** light and dark themes (fixing the defect where dark themes ignored `spec` and painted hardcoded Arthas/TBC hex values).
  - Atmospheric radial glow & motes: Tinted by `spec.accent_bright` / `spec.accent_light` / `spec.ember` blended with each game group's signature motif so switching from `arthas` to `gruvbox-dark`, `dracula-dark`, or `floral` visibly transforms both the Stage 1 boxart cards and Stage 2 server cards.
  - Card border & focus ring: `spec.border_dark` at rest, `spec.accent_brass` on hover, `2px solid spec.accent_bright` on D-pad `:focus`.
  - Card typography: `spec.tile_title`, `spec.tile_subtitle`, `spec.tile_desc`, `spec.tile_meta`, `spec.tile_warning`.
  - Card child buttons (`QFrame[objectName^="catalog-tile-"] QPushButton`): Styled with `spec.bg_panel`, `spec.text_primary`, `spec.border_dark`, and `spec.bg_hover` in both light and dark modes (removing the hardcoded `rgba(11, 17, 27, 0.86)` and `#E6F0F8` overrides).
- **Theme-Aware Tooltips & Dynamic Icon Re-Tinting**:
  - `format_dadcraft_tooltip()` accepts `theme_name: str | None = None`, resolves `spec = get_theme(theme_name)`, and formats HTML using `spec.text_primary`, `spec.text_gold`, `spec.border_dark`, `spec.uncommon`, `spec.rare`, and `spec.accent_bright` so light themes (`gruvbox-light`, `dracula-light`, `98x-light`, `ocean`, `floral`, `jungle`) have full WCAG contrast.
  - `CatalogView.apply_theme(theme_name)`, `TopMenuBar.apply_theme(theme_name)`, and `SingleServerView.apply_theme(theme_name)` re-tint all `dadcraft_icon()` button icons using `spec.primary_btn_text` (on primary buttons) and `spec.text_gold` / `spec.accent_bright` (on secondary/header buttons).
  - `apply_dadcraft_theme(target)` updates all `catalog-tile-*`, `boxart-card-*`, and `DadcraftRealmBadge` children so isolated `apply_dadcraft_theme(host, theme_name=...)` calls immediately repaint every custom widget.

---

## 5. Controller & Keyboard Navigation Specification (`gamepad.py`)

Steam Big Picture inspiration requires every screen, card, back transition, and topbar switch to feel effortless on a controller (Steam Deck / Xbox / DualSense via SDL2) as well as keyboard + mouse:

### 5.1 Top-Level Module Cycling (`LB` / `RB`)
- Pressing **`LB` (`Action.CYCLE_PREV`)** or **`RB` (`Action.CYCLE_NEXT`)** cycles across the active top-level views (`Catalog`, installed `Server` tabs, `Settings`) and synchronizes the three right-aligned `TopMenuBar` buttons (`Catalog`, `Server`, `Settings`).
- When inside a `ControllerView` whose sub-tab bar has focus, `LB` / `RB` and Left/Right arrow keys navigate naturally while Ctrl+Tab / Ctrl+Shift+Tab walk the top-level ring.

### 5.2 Hierarchical Back Navigation (`B` / `Escape` / `Backspace`) & Prompt Guard
`gamepad.py` extends `Navigator._back()` with an in-panel back-action step before `BACK_CLOSES`:

1. **Open Popup / Menu**: If a `QMenu` or combo popup is open, `B` closes the popup (existing behavior).
2. **Active Input Prompt Guard**: If an inline `InputPrompter` is visible inside `CatalogView` (e.g., asking for a sudo password during Docker setup), `B` / `Escape` cancels the prompt (`prompter.cancel()`) rather than navigating away from Stage 2 mid-prompt.
3. **Active Panel Back Stack (`BACK_ACTION_PROPERTY = "yulon_back_button"`)**: If the active page contains a visible, enabled button carrying `setProperty(BACK_ACTION_PROPERTY, True)` (specifically `CatalogView`'s upper-left `← Back` button when viewing Stage 2 `CatalogGameServersView`), `Navigator._back()` clicks that button to return to Stage 1 (`CatalogBoxartGrid`).
4. **Secondary Window / Dialog (`BACK_CLOSES`)**: If the active window carries `BACK_CLOSES` (`LauncherWindow`, `TraySettingsDialog`, `FittedMessageBox`), `B` closes the window (existing behavior).
5. **Main Window Root**: At the top-level root view (Stage 1 of Catalog, Server, or Settings), `B` is a safe no-op.

### 5.3 Cache Invalidation & Focus Memory Across `QStackedWidget` Pages
- **Preventing Stale Focus Caches in `Navigator`**:
  - `Navigator` in `yulon/ui/gamepad.py` caches `_iter_focusable(root)` in `self._cache` and records the last directional move in `self._last_move` (`_way_back`).
  - Whenever `CatalogView._stack` switches between Stage 1 (Boxart Grid) and Stage 2 (Game Server View), `Navigator.invalidate()` **must** clear both `self._cache` and `self._last_move = None`. Without clearing `_last_move`, pressing the opposite D-pad direction immediately after entering Stage 2 would attempt to jump back to the now-hidden Stage 1 boxart card.
- **Deterministic Focus Restoration**:
  - When drilling down from Stage 1 (`GameBoxartCard`) to Stage 2 (`CatalogGameServersView`), `CatalogView` records `self._active_group_id` and moves focus to the first visible primary action (`install-<id>`) or the upper-left `catalog-back-button`.
  - When returning from Stage 2 to Stage 1 (via clicking `catalog-back-button` or pressing `B`), `CatalogView` restores focus directly to `self._boxart_cards[last_group_id]`.

---

## 6. Component & Signal Contracts (`style-guide.md` §2, §3, §5)

Every new and refactored UI component obeys **Call Down / Signal Up** (`style-guide.md` §5), preserves existing `main.py` / test-suite attributes, and uses full static typing (`from __future__ import annotations`, zero `Any`).

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QWidget
from yulon.catalog.catalog import CatalogEntry, SingleEntry


@dataclass(frozen=True, slots=True)
class GameGroup:
    """One top-level game franchise card in the Stage 1 Catalog boxart grid."""

    id: str  # "wow" | "ddon" | "dfo"
    title: str  # Acronym only: "WoW" | "DDON" | "DFO" (style-guide.md §6)
    subtitle: str  # Short edition summary, e.g. "7 Server Cores"
    glyph: str  # Vector icon name in `yulon/ui/icons.py`
    entries: tuple[CatalogEntry | SingleEntry, ...]


class ManagedServerView(Protocol):
    """Typed contract for a server management tab (`ControllerView` | `SingleServerView`)."""

    @property
    def entry(self) -> CatalogEntry | SingleEntry: ...


class GameBoxartCard(QPushButton):
    """Focusable 2:3 vertical boxart card for Stage 1 of `CatalogView` (`boxart-card-<id>`)."""

    group_selected = Signal(str)  # emits `GameGroup.id`

    def set_server_counts(self, total: int, installed: int) -> None: ...
    def apply_theme(self, theme_name: str) -> None: ...


class CatalogView(QWidget):
    """Two-stage stacked Catalog: Stage 0 = Boxart Grid, Stage 1 = Game Server List."""

    # Signals match existing `main.py` & `test_main.py` contracts; `object` is used for
    # `Path | None` slots because Shiboken6 C++ signal marshaling rejects `None` on `Path`.
    install_started = Signal(str)
    install_finished = Signal(str, bool, str)
    installed = Signal(str, object, object)
    fresh_install = Signal(str, object, object)
    adopted = Signal(str, object, object, object)
    open_server = Signal(str, object)
    stage_changed = Signal(int)  # 0 = boxart grid, 1 = game server list (invalidates Navigator)

    def show_boxart_grid(self) -> None: ...
    def show_game_servers(self, group_id: str) -> None: ...
    def apply_theme(self, theme_name: str) -> None: ...
```

### 6.1 Spacing, Typography & Visual Hierarchy Tokens

| Surface / Element | Token / Property | Reference (`1280×800`) | Minimum (`960×640`) | Rationale |
|---|---|---|---|---|
| **Module Topbar (`DadcraftHeader`)** | Height / Outer Margin | `64px` / `0 18px` | `54px` / `0 12px` | Left-aligns title + realm badge; right-aligns **Catalog**, **Server**, and **Settings** buttons with zero right-side dead space. |
| **Topbar Module Buttons** | Min-Height / Padding / Gap | `38px` / `8px 20px` / `8px` | `34px` / `6px 14px` / `6px` | Exceeds `TOUCH_TARGET_PX` (`32px`) and clearly separates **Catalog**, **Server**, and **Settings**. |
| **Panel Content Viewport** | Outer Margin / Section Gap | `16px 20px` / `14px` | `10px 14px` / `10px` | Gives Big Picture breathing room around cards while preserving vertical budget at `640px` height (`T85` floor). |
| **Stage 1 `GameBoxartCard`** | Card Size / Grid Spacing | `220×300px` / `18px` | `180×240px` / `12px` | Pleasant 2:3 vertical boxart proportions; fits 3–4 across comfortably with zero horizontal scrolling. |
| **Stage 2 Back Button** | Position / Min-Height / Padding | Upper-left / `34px` / `6px 14px` | Upper-left / `32px` / `5px 12px` | Consistent upper-left return anchor (`← Back`), paired with `B` / `Escape` controller navigation. |
| **Stage 2 Server Cards** | Inner Padding / Grid Gap | `14px` / `12px` | `12px` / `10px` | Preserves `_CATALOG_MIN_WIDTH` (`420px`) and ensures `Install` buttons never clip at `960×640`. |

---

## 7. Phased Implementation Plan

To keep every commit small, verifiable, and green across `pytest -m "not integration"`, `mypy yulon main.py`, `ruff check .`, and `black --check .`, implementation is divided into five sequential phases:

### Phase K1 — Theme System Fixes, Decomposition & Style-Guide Cleanup
1. **Fix Dark-Theme Card & Tooltip Rendering (`dadcraft_decorations.py` & `theme.py`)**:
   - Update `DadcraftCampaignCard.paintEvent` so dark themes (`not spec.is_light`) derive their base surface, border, and hover colors from `spec` (`spec.bg_panel`, `spec.bg_container`, `spec.bg_dark`, `spec.border_dark`, `spec.accent_bright`) instead of hardcoded Arthas/TBC hex values.
   - Update `QFrame[objectName^="catalog-tile-"] QPushButton` in `theme.py` to use `spec.bg_panel`, `spec.text_primary`, `spec.border_dark`, and `spec.bg_hover` in both dark and light themes.
   - Update `format_dadcraft_tooltip()` to resolve `spec = get_theme(theme_name)` so light themes render dark text (`spec.text_primary`) on light tooltip backgrounds (`spec.tooltip_bg`).
   - Add `apply_theme(theme_name: str)` to `CatalogView`, `TopMenuBar`, and `SingleServerView` to re-tint `dadcraft_icon()` icons whenever the theme changes.
2. **Modularize Theme Internals & Enforce Parity (`yulon/ui/theme_spec.py`, `theme_widgets.py`, `theme.py`)**:
   - Extract `ThemeSpec`, `BUNDLED_THEMES`, JSON loading/validation, and `sync_user_themes()` into `theme_spec.py`, and `_SpinNumberGuard` / `_SpinBoxWatcher` into `theme_widgets.py`, keeping `theme.py` as the public facade.
   - Add a test in `tests/test_theme.py` asserting that every `.json` file in `yulon/ui/themes/` matches its corresponding entry in `BUNDLED_THEMES`.
3. **Type & Acronym Audit (`style-guide.md` §2 & §6)**:
   - Replace `_particles: list[dict[str, float | int]]` in `dadcraft_decorations.py` with `@dataclass(slots=True) class _CardParticle`.
   - Replace `list[Any]` in `sidebar.py` with `ManagedServerView` `Protocol`, and `move_in: object | None` in `catalog_view.py` with a typed `Protocol`.
   - Replace non-acronym `"Turtle WoW"` strings in `catalog_view.py` and `dadcraft_decorations.py` with `WoW Tortoise` (`style-guide.md` §6).

### Phase K2 — Game Grouping & Two-Stage Boxart Catalog (`catalog_view.py`)
1. **Catalog Game Grouping Model (`GameGroup`)**:
   - Group `Catalog.games` and `Catalog.single` into typed franchise groups:
     - `wow` (**WoW**): `wow-wotlk`, `wow-tbc`, `wow-vanilla`, `wow-tortoise`, `wow-centurion`, `wow-unbound`, `wow-coa`
     - `ddon` (**DDON**): `ddon`
     - `dfo` (**DFO**): `dfo`
2. **`GameBoxartCard` Widget (`yulon/ui/widgets/boxart_card.py`)**:
   - Implement a keyboard/controller-focusable vertical boxart card (`objectName="boxart-card-<id>"`) painted with active `ThemeSpec` tokens, vector crest iconography (`icons.py`), acronym title, server count badge, and installed indicator pill.
3. **Two-Stage `CatalogView` Stack with Upper-Left Back Navigation**:
   - Build a two-page `QStackedWidget` inside `CatalogView`:
     - **Stage 0 (`CatalogBoxartGrid`)**: Responsive grid of `GameBoxartCard`s.
     - **Stage 1 (`CatalogGameServersView`)**: Upper-left `← Back` button (`QPushButton#catalog-back-button`, carrying `BACK_ACTION_PROPERTY`), game group heading, and the eagerly constructed server tiles filtered to the chosen `GameGroup`.
   - Keep `QSplitter#catalog-splitter` and `LogPanel` in `main.build_catalog_tab()` below `CatalogView` so install logs remain visible across both stages.

### Phase K3 — Right-Aligned Three-Module Topbar (`DadcraftHeader` + `TopMenuBar`)
1. **Right-Align Top-Level Module Buttons Opposite the Title (`dadcraft_decorations.py` & `sidebar.py`)**:
   - Move `_realm_title` and `_badge` into the left cluster of `DadcraftHeader._row` (right beside the title/subtitle stack), followed by a central stretch (`addStretch(1)`), and place `TopMenuBar` flush on the **right** side of `DadcraftHeader._row`.
   - Present the three prominent top-level module buttons on the right of `TopMenuBar`: **Catalog**, **Server**, and **Settings**. (the launcher client play patch and log buttons remain accessible but are not part of the main top-level module buttons, they should be positioned elsewhere in the UI, separate from the main top-level module buttons, likely within bottom-right corner controls or a secondary toolbar). Each button should have an icon representing its function for quick visual identification.
2. **Permanent Settings & Diagnostics Integration (`main.py` & `tray_settings.py`)**:
   - Make `SettingsView` a permanent page in the window's view stack (eliminating the fragile dynamic `addTab`/`removeTab` reparenting in `TopMenuBar._sync`), and refactor `SettingsView` to emit `theme_selected = Signal(str)` instead of holding a parent `self._window` reference (`style-guide.md` §5).
   - Provide access to `LogsView` (`window.yulon_logs_view`) and `FriendLauncher` (`window.yulon_show_friend_launcher`) cleanly without cluttering the three-button module topbar, while preserving all `test_main.py` and `main_window_fit_probe.py` hooks.

### Phase K4 — Steam Big Picture Spacing, Visual Hierarchy & Polish Pass
1. **Spacing & Typography Refinement (`theme.py` / `theme_qss.py`)**:
   - Move `DadcraftHeader` and `DadcraftRealmBadge` font sizing under `_px(..., scale)` so header typography scales smoothly with window width.
   - Standardize panel margins, card padding, grid spacing, and `:focus` / `:hover` border highlights across all 13 themes.
2. **Viewport Fit Verification**:
   - Verify every panel, Stage 1 boxart card, Stage 2 server card, and topbar button across `960×640`, `1280×800`, and `1920×1080` using `main_window_fit_probe.py` and `test_catalog_view.py`.

### Phase K5 — Controller Navigation & Back-Stack Parity (`gamepad.py`)
1. **View-Stack Back Action & Cache Invalidation (`Navigator`)**:
   - Update `Navigator.invalidate()` in `yulon/ui/gamepad.py` to reset `self._last_move = None` alongside `self._cache.clear()`, and connect `CatalogView.stage_changed` to `navigator.invalidate()`.
   - Extend `Navigator._back()` to check for an active `InputPrompter` first, then trigger any visible `BACK_ACTION_PROPERTY` button (`catalog-back-button` in Stage 2) before checking `BACK_CLOSES`.
2. **Test Suite Verification**:
   - Extend `tests/test_catalog_view.py`, `tests/test_sidebar.py`, `tests/test_gamepad_keyboard.py`, `tests/test_theme.py`, and `tests/test_theme_widgets.py` to verify dark/light theme parity on boxart and server cards, right-aligned 3-button topbar geometry, Stage 1 → Stage 2 boxart drill-down, upper-left back button return, and `B`-button focus restoration.
