"""Own Open Graph image (1200x630) as SVG and PNG, generated in pure Python (zlib + struct, own 5x7 pixel font).

No Rockstar/Take-Two assets, logos or fonts.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

W, H = 1200, 630

FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "·": ["00000", "00000", "00000", "00100", "00000", "00000", "00000"],
    " ": ["00000"] * 7,
}

BG_TOP = (22, 27, 34)
BG_BOTTOM = (17, 21, 27)
PINK = (239, 122, 106)
CYAN = (92, 198, 184)
ORANGE = (242, 178, 122)
TEXT = (242, 237, 230)
MUTED = (163, 172, 184)

LINES = [
    ("GTA VI PC TRACKER", 11, TEXT, 150),
    ("LIVE PORT STATUS", 7, CYAN, 300),
    ("ES / EN  ·  SPOILER-FREE", 5, MUTED, 400),
]


def _canvas() -> list[bytearray]:
    rows = []
    for y in range(H):
        t = y / (H - 1)
        base = [int(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3)]
        row = bytearray()
        for x in range(W):
            # soft sunset glow top-right
            dx, dy = (x - 1000) / 600, (y + 120) / 420
            g = max(0.0, 1 - (dx * dx + dy * dy))
            r = min(255, int(base[0] + PINK[0] * 0.18 * g))
            gg = min(255, int(base[1] + PINK[1] * 0.18 * g))
            b = min(255, int(base[2] + PINK[2] * 0.18 * g))
            row += bytes((r, gg, b))
        rows.append(row)
    return rows


def _rect(rows: list[bytearray], x0: int, y0: int, w: int, h: int, color: tuple[int, int, int]) -> None:
    for y in range(max(0, y0), min(H, y0 + h)):
        for x in range(max(0, x0), min(W, x0 + w)):
            rows[y][x * 3 : x * 3 + 3] = bytes(color)


def _text(rows: list[bytearray], text: str, x: int, y: int, scale: int, color: tuple[int, int, int]) -> int:
    for ch in text:
        glyph = FONT.get(ch, FONT[" "])
        for gy, line in enumerate(glyph):
            for gx, bit in enumerate(line):
                if bit == "1":
                    _rect(
                        rows,
                        x + gx * scale,
                        y + gy * scale,
                        scale - 1 if scale > 3 else scale,
                        scale - 1 if scale > 3 else scale,
                        color,
                    )
        x += 6 * scale
    return x


def png_bytes() -> bytes:
    rows = _canvas()
    # neon frame + sunset bars
    _rect(rows, 60, 60, W - 120, 4, PINK)
    _rect(rows, 60, H - 64, W - 120, 4, CYAN)
    for i, c in enumerate((PINK, ORANGE, CYAN)):
        _rect(rows, 80 + i * 60, 520, 44, 10, c)
    for text, scale, color, y in LINES:
        _text(rows, text, 80, y, scale, color)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def svg_text() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#120d1e"/><stop offset="1" stop-color="#0b0a12"/></linearGradient>
<radialGradient id="sun" cx="0.85" cy="0" r="0.6"><stop offset="0" stop-color="#ff4fa3" stop-opacity=".35"/><stop offset="1" stop-color="#ff4fa3" stop-opacity="0"/></radialGradient></defs>
<rect width="{W}" height="{H}" fill="url(#bg)"/><rect width="{W}" height="{H}" fill="url(#sun)"/>
<rect x="60" y="60" width="{W - 120}" height="4" fill="#ff4fa3"/><rect x="60" y="{H - 64}" width="{W - 120}" height="4" fill="#3ee6f0"/>
<text x="80" y="230" font-family="Arial Narrow, Arial, sans-serif" font-weight="700" font-size="96" letter-spacing="6" fill="#f1edf7">GTA VI PC TRACKER</text>
<text x="80" y="340" font-family="Arial Narrow, Arial, sans-serif" font-weight="700" font-size="56" letter-spacing="8" fill="#3ee6f0">LIVE PORT STATUS</text>
<text x="80" y="430" font-family="Arial, sans-serif" font-size="34" letter-spacing="4" fill="#b2a9c4">ES / EN · SPOILER-FREE</text>
<rect x="80" y="520" width="44" height="10" fill="#ff4fa3"/><rect x="140" y="520" width="44" height="10" fill="#ff9a3c"/><rect x="200" y="520" width="44" height="10" fill="#3ee6f0"/>
</svg>
"""


def write(site_dir: Path) -> None:
    assets = Path(site_dir) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "og.png").write_bytes(png_bytes())
    (assets / "og.svg").write_text(svg_text(), encoding="utf-8")


if __name__ == "__main__":
    from common import SITE_DIR

    write(SITE_DIR)
