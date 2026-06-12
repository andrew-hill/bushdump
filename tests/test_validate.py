"""Tests for bushdump.validate — pure media validation logic."""

from __future__ import annotations

import io
from pathlib import Path

from bushdump.validate import validate_media


def _jpeg_bytes(width: int = 16, height: int = 16) -> bytes:
    """Return a real JPEG with brightness variation so the dead-sensor check passes."""
    from PIL import Image

    img = Image.new("L", (width, height))
    img.putdata([int(255 * x / (width - 1)) for y in range(height) for x in range(width)])
    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _mp4_stub() -> bytes:
    # Minimal ftyp box: 4-byte size + "ftyp" + brand + padding
    return b"\x00\x00\x00\x20" + b"ftyp" + b"isom" + b"\x00" * 28


# --- JPEG ---


def test_jpeg_valid(tmp_path: Path) -> None:
    p = tmp_path / "img.jpg"
    p.write_bytes(_jpeg_bytes())
    assert validate_media(p, "JPG") == []


def test_jpeg_bad_soi(tmp_path: Path) -> None:
    data = _jpeg_bytes()
    p = tmp_path / "img.jpg"
    p.write_bytes(b"\x00\x00\x00" + data[3:])
    failures = validate_media(p, "JPG")
    assert any("SOI" in f for f in failures)


def test_jpeg_truncated_no_eoi(tmp_path: Path) -> None:
    data = _jpeg_bytes()
    p = tmp_path / "img.jpg"
    p.write_bytes(data[:-2])  # strip EOI
    failures = validate_media(p, "JPG")
    assert any("EOI" in f for f in failures)


def test_jpeg_timelapse_com_after_eoi(tmp_path: Path) -> None:
    # Timelapse JPEGs have a 1028-byte COM block appended after EOI — should pass.
    com_block = b"\xff\xfe" + (1024).to_bytes(2, "big") + b"\x00" * 1024
    data = _jpeg_bytes() + com_block
    p = tmp_path / "img.jpg"
    p.write_bytes(data)
    assert validate_media(p, "JPG") == []


def test_jpeg_garbage_body_fails_pillow(tmp_path: Path) -> None:
    # Valid SOI + EOI wrapping junk — Pillow should reject the corrupt body
    p = tmp_path / "img.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\xab" * 200 + b"\xff\xd9")
    failures = validate_media(p, "JPG")
    assert any("Pillow" in f or "decode" in f.lower() for f in failures)


# --- MP4 ---


def test_mp4_valid(tmp_path: Path) -> None:
    p = tmp_path / "clip.mp4"
    p.write_bytes(_mp4_stub())
    assert validate_media(p, "MP4") == []


def test_mp4_bad_ftyp(tmp_path: Path) -> None:
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 100)
    failures = validate_media(p, "MP4")
    assert any("ftyp" in f for f in failures)


# --- Unknown kind ---


def test_unknown_kind_passes(tmp_path: Path) -> None:
    p = tmp_path / "file.raw"
    p.write_bytes(b"\x00" * 50)
    assert validate_media(p, "RAW") == []
