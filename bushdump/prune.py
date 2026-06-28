"""Prune candidate logic — no httpx."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from bushdump.backup import date_from_name
from bushdump.camera import CameraFile


@dataclass(frozen=True, slots=True)
class LocalFile:
    name: str
    size: int
    has_error_sidecar: bool


@dataclass(frozen=True, slots=True)
class PruneVerdict:
    file: CameraFile
    deletable: bool
    reason: str  # "" when deletable; else the first blocking reason


def parse_cutoff(s: str) -> str:
    """'2026-05-01' -> '2026-05-01 00:00:00'. Accepts full datetime. Raises on garbage."""
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return f"{s} 00:00:00"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", s):
        return s
    raise ValueError(f"Cannot parse cutoff date: {s!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")


def classify_for_prune(
    camera_files: Iterable[CameraFile],
    *,
    local: Mapping[str, LocalFile],
    backup_watermark: str | None,
    cutoff_date: str,
) -> list[PruneVerdict]:
    """Classify each camera file as deletable or skipped (with reason).

    Gate order (first failure wins):
    1. f.date >= cutoff_date         → "newer than cutoff"
    2. date > backup_watermark       → "not confirmed backed up"
    3. name not in local             → "not downloaded locally"
    4. local size mismatch           → "size mismatch — re-download"
    5. has .error.txt sidecar        → "validation failed (.error.txt)"
    """
    verdicts: list[PruneVerdict] = []
    for f in camera_files:
        if f.date >= cutoff_date:
            verdicts.append(PruneVerdict(file=f, deletable=False, reason="newer than cutoff"))
            continue
        if backup_watermark is None or f.date > backup_watermark:
            verdicts.append(PruneVerdict(file=f, deletable=False, reason="not confirmed backed up"))
            continue
        if f.name not in local:
            verdicts.append(PruneVerdict(file=f, deletable=False, reason="not downloaded locally"))
            continue
        if local[f.name].size != f.size:
            verdicts.append(
                PruneVerdict(file=f, deletable=False, reason="size mismatch — re-download")
            )
            continue
        if local[f.name].has_error_sidecar:
            verdicts.append(
                PruneVerdict(file=f, deletable=False, reason="validation failed (.error.txt)")
            )
            continue
        verdicts.append(PruneVerdict(file=f, deletable=True, reason=""))
    return verdicts


def scan_local_dir(output_dir: Path) -> dict[str, LocalFile]:
    """Canonical media files in the dir + whether each has a .error.txt sidecar.

    Keyed by filename; only canonical media names (non-None date_from_name) are
    included, so artifacts (.error.txt, .part, .alt.*, collision renames) are skipped.
    """
    result: dict[str, LocalFile] = {}
    if not output_dir.is_dir():
        return result
    for p in output_dir.iterdir():
        if not p.is_file():
            continue
        if date_from_name(p.name) is None:
            continue
        has_sidecar = p.with_name(p.name + ".error.txt").exists()
        result[p.name] = LocalFile(
            name=p.name, size=p.stat().st_size, has_error_sidecar=has_sidecar
        )
    return result
