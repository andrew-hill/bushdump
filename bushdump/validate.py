"""Media file validation: structural and visual checks for downloaded JPEGs and MP4s."""

from __future__ import annotations

from pathlib import Path

_JPEG_SOI = b"\xff\xd8\xff"
_JPEG_EOI = b"\xff\xd9"
_MP4_FTYP = b"ftyp"


def _check_jpeg_markers(path: Path) -> list[str]:
    failures: list[str] = []
    with path.open("rb") as fh:
        header = fh.read(3)
        if header != _JPEG_SOI:
            failures.append(f"invalid JPEG SOI marker (got {header.hex()})")
        try:
            fh.seek(-2, 2)
            trailer = fh.read(2)
        except OSError:
            trailer = b""
        if trailer != _JPEG_EOI:
            # Some cameras append metadata (e.g. timelapse COM blocks) after EOI.
            try:
                fh.seek(-4096, 2)
            except OSError:
                fh.seek(0)
            if _JPEG_EOI not in fh.read():
                failures.append(f"missing JPEG EOI marker (last bytes: {trailer.hex()})")
    return failures


def _check_mp4_ftyp(path: Path) -> list[str]:
    with path.open("rb") as fh:
        fh.seek(4)
        box_type = fh.read(4)
    if box_type != _MP4_FTYP:
        return [f"missing MP4 ftyp box (got {box_type.hex()})"]
    return []


def _check_pillow(path: Path) -> list[str]:
    """Decode pixels at 1/8 scale and flag degenerate visual content (dead sensor)."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return []
    try:
        img = Image.open(path)
        img.draft("RGB", (64, 64))
        img.load()
        thumb = img.resize((64, 64), Image.BOX).convert("HSV")
        _, _, v_ch = thumb.split()
        v_lo, v_hi = v_ch.getextrema()
        if v_lo == v_hi:
            return ["image is entirely a single brightness (possible dead sensor)"]
    except UnidentifiedImageError as e:
        return [f"Pillow could not identify image: {e}"]
    except Exception as e:
        return [f"Pillow structural check failed: {e}"]
    return []


def validate_media(path: Path, kind: str) -> list[str]:
    """Return a list of failure reasons; empty list means the file looks valid."""
    if kind == "JPG":
        return _check_jpeg_markers(path) + _check_pillow(path)
    if kind == "MP4":
        return _check_mp4_ftyp(path)
    return []
