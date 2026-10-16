"""Generate the app's code-drawn Windows icon using only the standard library."""

from pathlib import Path
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]


def rounded(x, y, left, top, right, bottom, radius):
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x-cx)**2 + (y-cy)**2 <= radius**2


def pixel(x, y):
    if not rounded(x, y, 0, 0, 256, 256, 48):
        return (0, 0, 0, 0)
    color = (13, 27, 43, 255)
    if rounded(x, y, 34, 43, 222, 181, 18):
        color = (37, 215, 196, 255)
    if rounded(x, y, 44, 53, 212, 171, 10):
        color = (13, 27, 43, 255)
    if 117 <= x <= 139 and 181 <= y <= 201 or rounded(x, y, 83, 199, 173, 211, 6):
        color = (222, 237, 242, 255)
    if (x-168)**2 + (y-96)**2 <= 22**2:
        color = (240, 100, 100, 255)
    return color


def png(size):
    rows = bytearray()
    scale = 256/size
    for y in range(size):
        rows.append(0)
        for x in range(size):
            samples = [pixel((x+(i+0.5)/3)*scale, (y+(j+0.5)/3)*scale) for i in range(3) for j in range(3)]
            rows.extend(round(sum(sample[c] for sample in samples)/9) for c in range(4))
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name+data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


def main():
    sizes = (16, 32, 48, 64, 128, 256)
    images = [png(size) for size in sizes]
    offset = 6 + 16*len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack("<4B2H2I", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "deskrec-buddy.ico").write_bytes(struct.pack("<3H", 0, 1, len(sizes)) + b"".join(entries+images))


if __name__ == "__main__":
    main()
