"""Incremental, date-based sync logic.

We persist the timestamp (`dt`) of the newest downloaded file and, on the next
run, only pull files newer than that watermark. Pure functions here so they're
easy to test without a camera.
"""

from __future__ import annotations

from collections.abc import Iterable

from bushdump.camera import CameraFile


def files_to_download(
    available: Iterable[CameraFile],
    watermark: int | None,
) -> list[CameraFile]:
    """Return files strictly newer than `watermark`, oldest first.

    `watermark` is the `dt` of the newest file downloaded on a prior run, or
    None for a first run (download everything).
    """
    cutoff = watermark if watermark is not None else -1
    newer = [f for f in available if f.timestamp > cutoff]
    return sorted(newer, key=lambda f: f.timestamp)


def next_watermark(downloaded: Iterable[CameraFile], previous: int | None) -> int | None:
    """Compute the new watermark after a run: the max `dt` seen."""
    timestamps = [f.timestamp for f in downloaded]
    if not timestamps:
        return previous
    newest = max(timestamps)
    return newest if previous is None else max(newest, previous)
