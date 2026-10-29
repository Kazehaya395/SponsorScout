"""Regenerate sponsorscout/data/icons/tick.png (the checkbox tick glyph).

Pure-python PNG writer so the asset needs no image library and its provenance
stays in the repo. The glyph is a DARK check on a transparent 16x16 canvas,
drawn with anti-aliased edges; it is displayed at 13-14 px by ui/style.py on a
white indicator, matching the native checkbox look the user asked for
(revision 3, 2026-10-22).

    python3 tools/make_tick_icon.py
"""
import pathlib
import struct
import zlib

W = H = 16

# Dark glyph (native-checkbox look, rev 3). Revisions 1-2 used a white glyph on
# an accent fill, which the user rejected as not looking like a checkbox.
TICK_RGB = (0x1F, 0x29, 0x37)


def _seg_dist(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    vx, vy = x2 - x1, y2 - y1
    length_sq = vx * vx + vy * vy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / length_sq))
    dx, dy = px - (x1 + t * vx), py - (y1 + t * vy)
    return (dx * dx + dy * dy) ** 0.5


def _rows() -> bytes:
    out = bytearray()
    for y in range(H):
        out.append(0)  # PNG filter: none
        for x in range(W):
            cx, cy = x + 0.5, y + 0.5
            d = min(_seg_dist(cx, cy, 3.2, 8.4, 6.4, 11.4),
                    _seg_dist(cx, cy, 6.4, 11.4, 12.8, 4.6))
            if d <= 1.35:
                alpha = 255
            elif d <= 2.1:
                alpha = int(255 * (2.1 - d) / 0.75)
            else:
                alpha = 0
            out += bytes(TICK_RGB + (alpha,))
    return bytes(out)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def main() -> None:
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(_rows(), 9))
    png += _chunk(b"IEND", b"")
    dest = pathlib.Path(__file__).resolve().parents[1] / "sponsorscout" / "data" / "icons" / "tick.png"
    dest.write_bytes(png)
    print(f"wrote {dest} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
