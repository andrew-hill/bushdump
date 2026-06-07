"""Load user config (multiple cameras) and persist per-camera sync state.

Config lives at ~/.config/bushdump/config.toml. Each camera is a
`[cameras.<name>]` section with its own BLE address and SSID; top-level keys are
defaults applied to every camera. Sync state (per-camera, per-type `dt`
watermark) lives next to it as state.json. Neither is committed — see .gitignore.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from bushdump.camera import DEFAULT_HOST

CONFIG_DIR = Path.home() / ".config" / "bushdump"
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_PATH = CONFIG_DIR / "state.json"

DEFAULT_OUTPUT_DIR = "~/Pictures/BushDump"
DEFAULT_PASSWORD = "12345678"

CONFIG_TEMPLATE = f"""\
# BushDump config — one entry per camera under [cameras.<name>].
# Run `bushdump add` to detect a camera and append it here automatically.

# Defaults applied to every camera unless overridden in its own section.
output_dir = "{DEFAULT_OUTPUT_DIR}"   # each camera saves to <output_dir>/<name>/
password = "{DEFAULT_PASSWORD}"
camera_host = "{DEFAULT_HOST}"

# Example (delete or edit):
# [cameras.frontgate]
# ble_address = "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
# ssid = "Trail Cam Pro 1234"
"""


@dataclass(slots=True)
class Camera:
    name: str
    ssid: str
    password: str
    camera_host: str
    output_dir: Path
    ble_address: str | None = None


@dataclass(slots=True)
class Config:
    cameras: dict[str, Camera] = field(default_factory=dict)


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(
            f"No config at {path}. Run `bushdump init` (or `bushdump add`) to create one."
        )
    data = tomllib.loads(path.read_text())
    base_dir = Path(data.get("output_dir", DEFAULT_OUTPUT_DIR)).expanduser()
    default_password = data.get("password", DEFAULT_PASSWORD)
    default_host = data.get("camera_host", DEFAULT_HOST)

    cameras: dict[str, Camera] = {}
    for name, section in data.get("cameras", {}).items():
        override = section.get("output_dir")
        output_dir = Path(override).expanduser() if override else base_dir / name
        cameras[name] = Camera(
            name=name,
            ssid=section.get("ssid", ""),
            password=section.get("password", default_password),
            camera_host=section.get("camera_host", default_host),
            output_dir=output_dir,
            ble_address=section.get("ble_address") or None,
        )
    return Config(cameras=cameras)


def write_config_template(path: Path = CONFIG_PATH) -> bool:
    """Write the config template. Returns False (no-op) if a config already exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE)
    return True


def camera_exists(name: str, path: Path = CONFIG_PATH) -> bool:
    if not path.exists():
        return False
    data = tomllib.loads(path.read_text())
    return name in data.get("cameras", {})


def add_camera(
    name: str,
    *,
    ble_address: str | None,
    ssid: str,
    password: str | None = None,
    path: Path = CONFIG_PATH,
) -> None:
    """Append a `[cameras.<name>]` section to the config (creating it if needed)."""
    write_config_template(path)  # no-op if it already exists
    if camera_exists(name, path):
        raise ValueError(f"A camera named {name!r} is already in {path}")
    with path.open("a") as fh:
        fh.write(_camera_section(name, ble_address=ble_address, ssid=ssid, password=password))


# --- sync state ------------------------------------------------------------


def load_state(path: Path = STATE_PATH) -> dict[str, dict[str, int]]:
    """Return {camera_name: {media_type: watermark_dt}}. Empty on first run."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {str(cam): {str(k): int(v) for k, v in d.items()} for cam, d in raw.items()}


def save_state(state: dict[str, dict[str, int]], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


# --- TOML writing (minimal; we only emit one camera section at a time) ------

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _toml_key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _toml_string(name)


def _camera_section(name: str, *, ble_address: str | None, ssid: str, password: str | None) -> str:
    lines = ["", f"[cameras.{_toml_key(name)}]"]
    if ble_address:
        lines.append(f"ble_address = {_toml_string(ble_address)}")
    lines.append(f"ssid = {_toml_string(ssid)}")
    if password is not None:
        lines.append(f"password = {_toml_string(password)}")
    return "\n".join(lines) + "\n"
