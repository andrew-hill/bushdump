"""Pure backup watermark logic — no I/O, no httpx."""

from __future__ import annotations

import re
from collections.abc import Iterable

# Matches exactly: YYYYMMDDTHHmmss_<8+ digit id>.(jpg|mp4)
# Rejects: .error.txt sidecars, .part/.part2 temps, .alt.* copies, _2/_3 collision renames.
_MEDIA_NAME_RE = re.compile(r"\d{8}T\d{6}_\d{8,}\.(jpg|mp4)", re.IGNORECASE)
_WATERMARK_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


def date_from_name(name: str) -> str | None:
    """'20260510T130001_00000001.jpg' -> '2026-05-10 13:00:01'. None for non-canonical names."""
    if _MEDIA_NAME_RE.fullmatch(name) is None:
        return None
    y, mo, d = name[0:4], name[4:6], name[6:8]
    h, mi, s = name[9:11], name[11:13], name[13:15]
    return f"{y}-{mo}-{d} {h}:{mi}:{s}"


def parse_rsync_pending(dry_run_output: str) -> set[str]:
    """Basenames rsync still wants to transfer, from `rsync -an --itemize-changes`.

    Itemize lines start with a Y-char: '.' = attribute-only (data already synced),
    '*' = message (e.g. *deleting). All other Y-chars (<, >, c, h) indicate a file
    that needs data transfer. The prefix length varies by rsync version (9 or 11
    chars); we find the first space to locate the path.
    """
    result: set[str] = set()
    for line in dry_run_output.splitlines():
        line = line.strip()
        if not line or line[0] not in ("<", ">", "c", "h"):
            continue
        space_idx = line.find(" ")
        if space_idx < 2:
            continue
        path = line[space_idx + 1 :]
        if path.endswith("/"):
            continue
        result.add(path.rsplit("/", 1)[-1])
    return result


def parse_rsync_extra(dry_run_output: str) -> set[str]:
    """Basenames on destination but not in source, from `rsync -an --delete --itemize-changes`."""
    result: set[str] = set()
    for line in dry_run_output.splitlines():
        line = line.strip()
        if not line.startswith("*deleting"):
            continue
        path = line[len("*deleting") :].lstrip()
        if path and not path.endswith("/"):
            result.add(path.rsplit("/", 1)[-1])
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


def rsync_has_summary(output: str) -> bool:
    """True if rsync verbose output contains the stats summary line.

    With -v, rsync always prints 'sent N bytes  received M bytes ...' even when
    nothing needs transferring. Absence of this line means rsync didn't complete
    (e.g. connection dropped before finishing).
    """
    return any(line.startswith("sent ") and "bytes" in line for line in output.splitlines())


def parse_rsync_transfer_count(stats_output: str) -> int | None:
    """Extract the file count from rsync --stats output.

    Returns None if the stats block is absent (e.g. transfer was skipped or failed
    before printing stats).
    """
    for line in stats_output.splitlines():
        if line.startswith("Number of regular files transferred:"):
            try:
                return int(line.split(":")[-1].strip().replace(",", ""))
            except ValueError:
                return None
    return None


def validate_watermark(value: str) -> bool:
    """True if value is a valid zero-padded watermark string (YYYY-MM-DD HH:MM:SS)."""
    return bool(_WATERMARK_RE.fullmatch(value))


def advance_watermark(computed: str | None, stored: str | None) -> tuple[str | None, bool]:
    """Return (new_watermark, regressed). Advance-only: never retreat below stored."""
    if computed is None:
        return stored, False
    if stored is not None and computed < stored:
        return stored, True
    return computed, False
