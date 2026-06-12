#!/usr/bin/env python3
"""Generate corrupt-but-structurally-valid JPEG fixtures.

Three types of real-world corruption:
  corrupt-scan-zeros.jpg  — latter half of scan data zeroed (partial download, EOI intact)
  corrupt-scan-flip.jpg   — 128 bytes bit-inverted mid-scan (transmission bit errors)
  dead-sensor.jpg         — valid JPEG, every pixel same brightness (dead sensor)
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

OUT_DIR = Path(__file__).parent / "corrupt-jpegs"
OUT_DIR.mkdir(exist_ok=True)


def _base_jpeg(w: int = 256, h: int = 256) -> bytes:
    img = Image.new("RGB", (w, h))
    img.putdata(
        [(int(255 * x / (w - 1)), int(255 * y / (h - 1)), 100) for y in range(h) for x in range(w)]
    )
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, subsampling=0)
    return buf.getvalue()


def _scan_bounds(data: bytes) -> tuple[int, int]:
    """Return (scan_data_start, eoi_offset) where scan data lives."""
    i = 0
    while i < len(data) - 1:
        if data[i] == 0xFF and data[i + 1] == 0xDA:
            # SOS: skip marker + length + scan header
            seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
            scan_start = i + 2 + seg_len
            eoi = len(data) - 2  # FF D9
            return scan_start, eoi
        i += 1
    raise ValueError("No SOS marker")


def _safe_fill(data: bytearray, start: int, end: int, value: int) -> None:
    """Fill [start, end) avoiding 0xFF to prevent accidental marker sequences."""
    fill = value if value != 0xFF else 0x00
    for i in range(start, end):
        data[i] = fill


def make_zeros(base: bytes, fraction: float = 0.45) -> bytes:
    """Zero out scan data from `fraction` onwards; keep EOI."""
    scan_start, eoi = _scan_bounds(base)
    corrupt_from = scan_start + int((eoi - scan_start) * fraction)
    result = bytearray(base)
    _safe_fill(result, corrupt_from, eoi, 0x00)
    return bytes(result)


def make_flip(base: bytes, fraction: float = 0.25, n: int = 128) -> bytes:
    """Bit-invert n bytes at fraction through the scan data."""
    scan_start, eoi = _scan_bounds(base)
    pos = scan_start + int((eoi - scan_start) * fraction)
    result = bytearray(base)
    for i in range(pos, min(pos + n, eoi)):
        v = result[i] ^ 0xFF
        result[i] = v if v != 0xFF else 0xFE  # avoid creating FF markers
    return bytes(result)


def make_dead_sensor(value: int = 128) -> bytes:
    img = Image.new("RGB", (256, 256), (value, value, value))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


if __name__ == "__main__":
    base = _base_jpeg()
    files = {
        "corrupt-scan-zeros.jpg": make_zeros(base),
        "corrupt-scan-flip.jpg": make_flip(base),
        "dead-sensor.jpg": make_dead_sensor(),
    }
    for name, data in files.items():
        (OUT_DIR / name).write_bytes(data)

    print("Verifying:")
    for name, data in files.items():
        soi = data[:3] == b"\xff\xd8\xff"
        eoi = b"\xff\xd9" in data[-4096:]
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            thumb = img.resize((64, 64)).convert("L")
            lo, hi = thumb.getextrema()
            print(f"  {name}: SOI={soi} EOI={eoi} loads OK {img.size} L={lo}-{hi}")
        except Exception as e:
            print(f"  {name}: SOI={soi} EOI={eoi} LOAD FAILED: {e}")
