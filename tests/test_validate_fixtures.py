"""validate_media tests against real corrupt JPEG fixture files.

Generated fixtures live in tests/fixtures/corrupt-jpegs/ and are created by
running tests/fixtures/make_corrupt_jpegs.py. Downloaded Pillow fixtures were
fetched manually from the python-pillow/Pillow test suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bushdump.validate import validate_media

FIXTURES = Path(__file__).parent / "fixtures" / "corrupt-jpegs"


def _f(name: str) -> Path:
    p = FIXTURES / name
    if not p.exists() or p.stat().st_size == 0:
        pytest.skip(f"fixture missing: {name} — run tests/fixtures/make_corrupt_jpegs.py")
    return p


# --- Generated fixtures ---


def test_dead_sensor_fails() -> None:
    # Every pixel is RGB(128,128,128) — simulates a dead or covered sensor
    # where the camera produces a flat grey frame instead of real image data.
    failures = validate_media(_f("dead-sensor.jpg"), "JPG")
    assert any("single brightness" in f for f in failures)


@pytest.mark.xfail(
    strict=True,
    reason="TODO: detect libjpeg MCU-row concealment from zeroed scan data",
)
def test_corrupt_scan_zeros_detected() -> None:
    # The latter 55% of the Huffman-coded scan data is replaced with 0x00 bytes,
    # simulating a partial WiFi download where the file was padded to appear
    # complete. libjpeg's error concealment fills the missing MCUs by repeating
    # the last decoded row, so the image loads but the lower portion is wrong.
    failures = validate_media(_f("corrupt-scan-zeros.jpg"), "JPG")
    assert failures


@pytest.mark.xfail(
    strict=True,
    reason="TODO: detect libjpeg MCU-row concealment from bit-flipped scan data",
)
def test_corrupt_scan_flip_detected() -> None:
    # 128 bytes at the 25% mark of the scan data are bit-inverted, simulating
    # WiFi transmission errors. libjpeg's error concealment recovers by
    # repeating MCU rows, so the image loads without raising an exception but
    # the affected region contains wrong pixel data.
    failures = validate_media(_f("corrupt-scan-flip.jpg"), "JPG")
    assert failures


# --- Downloaded Pillow fixtures ---


def test_pillow_truncated_fails() -> None:
    # Scan data is cut short and there is no EOI marker — a genuine truncated
    # download with no padding. Pillow raises when trying to load the image.
    failures = validate_media(_f("pillow-truncated.jpg"), "JPG")
    assert any("EOI" in f or "truncated" in f.lower() for f in failures)


def test_pillow_truncated_app14_passes() -> None:
    # The APP14 (Adobe color-transform) segment is truncated, but the scan data
    # and EOI are intact. The image decodes correctly; the missing APP14 only
    # affects color-space hints that Pillow ignores gracefully.
    assert validate_media(_f("pillow-truncated-app14.jpg"), "JPG") == []


def test_pillow_junk_header_passes() -> None:
    # Contains non-JPEG bytes before the real SOI marker. Pillow scans forward
    # to find the SOI and decodes normally. Our marker check reads the literal
    # first bytes, so this currently passes — tightening that check is a
    # separate decision.
    assert validate_media(_f("pillow-junk-header.jpg"), "JPG") == []


def test_pillow_exif_typeerror_passes() -> None:
    # EXIF metadata contains a tag with an unexpected type that causes a
    # TypeError in some parsers. Pixel data is intact; the image loads fine.
    assert validate_media(_f("pillow-exif-typeerror.jpg"), "JPG") == []


def test_pillow_photoshop_broken_passes() -> None:
    # Saved by Photoshop with a malformed segment (bad DPI field in APP0).
    # Pixel data is intact and the image loads correctly.
    assert validate_media(_f("pillow-photoshop-broken.jpg"), "JPG") == []


# --- Fuzz corpus ---


def test_fuzz_1a4d_fails() -> None:
    # 25-byte file from the go-fuzz-corpus JPEG corpus. Has a valid SOI but no
    # EOI and a broken Huffman data stream — a minimal malformed JPEG that
    # exercises the structural checks.
    failures = validate_media(_f("fuzz-1a4d.jpg"), "JPG")
    assert failures
