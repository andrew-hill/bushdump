"""HTTP client for the trail camera's local API.

See docs/camera-api.md for the wire protocol. The camera serves unencrypted
HTTP on its own WiFi AP (default 192.168.8.1:8080).
"""

from __future__ import annotations

import contextlib
import filecmp
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bushdump.validate import validate_media

# httpx is imported lazily inside CameraClient so the pure helpers above
# (CameraFile, parse_file_page) and the sync logic stay importable without it.

DEFAULT_HOST = "192.168.8.1:8080"


@dataclass(frozen=True, slots=True)
class CameraFile:
    """One entry from a /list/detail/forward listing."""

    id: int
    date: str  # e.g. "2026-05-10 13:00:01"
    size: int  # bytes
    type: int  # 1=JPG, 2=MP4

    @property
    def kind(self) -> str:
        return "JPG" if self.type == 1 else "MP4"

    @property
    def name(self) -> str:
        tag = self.date.replace("-", "").replace(" ", "T").replace(":", "")
        return f"{tag}_{self.id:08d}.{self.kind.lower()}"

    @classmethod
    def from_json(cls, obj: dict) -> CameraFile:
        return cls(
            id=int(obj["id"]),
            date=str(obj["date"]),
            size=int(obj["size"]),
            type=int(obj["type"]),
        )


@dataclass(frozen=True, slots=True)
class CameraStats:
    """Camera health snapshot from /cmd/info/2 and /cmd/info/3."""

    battery: int  # percent 0–100
    temperature: int  # celsius
    ext_power: bool
    sd_total_kb: int  # API returns KB
    sd_used_kb: int  # API returns KB
    photo_count: int
    video_count: int


@dataclass(frozen=True, slots=True)
class TimeInfo:
    """Parsed /cmd/info/4 clock + timezone."""

    clock_utc: datetime  # camera's local clock converted to UTC
    tz_minutes: int | None  # UTC offset in minutes; None if not reported


def parse_info4(data: object) -> TimeInfo | None:
    """Parse /cmd/info/4 → TimeInfo. Best-effort; returns None on unrecognised shape.

    The camera reports local time in `clock` (YYYY-MM-DD HH:MM:SS) and a UTC
    offset in minutes under either `tz` or `timezone`. We subtract the offset
    to get UTC so callers can compare directly to datetime.now(UTC).
    """
    if not isinstance(data, dict):
        return None
    d = data.get("data") or {}
    clock_str = d.get("clock")
    if not clock_str:
        return None
    try:
        local_dt = datetime.strptime(str(clock_str), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    tz_raw = d.get("tz") if "tz" in d else d.get("timezone")
    tz_minutes: int | None = None
    if tz_raw is not None:
        with contextlib.suppress(TypeError, ValueError):
            tz_minutes = int(tz_raw)
    from datetime import timedelta, timezone

    if tz_minutes is not None:
        aware = local_dt.replace(tzinfo=timezone(timedelta(minutes=tz_minutes)))
        clock_utc = aware.astimezone(UTC)
    else:
        # No tz info — assume clock is already UTC (best guess)
        clock_utc = local_dt.replace(tzinfo=UTC)
    return TimeInfo(clock_utc=clock_utc, tz_minutes=tz_minutes)


def parse_info2(data: object) -> tuple[int, int, bool]:
    """Parse /cmd/info/2 → (battery %, temperature °C, ext_power). Best-effort."""
    if not isinstance(data, dict):
        return 0, 0, False
    d = data.get("data") or {}
    return (
        int(d.get("battery") or d.get("voltage") or 0),
        int(d.get("temperature", 0) or 0),
        bool(d.get("ext_power", False)),
    )


def parse_info3(data: object) -> tuple[int, int, int, int]:
    """Parse /cmd/info/3 → (sd_total_kb, sd_used_kb, photo_count, video_count). Best-effort."""
    if not isinstance(data, dict):
        return 0, 0, 0, 0
    d = data.get("data") or {}
    return (
        int(d.get("total", 0) or 0),
        int(d.get("used", 0) or 0),
        int(d.get("photo", 0) or 0),
        int(d.get("video", 0) or 0),
    )


def parse_file_page(data: object) -> list[CameraFile]:
    """Parse a file listing response into CameraFiles.

    Expects {"code": 0, "data": [...]}. Entries missing required fields are
    skipped. Returns [] on any unexpected shape.
    """
    if not isinstance(data, dict):
        return []
    inner = data.get("data")
    if not isinstance(inner, list):
        return []
    out: list[CameraFile] = []
    for obj in inner:
        if isinstance(obj, dict) and {"id", "date", "size", "type"} <= obj.keys():
            out.append(CameraFile.from_json(obj))
    return out


def _sidecar_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".error.txt")


