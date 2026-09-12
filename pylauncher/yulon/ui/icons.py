"""FontAwesome-compatible SVG icons and Dadcraft-tinted QIcon generators (PySide6).

Provides crisp, scalable vector icons rendered directly via QPainter / QSvgRenderer
without any external web or font runtime dependencies:
- Navigation & action icons: server, play, stop, refresh, terminal, users, robot,
  modules, network, database, trash, wrench, copy, folder, steam, download, etc.
- Custom Dadcraft color tinting (gold, brass, fel green, arcane blue, crimson red, silver).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from yulon.ui.theme import (
    COLOR_GOLD_BRIGHT,
    COLOR_GOLD_LIGHT,
    COLOR_RARE,
    COLOR_TEXT_GOLD,
    COLOR_UNCOMMON,
)

# Standard FontAwesome 6 SVG path definitions (viewBox="0 0 512 512" or "0 0 640 512")
_FA_PATHS: dict[str, tuple[str, int, int]] = {
    # (svg_path_data, viewBox_width, viewBox_height)
    "server": (
        "M64 32C28.7 32 0 60.7 0 96l0 64c0 35.3 28.7 64 64 64l384 0c35.3 0 "
        "64-28.7 64-64l0-64c0-35.3-28.7-64-64-64L64 32zm280 72a24 24 0 1 1 0 "
        "48 24 24 0 1 1 0-48zm48 24a24 24 0 1 1 48 0 24 24 0 1 1 -48 0zM64 "
        "288c-35.3 0-64 28.7-64 64l0 64c0 35.3 28.7 64 64 64l384 0c35.3 0 "
        "64-28.7 64-64l0-64c0-35.3-28.7-64-64-64L64 288zm280 72a24 24 0 1 1 0 "
        "48 24 24 0 1 1 0-48zm48 24a24 24 0 1 1 48 0 24 24 0 1 1 -48 0z",
        512,
        512,
    ),
    "play": (
        "M73 39c-14.8-9.1-33.4-9.4-48.5-.9S0 62.6 0 80L0 432c0 17.4 9.4 33.4 "
        "24.5 41.9s33.7 8.1 48.5-.9L361 297c14.3-8.7 23-24.2 23-41s-8.7-32.2-23-41L73 39z",
        384,
        512,
    ),
    "stop": (
        "M0 96C0 60.7 28.7 32 64 32l256 0c35.3 0 64 28.7 64 64l0 256c0 35.3-28.7 "
        "64-64 64L64 416c-35.3 0-64-28.7-64-64L0 96z",
        384,
        512,
    ),
    "refresh": (
        "M105.1 202.6c7.7-21.8 20.2-42.3 37.8-59.8c62.5-62.5 163.8-62.5 226.3 0L386.3 "
        "160 304 160c-17.7 0-32 14.3-32 32s14.3 32 32 32l144 0c17.7 0 32-14.3 "
        "32-32l0-144c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 62.4c-83-75.9-211-73.4-290.7 "
        "6.3c-23.7 23.7-40.4 51.5-50.6 81.4c-5.8 16.7 3 35.1 19.8 40.9s35.1-3 40.9-19.8zM406.9 "
        "309.4c-7.7 21.8-20.2 42.3-37.8 59.8c-62.5 62.5-163.8 62.5-226.3 0l-17.1-17.1L208 "
        "352c17.7 0 32-14.3 32-32s-14.3-32-32-32L64 288c-17.7 0-32 14.3-32 32l0 "
        "144c0 17.7 14.3 32 32 32s32-14.3 32-32l0-62.4c83 75.9 211 73.4 290.7-6.3c23.7-23.7 "
        "40.4-51.5 50.6-81.4c5.8-16.7-3-35.1-19.8-40.9s-35.1 3-40.9 19.8z",
        512,
        512,
    ),
    "terminal": (
        "M0 96C0 60.7 28.7 32 64 32l384 0c35.3 0 64 28.7 64 64l0 320c0 35.3-28.7 "
        "64-64 64L64 480c-35.3 0-64-28.7-64-64L0 96zM137 185c-9.4-9.4-24.6-9.4-33.9 "
        "0s-9.4 24.6 0 33.9l55 55-55 55c-9.4 9.4-9.4 24.6 0 33.9s24.6 9.4 33.9 0l72-72c9.4-9.4 "
        "9.4-24.6 0-33.9l-72-72zm119 151l112 0c13.3 0 24-10.7 24-24s-10.7-24-24-24l-112 "
        "0c-13.3 0-24 10.7-24 24s10.7 24 24 24z",
        512,
        512,
    ),
    "users": (
        "M96 128a128 128 0 1 1 256 0A128 128 0 1 1 96 128zM0 480c0-53 43-96 96-96l256 "
        "0c53 0 96 43 96 96c0 17.7-14.3 32-32 32L32 512c-17.7 0-32-14.3-32-32z",
        448,
        512,
    ),
    "robot": (
        "M224 0c-17.7 0-32 14.3-32 32l0 19.2C119 61.6 64 124.4 64 200l0 160c0 79.5 "
        "64.5 144 144 144l96 0c79.5 0 144-64.5 144-144l0-160c0-75.6-55-138.4-128-148.8L320 "
        "32c0-17.7-14.3-32-32-32l-64 0zm-32 208a32 32 0 1 1 0 64 32 32 0 1 1 0-64zm160 "
        "32a32 32 0 1 1 64 0 32 32 0 1 1 -64 0zM192 384l128 0c17.7 0 32 14.3 32 32s-14.3 "
        "32-32 32l-128 0c-17.7 0-32-14.3-32-32s14.3-32 32-32z",
        512,
        512,
    ),
    "puzzle": (
        "M176 0c17.7 0 32 14.3 32 32l0 16c26.5 0 48 21.5 48 48s-21.5 48-48 48l0 "
        "16c0 17.7-14.3 32-32 32l-144 0C14.3 192 0 177.7 0 160L0 32C0 14.3 14.3 0 32 "
        "0l144 0zM352 96c17.7 0 32 14.3 32 32l0 144c0 17.7-14.3 32-32 32l-16 0c0 "
        "26.5-21.5 48-48 48s-48-21.5-48-48l-16 0c-17.7 0-32-14.3-32-32l0-144c0-17.7 "
        "14.3-32 32-32l144 0zM64 256c17.7 0 32 14.3 32 32l0 16c26.5 0 48 21.5 48 48s-21.5 "
        "48-48 48l0 16c0 17.7-14.3 32-32 32l-32 0c-17.7 0-32-14.3-32-32l0-128c0-17.7 "
        "14.3-32 32-32l32 0z",
        384,
        512,
    ),
    "network": (
        "M64 32C28.7 32 0 60.7 0 96l0 128c0 35.3 28.7 64 64 64l160 0 0 64-96 0c-17.7 "
        "0-32 14.3-32 32s14.3 32 32 32l96 0 0 32c0 17.7 14.3 32 32 32s32-14.3 32-32l0-32 "
        "96 0c17.7 0 32-14.3 32-32s-14.3-32-32-32l-96 0 0-64 160 0c35.3 0 64-28.7 64-64l0-128"
        "c0-35.3-28.7-64-64-64L64 32zm0 64l384 0 0 128L64 224 64 96z",
        512,
        512,
    ),
    "database": (
        "M448 80c0-44.2-100.3-80-224-80S0 35.8 0 80l0 96c0 44.2 100.3 80 224 80s224-35.8 "
        "224-80l0-96zm0 160c0 44.2-100.3 80-224 80S0 284.2 0 240l0 96c0 44.2 100.3 80 "
        "224 80s224-35.8 224-80l0-96zm0 160c0 44.2-100.3 80-224 80S0 444.2 0 400l0 32c0 "
        "44.2 100.3 80 224 80s224-35.8 224-80l0-32z",
        448,
        512,
    ),
    "trash": (
        "M135.2 17.7L128 32 32 32C14.3 32 0 46.3 0 64S14.3 96 32 96l384 0c17.7 0 "
        "32-14.3 32-32s-14.3-32-32-32l-96 0-7.2-14.3C307.4 6.8 296.3 0 284.2 0L163.8 "
        "0c-12.1 0-23.2 6.8-28.6 17.7zM416 128L32 128 53.2 467c1.6 25.3 22.6 45 47.9 "
        "45l245.8 0c25.3 0 46.3-19.7 47.9-45L416 128z",
        448,
        512,
    ),
    "wrench": (
        "M1 127.1C-3.4 186.4 17.7 246.3 64 288L64 448c0 35.3 28.7 64 64 64l64 0c35.3 "
        "0 64-28.7 64-64l0-160c46.3-41.7 67.4-101.6 63-160.9L242 204.1c-9.4 9.4-24.6 "
        "9.4-33.9 0l-60.2-60.2c-9.4-9.4-9.4-24.6 0-33.9L224.9 33C165.6 28.6 105.7 49.7 "
        "64 96l63 63c9.4 9.4 9.4 24.6 0 33.9s-24.6 9.4-33.9 0L29.1 129.9 1 127.1z",
        512,
        512,
    ),
    "copy": (
        "M288 448L64 448c-35.3 0-64-28.7-64-64L0 64C0 28.7 28.7 0 64 0l224 0c35.3 0 "
        "64 28.7 64 64l0 320c0 35.3-28.7 64-64 64zm64-320l0 288c0 53-43 96-96 96L96 "
        "512c-17.7 0-32-14.3-32-32s14.3-32 32-32l160 0c17.7 0 32-14.3 32-32l0-288c0-17.7 "
        "14.3-32 32-32s32 14.3 32 32z",
        448,
        512,
    ),
    "folder": (
        "M64 480c-35.3 0-64-28.7-64-64L0 96C0 60.7 28.7 32 64 32l128 0c20.1 0 39.1 "
        "9.5 51.2 25.6l19.2 25.6c4 5.3 10.4 8.5 17.1 8.5l168.5 0c35.3 0 64 28.7 64 "
        "64l0 224c0 35.3-28.7 64-64 64L64 480z",
        512,
        512,
    ),
    "download": (
        "M288 32c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 242.7-73.4-73.4c-12.5-12.5-32.8-12.5-45.3 "
        "0s-12.5 32.8 0 45.3l128 128c12.5 12.5 32.8 12.5 45.3 0l128-128c12.5-12.5 12.5-32.8 "
        "0-45.3s-32.8-12.5-45.3 0L288 274.7 288 32zM64 352c-17.7 0-32 14.3-32 32l0 "
        "64c0 35.3 28.7 64 64 64l320 0c35.3 0 64-28.7 64-64l0-64c0-17.7-14.3-32-32-32s-32 "
        "14.3-32 32l0 64c0 0 0 0 0 0l-320 0c0 0 0 0 0 0l0-64c0-17.7-14.3-32-32-32z",
        512,
        512,
    ),
    "steam": (
        "M256 0C114.6 0 0 114.6 0 256c0 102.5 60.4 190.9 147.6 231.2l61.5-89.4C198.5 "
        "385.6 192 369.6 192 352c0-35.3 28.7-64 64-64 4.8 0 9.4 .6 13.9 1.6l49.9-72.6C302.3 "
        "203.4 288 181.2 288 156c0-44.2 35.8-80 80-80s80 35.8 80 80-35.8 80-80 80c-7.3 "
        "0-14.2-1-20.9-2.8l-49.8 72.4c17.5 14.1 28.7 35.6 28.7 59.7 0 41.8-33.9 75.7-75.7 "
        "75.7-27.4 0-51.5-14.6-64.7-36.4L105 487.6C148.9 503.2 201.4 512 256 512c141.4 "
        "0 256-114.6 256-256S397.4 0 256 0z",
        512,
        512,
    ),
    "sword": (
        "M490.7 21.3C476.3 6.9 453.7 6.9 439.3 21.3L277.5 183.1 234.3 139.9c-12.5-12.5"
        "-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l43.2 43.2L35.6 425c-12.5 12.5-12.5 32.8 "
        "0 45.3l6.1 6.1c12.5 12.5 32.8 12.5 45.3 0l196.6-196.6 43.2 43.2c12.5 12.5 32.8 "
        "12.5 45.3 0s12.5-32.8 0-45.3l-43.2-43.2L490.7 72.7c14.4-14.4 14.4-37 0-51.4z",
        512,
        512,
    ),
    "skull": (
        "M416 398.9C474.3 356.1 512 286.7 512 208C512 93.1 397.7 0 256 0S0 93.1 0 "
        "208c0 78.7 37.7 148.1 96 190.9l0 57.1c0 17.7 14.3 32 32 32l32 0c17.7 0 32-14.3 "
        "32-32l0-16 64 0 0 16c0 17.7 14.3 32 32 32l32 0c17.7 0 32-14.3 32-32l0-57.1zM144 "
        "160a48 48 0 1 1 96 0 48 48 0 1 1 -96 0zm176 48a48 48 0 1 1 0-96 48 48 0 1 1 "
        "0 96zM256 352a32 32 0 1 1 0-64 32 32 0 1 1 0 64z",
        512,
        512,
    ),
    "teleport": (
        "M256 0c141.4 0 256 114.6 256 256S397.4 512 256 512 0 397.4 0 256 114.6 0 "
        "256 0zm0 96c-88.4 0-160 71.6-160 160s71.6 160 160 160 160-71.6 160-160S344.4 "
        "96 256 96zm0 64a96 96 0 1 1 0 192 96 96 0 1 1 0-192z",
        512,
        512,
    ),
    "coins": (
        "M512 80c0 44.2-89.5 80-200 80S112 124.2 112 80 201.5 0 312 0s200 35.8 "
        "200 80zm-200 128c-57.7 0-109.9-9.7-146.5-25.5C140.7 200.7 112 226.7 112 "
        "256c0 44.2 89.5 80 200 80s200-35.8 200-80c0-29.3-28.7-55.3-53.5-73.5C421.9 "
        "198.3 369.7 208 312 208zm0 160c-57.7 0-109.9-9.7-146.5-25.5C140.7 360.7 112 "
        "386.7 112 416c0 44.2 89.5 80 200 80s200-35.8 200-80c0-29.3-28.7-55.3-53.5-73.5C421.9 "
        "358.3 369.7 368 312 368z",
        512,
        512,
    ),
    "shield": (
        "M256 0c4.6 0 9.2 1 13.4 2.9L457.7 82.8c22 9.3 36.3 30.8 36.3 54.5l0 "
        "149.9c0 148.6-107.5 277.6-250 307.7C248.5 595.9 243.5 596 238.4 596c-5.1 "
        "0-10.1-.1-15.6-.9C79.5 565 0 436 0 287.2L0 137.3c0-23.7 14.3-45.2 "
        "36.3-54.5L242.6 2.9C246.8 1 251.4 0 256 0z",
        512,
        512,
    ),
    "sparkles": (
        "M256 0c10.5 0 19.8 6.8 23 16.8l38.2 119.5 119.5 38.2c10 3.2 16.8 12.5 "
        "16.8 23s-6.8 19.8-16.8 23l-119.5 38.2-38.2 119.5c-3.2 10-12.5 16.8-23 "
        "16.8s-19.8-6.8-23-16.8L195.8 276.7 76.3 238.5C66.3 235.3 59.5 226 59.5 "
        "215.5s6.8-19.8 16.8-23l119.5-38.2 38.2-119.5C236.2 6.8 245.5 0 256 0z",
        512,
        512,
    ),
    "catalog": (
        "M64 480c-35.3 0-64-28.7-64-64L0 96C0 60.7 28.7 32 64 32l160 0 0 448L64 "
        "480zm224 0l0-448 160 0c35.3 0 64 28.7 64 64l0 320c0 35.3-28.7 64-64 64l-160 0z",
        512,
        512,
    ),
    "check": (
        "M438.6 105.4c12.5 12.5 12.5 32.8 0 45.3l-256 256c-12.5 12.5-32.8 12.5-45.3 "
        "0l-128-128c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0L160 338.7 393.4 "
        "105.4c12.5-12.5 32.8-12.5 45.3 0z",
        448,
        512,
    ),
    "flask": (
        "M437.2 403.5L320 215V64h8c13.3 0 24-10.7 24-24s-10.7-24-24-24H120c-13.3 0-24 10.7-24 "
        "24s10.7 24 24 24h8v151L10.8 403.5C-1.8 423.6-3.8 449.2 5.7 471.2S33.4 512 64 "
        "512h320c30.6 0 58.3-20.8 67.8-40.8s7.5-47.6-5.1-67.7z",
        448,
        512,
    ),
    "beaker": (
        "M437.2 403.5L320 215V64h8c13.3 0 24-10.7 24-24s-10.7-24-24-24H120c-13.3 0-24 10.7-24 "
        "24s10.7 24 24 24h8v151L10.8 403.5C-1.8 423.6-3.8 449.2 5.7 471.2S33.4 512 64 "
        "512h320c30.6 0 58.3-20.8 67.8-40.8s7.5-47.6-5.1-67.7z",
        448,
        512,
    ),
}


_LAB_BEAKER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="flaskGlass" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FFEAA0" stop-opacity="0.95"/>
      <stop offset="40%" stop-color="#D4AF37" stop-opacity="0.8"/>
      <stop offset="100%" stop-color="#785A28" stop-opacity="0.95"/>
    </linearGradient>
    <linearGradient id="liquidYellow" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#FFF677"/>
      <stop offset="30%" stop-color="#FFD100"/>
      <stop offset="70%" stop-color="#FF9E00"/>
      <stop offset="100%" stop-color="#D46B00"/>
    </linearGradient>
    <linearGradient id="liquidTop" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#FFFFA0"/>
      <stop offset="50%" stop-color="#FFEA40"/>
      <stop offset="100%" stop-color="#E5B800"/>
    </linearGradient>
    <radialGradient id="innerGlow" cx="50%" cy="70%" r="50%">
      <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.85"/>
      <stop offset="40%" stop-color="#FFD100" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#FF9E00" stop-opacity="0"/>
    </radialGradient>
  </defs>

  <!-- Outer warm aura glow -->
  <path d="M208 32 L304 32 L304 176 L468 408 C488 436 472 480 432 480 L80 480
           C40 480 24 436 44 408 L208 176 Z"
        fill="#FFD100" opacity="0.22" />

  <!-- Yellow / Golden Potion Liquid Body -->
  <path d="M152 256 L360 256 L440 376 C460 404 444 456 408 456 L104 456 C68 456 52 404 72 376 Z"
        fill="url(#liquidYellow)" />
  <!-- Liquid Meniscus Surface -->
  <ellipse cx="256" cy="256" rx="104" ry="18" fill="url(#liquidTop)" />

  <!-- Inner Potion Glow Center -->
  <ellipse cx="256" cy="370" rx="130" ry="70" fill="url(#innerGlow)" />

  <!-- Rising Bubbles in Liquid -->
  <circle cx="200" cy="380" r="16" fill="#FFFFA0" opacity="0.8" />
  <circle cx="290" cy="340" r="20" fill="#FFFFFF" opacity="0.9" />
  <circle cx="240" cy="300" r="12" fill="#FFFFA0" opacity="0.7" />
  <circle cx="320" cy="400" r="10" fill="#FFF480" opacity="0.6" />
  <circle cx="160" cy="340" r="14" fill="#FFFFA0" opacity="0.75" />
  <!-- Floating bubble steam / vapors -->
  <circle cx="235" cy="200" r="15" fill="#FFD100" opacity="0.85" />
  <circle cx="265" cy="150" r="11" fill="#FFF1A8" opacity="0.9" />
  <circle cx="245" cy="90" r="8" fill="#FFFFFF" opacity="0.95" />

  <!-- Glass Flask Body (thick beveled outline) -->
  <path d="M192 48 L320 48 M208 48 L208 176 L44 408 C24 436 40 480 80 480
           L432 480 C472 480 488 436 468 408 L304 176 L304 48"
        fill="none" stroke="url(#flaskGlass)" stroke-width="28" stroke-linecap="round"
        stroke-linejoin="round" />

  <!-- Flask Lip / Top Rim Collar -->
  <path d="M180 32 L332 32 C340 32 344 40 338 48 L326 64 C322 68 314 72 304 72
           L208 72 C198 72 190 68 186 64 L174 48 C168 40 172 32 180 32 Z"
        fill="#D4AF37" stroke="#FFD100" stroke-width="6" />

  <!-- Glass Specular Reflection Highlight -->
  <path d="M224 96 L224 160 L80 364 C68 382 72 420 90 436"
        fill="none" stroke="#FFFFFF" stroke-width="16" stroke-linecap="round" opacity="0.75" />

  <!-- Measurement volume markings on glass -->
  <line x1="270" y1="320" x2="305" y2="320" stroke="#FFEAA0" stroke-width="8"
        stroke-linecap="round" opacity="0.8"/>
  <line x1="285" y1="355" x2="325" y2="355" stroke="#FFEAA0" stroke-width="8"
        stroke-linecap="round" opacity="0.8"/>
  <line x1="300" y1="390" x2="345" y2="390" stroke="#FFEAA0" stroke-width="8"
        stroke-linecap="round" opacity="0.8"/>
  <line x1="315" y1="425" x2="365" y2="425" stroke="#FFEAA0" stroke-width="8"
        stroke-linecap="round" opacity="0.8"/>
</svg>"""


