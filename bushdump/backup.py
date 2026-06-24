"""Pure backup watermark logic — no I/O, no httpx."""

from __future__ import annotations

import re
from collections.abc import Iterable

# Matches exactly: YYYYMMDDTHHmmss_<8+ digit id>.(jpg|mp4)
# Rejects: .error.txt sidecars, .part/.part2 temps, .alt.* copies, _2/_3 collision renames.
_MEDIA_NAME_RE = re.compile(r"\d{8}T\d{6}_\d{8,}\.(jpg|mp4)", re.IGNORECASE)


def date_from_name(name: str) -> str | None:
    """'20260510T130001_00000001.jpg' -> '2026-05-10 13:00:01'. None for non-canonical names."""
    if _MEDIA_NAME_RE.fullmatch(name) is None:
        return None
    y, mo, d = name[0:4], name[4:6], name[6:8]
    h, mi, s = name[9:11], name[11:13], name[13:15]
    return f"{y}-{mo}-{d} {h}:{mi}:{s}"


def parse_rsync_pending(dry_run_output: str) -> set[str]:
    """Basenames rsync still wants to transfer, from `rsync -an --out-format='%n'`."""
    result: set[str] = set()
    for line in dry_run_output.splitlines():
        line = line.strip()
        if not line or line.endswith("/"):
            continue
        result.add(line.rsplit("/", 1)[-1])
    return result


def media_names_of_kind(names: Iterable[str], kind: str) -> set[str]:
    """Canonical media names of the given kind ('JPG'/'MP4') from names.

    Drops every artifact (sidecars, .part files, .alt copies, collision renames)
    and the other media type. This is the single sanitizer both rsync-pending and
    local sets pass through before reaching safe_watermark.
    """
    ext = f".{kind.lower()}"
    return {n for n in names if date_from_name(n) is not None and n.lower().endswith(ext)}


def safe_watermark(local_names: Iterable[str], blocked: set[str]) -> str | None:
    """Highest fully-confirmed contiguous date for one media type.

    `local_names` and `blocked` should already be type-scoped canonical media names
    (callers use media_names_of_kind). Defensively ignores any name that fails
    date_from_name so a stray entry can't expand the blocked set.

    Same-second rule: a blocked file at timestamp T blocks the watermark from
    becoming T, even if a confirmed sibling shares second T.
    """
    dated: list[str] = []
    for name in local_names:
        d = date_from_name(name)
        if d is not None:
            dated.append(d)

    if not dated:
        return None

    blocked_dates: set[str] = set()
    for name in blocked:
        d = date_from_name(name)
        if d is not None:
            blocked_dates.add(d)

    if not blocked_dates:
        return max(dated)

    earliest_blocked = min(blocked_dates)
    confirmed = [d for d in dated if d < earliest_blocked]
    return max(confirmed) if confirmed else None


def advance_watermark(computed: str | None, stored: str | None) -> tuple[str | None, bool]:
    """Return (new_watermark, regressed). Advance-only: never retreat below stored."""
    if computed is None:
        return stored, False
    if stored is not None and computed < stored:
        return stored, True
    return computed, False