def _check_size(path: Path, expected: int) -> list[str]:
    actual = path.stat().st_size
    if actual != expected:
        return [f"incomplete download: expected {expected} B, got {actual} B"]
    return []


def _files_identical(a: Path, b: Path) -> bool:
    return filecmp.cmp(a, b, shallow=False)


def _write_sidecar(
    path: Path,
    reasons: list[str],
    *,
    expected_size: int,
    actual_size: int,
    identical: bool,
    alt_path: Path | None,
) -> None:
    lines = [
        f"Validation failed: {'; '.join(reasons)}",
        f"Downloaded: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"File size: {actual_size} bytes (expected {expected_size})",
        f"Identical on re-download: {'yes' if identical else 'no'}",
    ]
    if alt_path is not None:
        lines.append(f"Alt file saved: {alt_path.name}")
    path.write_text("\n".join(lines) + "\n")


class CameraClient:
    """Thin wrapper over the camera HTTP API."""

    def __init__(self, host: str = DEFAULT_HOST, timeout: float = 10.0) -> None:
        import httpx

        self.host = host
        self._timeout = timeout
        self.base_url = f"http://{host}"
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def __enter__(self) -> CameraClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- readiness ---------------------------------------------------------

    def is_ready(self) -> bool:
        """True if the camera HTTP server is responding and reports ready."""
        try:
            resp = self._client.get("/cmd/standby/reset", timeout=2.0)
            return resp.status_code == 200 and resp.json().get("code") == 0
        except Exception:
            return False

    def keep_alive(self) -> bool:
        """Ping the camera to prevent it sleeping during a long download."""
        try:
            resp = self._client.get("/cmd/standby/reset")
            return resp.status_code == 200
        except Exception:
            return False

    def wait_until_ready(self, timeout: float = 30.0, interval: float = 1.0) -> bool:
        """Poll until the camera answers HTTP, or give up after `timeout`s."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_ready():
                return True
            time.sleep(interval)
        return False

    # --- API calls ---------------------------------------------------------

    def list_all_files(self, on_page: Callable[[int], None] | None = None) -> list[CameraFile]:
        """Fetch all files on the camera in one paginated scan.

        Retries each page once on ReadTimeout — the camera's HTTP server
        occasionally stalls mid-listing on longer card contents.

        on_page, if provided, is called with the running file count after each page.
        """
        import httpx

        files: list[CameraFile] = []
        from_id = 0
        while True:
            for attempt in range(2):
                try:
                    resp = self._client.get(f"/list/detail/forward/{from_id}/50")
                    break
                except httpx.ReadTimeout:
                    if attempt == 1:
                        raise
                    self._client.close()
                    self._client = httpx.Client(base_url=self.base_url, timeout=self._timeout)
            resp.raise_for_status()
            page = parse_file_page(resp.json())
            if not page:
                break
            files.extend(page)
            if on_page is not None:
                on_page(len(files))
            from_id = page[-1].id
        return files

    def _stream_to_tmp(self, file: CameraFile, tmp: Path) -> None:
        """Stream a file from the camera to tmp. Retries once on RemoteProtocolError."""
        import httpx

        for attempt in range(2):
            try:
                with self._client.stream("GET", f"/file/{file.id}/{file.kind}") as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                return
            except httpx.RemoteProtocolError:
                if attempt == 1:
                    raise
                tmp.unlink(missing_ok=True)
                self._client.close()
                self._client = httpx.Client(base_url=self.base_url, timeout=self._timeout)

    def download(self, file: CameraFile, dest_dir: Path, *, retry: bool = False) -> Path | None:
        """Stream a file to dest_dir. Returns the saved path, or None if already complete.

        Filenames include the camera timestamp, so collisions are extremely
        rare. If one does occur (same timestamp+id, different content), a
        numeric suffix (_2, _3, …) is appended until a free slot is found.

        On validation failure, re-downloads and compares the two copies:
        - identical bytes → camera-side corruption; saves with .error.txt sidecar
        - different bytes, second clean → transit error, silently recovered
        - different bytes, both bad → saves both (.alt copy) with sidecar

        Pass retry=True to re-download even if a sidecar exists; the sidecar is
        removed on a clean re-download.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file.name
        sidecar = _sidecar_path(dest)

        if dest.exists():
            if (sidecar.exists() and not retry) or dest.stat().st_size == file.size:
                return None
            # Collision: same name, different size, no sidecar → find a free slot.
            stem, _, suffix = file.name.rpartition(".")
            counter = 2
            while True:
                candidate = dest_dir / f"{stem}_{counter}.{suffix}"
                if not candidate.exists():
                    dest = candidate
                    sidecar = _sidecar_path(dest)
                    break
                if _sidecar_path(candidate).exists() or candidate.stat().st_size == file.size:
                    return None
                counter += 1

        # .part keeps dest absent during the write so an interrupted run retries cleanly.
        tmp = dest.with_suffix(dest.suffix + ".part")
        self._stream_to_tmp(file, tmp)
        failures = _check_size(tmp, file.size) + validate_media(tmp, file.kind)
        if not failures:
            tmp.replace(dest)
            if retry:
                sidecar.unlink(missing_ok=True)
            return dest

        # First download failed validation — re-download and compare.
        tmp2 = dest.with_suffix(dest.suffix + ".part2")
        try:
            self._stream_to_tmp(file, tmp2)
        except Exception:
            tmp.unlink(missing_ok=True)
            tmp2.unlink(missing_ok=True)
            raise

        if _files_identical(tmp, tmp2):
            # Same bytes both times → camera-side corruption; accept and note it.
            actual = tmp.stat().st_size
            tmp.replace(dest)
            tmp2.unlink(missing_ok=True)
            _write_sidecar(
                sidecar,
                failures,
                expected_size=file.size,
                actual_size=actual,
                identical=True,
                alt_path=None,
            )
            return dest

        # Different bytes → transit error; check if the second download is clean.
        failures2 = _check_size(tmp2, file.size) + validate_media(tmp2, file.kind)
        if not failures2:
            tmp.unlink(missing_ok=True)
            tmp2.replace(dest)
            return dest

        # Both downloads failed with different content — keep both for inspection.
        alt = dest.with_suffix(".alt" + dest.suffix)
        actual = tmp.stat().st_size
        tmp.replace(dest)
        tmp2.replace(alt)
        combined = failures + [f"re-download: {r}" for r in failures2]
        _write_sidecar(
            sidecar,
            combined,
            expected_size=file.size,
            actual_size=actual,
            identical=False,
            alt_path=alt,
        )
        return dest

    def stats(self) -> CameraStats:
        """Fetch camera health: battery, SD usage, file counts."""
        battery, temperature, ext_power = parse_info2(self._client.get("/cmd/info/2").json())
        sd_total_kb, sd_used_kb, photo_count, video_count = parse_info3(
            self._client.get("/cmd/info/3").json()
        )
        return CameraStats(
            battery=battery,
            temperature=temperature,
            ext_power=ext_power,
            sd_total_kb=sd_total_kb,
            sd_used_kb=sd_used_kb,
            photo_count=photo_count,
            video_count=video_count,
        )

    def describe(self) -> str:
        """One-line summary for the add-confirm step (best-effort)."""
        label = f"camera at {self.host}"
        try:
            info = self._client.get("/cmd/info/1").json()
            brand = info.get("data", {}).get("brand", "")
            product = info.get("data", {}).get("product", "")
            if brand or product:
                label = " ".join(filter(None, [brand, product]))
        except Exception:
            pass
        counts = ["? photos", "? videos"]
        try:
            _, _, photo_count, video_count = parse_info3(self._client.get("/cmd/info/3").json())
            counts = [f"{photo_count} photos", f"{video_count} videos"]
        except Exception:
            pass
        return f"{label} — " + ", ".join(counts)

    def get_settings(self) -> dict:
        """Fetch current camera settings from /cmd/getSetting.

        Returns the unwrapped data dict. Raises RuntimeError if the response
        is not the expected {"code": 0, "data": {...}} envelope.
        """
        resp = self._client.get("/cmd/getSetting")
        resp.raise_for_status()
        body = resp.json()
        if (
            not isinstance(body, dict)
            or body.get("code") != 0
            or not isinstance(body.get("data"), dict)
        ):
            raise RuntimeError(f"Unexpected response from /cmd/getSetting: {body!r}")
        return body["data"]

    def time_info(self) -> dict:
        """Return the raw /cmd/info/4 response (clock + timezone).

        Response shape varies by firmware — returned as-is for the caller to inspect.
        """
        return self._client.get("/cmd/info/4").json()

    def parsed_time_info(self) -> TimeInfo | None:
        """Return parsed /cmd/info/4 clock as a TimeInfo, or None on unrecognised shape."""
        return parse_info4(self.time_info())

    def set_clock(self, when: datetime) -> None:
        """Set camera clock via POST /cmd/setGmtClock. Pass a UTC datetime."""
        payload = when.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        self._client.post("/cmd/setGmtClock", json={"data": payload})

    def power_off(self) -> None:
        """Turn the camera's WiFi off (saves its battery).

        Some models drop the TCP connection before sending an HTTP response;
        suppress those errors — the command still reached the camera.
        """
        import httpx

        with contextlib.suppress(httpx.RemoteProtocolError, httpx.ConnectError):
            self._client.get("/cmd/standby/now")
