"""Validate already-downloaded media files and write .error.txt sidecars for failures.

Runs the same checks used during sync (JPEG markers, Pillow structural verify,
pixel decode, MP4 ftyp box) on files already on disk. No camera or network
connection needed.

Files that already have a .error.txt sidecar are skipped — consistent with how
sync treats accepted-corrupt files.

Timing output on completion (or Ctrl+C) shows average time per file, so the
tool doubles as a benchmark for how much validation adds per download.

Usage:
  uv run python tools/validate-files.py ~/photos/frontgate/
  uv run python tools/validate-files.py ~/photos/frontgate/*.jpg
  uv run python tools/validate-files.py img1.jpg img2.mp4
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from bushdump.validate import validate_media

_MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".mp4"}
_JPEG_EOI = b"\xff\xd9"
_JPEG_COM = b"\xff\xfe"


def _collect_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in _MEDIA_EXTENSIONS:
                    files.append(f)
        elif p.is_file():
            files.append(p)
        else:
            print(f"warning: {p} not found", file=sys.stderr)
    return files


def _kind(path: Path) -> str:
    return "JPG" if path.suffix.lower() in {".jpg", ".jpeg"} else "MP4"


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".error.txt")


def _extract_timelapse_meta(path: Path) -> Path | None:
    """Extract the COM block after EOI on timelapse JPEGs to a .timelapse.bin sidecar.

    Returns the sidecar path if written, else None.
    """
    sidecar = path.with_name(path.name + ".timelapse.bin")
    if sidecar.exists():
        return None
    data = path.read_bytes()
    eoi_pos = data.rfind(_JPEG_EOI)
    if eoi_pos == -1:
        return None
    tail_start = eoi_pos + len(_JPEG_EOI)
    tail = data[tail_start:]
    if not tail.startswith(_JPEG_COM):
        return None
    sidecar.write_bytes(tail)
    return sidecar


def _write_sidecar(path: Path, reasons: list[str]) -> None:
    lines = [
        f"Validation failed: {'; '.join(reasons)}",
        f"Validated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"File size: {path.stat().st_size} bytes",
    ]
    _sidecar(path).write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate downloaded media files and write .error.txt sidecars for failures.",
    )
    parser.add_argument(
        "paths", nargs="+", type=Path, metavar="PATH", help="Files or directories to validate"
    )
    args = parser.parse_args()

    files = _collect_files(args.paths)
    if not files:
        print("No media files found.")
        sys.exit(0)

    ok = skipped = failed = 0
    times: list[float] = []
    interrupted = False

    try:
        for f in files:
            if _sidecar(f).exists():
                skipped += 1
                print(f"  skip  {f}")
                continue
            t0 = time.monotonic()
            reasons = validate_media(f, _kind(f))
            elapsed = time.monotonic() - t0
            times.append(elapsed)
            if not reasons:
                ok += 1
                print(f"    ok  {f}  ({elapsed:.2f}s)")
            else:
                failed += 1
                _write_sidecar(f, reasons)
                print(f"  FAIL  {f}  ({elapsed:.2f}s)")
                for r in reasons:
                    print(f"        {r}")
            if _kind(f) == "JPG":
                meta_path = _extract_timelapse_meta(f)
                if meta_path:
                    print(f"  meta  {f.name} → {meta_path.name}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        interrupted = True

    print(f"\n{ok} ok, {failed} failed, {skipped} skipped (already have sidecar)")
    if times:
        total = sum(times)
        avg = total / len(times)
        print(f"{ok + failed} validated in {total:.2f}s  (avg {avg:.3f}s/file)")

    sys.exit(1 if (failed or interrupted) else 0)


if __name__ == "__main__":
    main()
