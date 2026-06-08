"""HTTP client for the trail camera's local API.

See docs/camera-api.md for the wire protocol. The camera serves unencrypted
HTTP on its own WiFi AP (default 192.168.8.1:8080).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

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


def parse_info2(data: object) -> tuple[int, int, bool]:
    """Parse /cmd/info/2 → (battery %, temperature °C, ext_power). Best-effort."""
    if not isinstance(data, dict):
        return 0, 0, False
    d = data.get("data") or {}
    return (
        int(d.get("battery", 0) or 0),
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

    def list_all_files(self) -> list[CameraFile]:
        """Fetch all files on the camera in one paginated scan.

        Retries each page once on ReadTimeout — the camera's HTTP server
        occasionally stalls mid-listing on longer card contents.
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
            from_id = page[-1].id
        return files

    def download(self, file: CameraFile, dest_dir: Path) -> Path | None:
        """Stream a file to dest_dir. Returns the saved path, or None if already complete.

        Filenames include the camera timestamp, so collisions are extremely
        rare. If one does occur (same timestamp+id, different content), a
        numeric suffix (_2, _3, …) is appended until a free slot is found.

        Retries once on a fresh connection if the camera dropped the persistent
        connection after the previous response (RemoteProtocolError).
        """
        import httpx

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file.name
        if dest.exists() and dest.stat().st_size == file.size:
            return None
        if dest.exists():
            # Collision: same timestamp+id but different size. Find a free slot.
            stem, _, suffix = file.name.rpartition(".")
            counter = 2
            while True:
                candidate = dest_dir / f"{stem}_{counter}.{suffix}"
                if not candidate.exists():
                    dest = candidate
                    break
                if candidate.stat().st_size == file.size:
                    return None  # already downloaded under this suffix
                counter += 1
        # .part keeps dest absent until the write is complete, so an interrupted
        # run leaves dest.exists() False and the next run retries cleanly.
        # The size check before rename catches silent truncations (camera closes
        # the stream early without raising), which .part alone cannot detect.
        tmp = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(2):
            try:
                with self._client.stream("GET", f"/file/{file.id}/{file.kind}") as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                break
            except httpx.RemoteProtocolError:
                if attempt == 1:
                    raise
                tmp.unlink(missing_ok=True)
                self._client.close()
                self._client = httpx.Client(base_url=self.base_url, timeout=self._timeout)
        actual = tmp.stat().st_size
        if actual != file.size:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Incomplete download: {file.name} expected {file.size} B, got {actual} B"
            )
        tmp.replace(dest)
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

    def power_off(self) -> None:
        """Turn the camera's WiFi off (saves its battery)."""
        self._client.get("/cmd/standby/now")
