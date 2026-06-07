"""HTTP client for the trail camera's local API.

See docs/camera-api.md for the wire protocol. The camera serves unencrypted
HTTP on its own WiFi AP (default 192.168.1.8).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_HOST = "192.168.1.8"


@dataclass(frozen=True, slots=True)
class CameraFile:
    """One entry from a /Storage?GetFilePage listing."""

    name: str  # "n"
    timestamp: int  # "dt" — unix seconds
    size: int  # "s" — bytes
    fid: str  # "fid" — file ID used for download/thumb/delete

    @classmethod
    def from_json(cls, obj: dict) -> CameraFile:
        return cls(
            name=obj["n"],
            timestamp=int(obj["dt"]),
            size=int(obj["s"]),
            fid=str(obj["fid"]),
        )


class CameraClient:
    """Thin wrapper over the camera HTTP API.

    TODO: implement against httpx — enter storage mode, list pages, download.
    """

    def __init__(self, host: str = DEFAULT_HOST) -> None:
        self.host = host
        self.base_url = f"http://{host}"