def dadcraft_icon(name: str, color: str = COLOR_GOLD_BRIGHT, size: int = 16) -> QIcon:
    """Generate a clean, high-DPI QIcon from FontAwesome SVG vector data."""
    entry = _FA_PATHS.get(name.lower())
    if entry is None:
        return QIcon()

    path_data, vb_w, vb_h = entry
    svg_text = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w} {vb_h}">
    <path fill="{color}" d="{path_data}"/>
</svg>"""

    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    return QIcon(pixmap)


def get_tab_icon(tab_name: str) -> QIcon:
    """Retrieve the authentic Dadcraft icon for standard launcher tabs."""
    mapping = {
        "catalog": ("catalog", COLOR_GOLD_BRIGHT),
        "server": ("server", COLOR_GOLD_BRIGHT),
        "console": ("terminal", COLOR_RARE),
        "accounts": ("users", COLOR_UNCOMMON),
        "play": ("sword", COLOR_GOLD_BRIGHT),
        "characters": ("sword", COLOR_GOLD_BRIGHT),
        "bots": ("robot", COLOR_RARE),
        "maintenance": ("database", COLOR_GOLD_LIGHT),
        "modules": ("puzzle", COLOR_TEXT_GOLD),
        "networking": ("network", COLOR_RARE),
    }
    low = tab_name.lower().strip()
    for key, (icon_name, color) in mapping.items():
        if key in low:
            return dadcraft_icon(icon_name, color=color, size=16)
    return dadcraft_icon("server", color=COLOR_GOLD_BRIGHT, size=16)


def get_app_icon() -> QIcon:
    """Retrieve the application icon for the launcher window, dock, and taskbar.

    If an icon image file exists under the resource tree, loads it directly.
    Otherwise, procedurally constructs a crisp multi-resolution vector emblem
    (16, 24, 32, 48, 64, 128, 256px) featuring the glowing yellow lab beaker.
    """
    from yulon.resources import bundle_root

    for candidate in ("yulon.png", "yulon.ico", "yulon.icns", "app_icon.png"):
        icon_path = bundle_root() / candidate
        if icon_path.exists():
            return QIcon(str(icon_path))
        assets_path = bundle_root() / "assets" / candidate
        if assets_path.exists():
            return QIcon(str(assets_path))

    renderer = QSvgRenderer(QByteArray(_LAB_BEAKER_SVG.encode("utf-8")))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)

    return icon
