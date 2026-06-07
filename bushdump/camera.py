"""HTTP client for the trail camera's local API.

See docs/camera-api.md for the wire protocol. The camera serves unencrypted
HTTP on its own WiFi AP (default 192.168.8.1:8080).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# httpx is imported lazily inside CameraClient so the pure helpers above
# (CameraFile, parse_file_page) and the sync logic stay importable without it.

DEFAULT_HOST = "192.168.8.1:8080"
MediaType = str  # "Photo" | "Video"

_MEDIA_TYPE_CODE = {"Photo": 1, "Video": 2}


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
        return f"{self.id:08d}.{self.kind.lower()}"

    @classmethod
    def from_json(cls, obj: dict) -> CameraFile:
        return cls(
            id=int(obj["id"]),
            date=str(obj["date"]),
            size=int(obj["size"]),
            type=int(obj["type"]),
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

    def iter_files(self, media_type: MediaType) -> Iterator[CameraFile]:
        """Yield every file of a type, walking pages until one comes back empty."""
        type_code = _MEDIA_TYPE_CODE[media_type]
        from_id = 0
        while True:
            resp = self._client.get(f"/list/detail/forward/{from_id}/50")
            resp.raise_for_status()
            all_files = parse_file_page(resp.json())
            if not all_files:
                return
            yield from (f for f in all_files if f.type == type_code)
            from_id = all_files[-1].id

    def download(self, file: CameraFile, dest_dir: Path) -> Path:
        """Stream a file to dest_dir. Skips if a same-size copy already exists."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / file.name
        if dest.exists() and dest.stat().st_size == file.size:
            return dest
        tmp = dest.with_suffix(dest.suffix + ".part")
        with self._client.stream("GET", f"/file/{file.id}/{file.kind}") as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        tmp.replace(dest)
        return dest

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
            counts_data = self._client.get("/cmd/info/3").json()
            photo_count = counts_data.get("data", {}).get("photo", "?")
            video_count = counts_data.get("data", {}).get("video", "?")
            counts = [f"{photo_count} photos", f"{video_count} videos"]
        except Exception:
            pass
        return f"{label} — " + ", ".join(counts)

    def power_off(self) -> None:
        """Turn the camera's WiFi off (saves its battery)."""
        self._client.get("/cmd/standby/now")
