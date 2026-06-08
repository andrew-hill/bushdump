"""Incremental, date-based sync logic.

We persist the date string of the newest downloaded file and, on the next run,
re-check files from that same second plus anything later so interrupted runs
don't skip same-timestamp siblings. Pure functions here so they're easy to test
without a camera.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from bushdump.camera import CameraFile

if TYPE_CHECKING:
    from bushdump.config import Camera


def cameras_present(cameras: Iterable[Camera], present_addresses: Iterable[str]) -> list[Camera]:
    """Pick configured cameras whose BLE address showed up in a scan (case-insensitive)."""
    present = {a.lower() for a in present_addresses}
    return [c for c in cameras if c.ble_address and c.ble_address.lower() in present]


def files_to_download(
    available: Iterable[CameraFile],
    watermark: str | None,
) -> list[CameraFile]:
    """Return files at or after `watermark`, oldest first.

    `watermark` is the `date` of the newest file downloaded on a prior run
    ("YYYY-MM-DD HH:MM:SS"), or None for a first run (download everything). We
    include the watermark second again so an interrupted run cannot permanently
    skip same-timestamp siblings; already-on-disk files are skipped by size
    during download.

    """
    newer = list(available) if watermark is None else [f for f in available if f.date >= watermark]
    return sorted(newer, key=lambda f: (f.date, f.id))
