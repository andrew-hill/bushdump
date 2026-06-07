"""Load user config and persist sync state.

Config lives at ~/.config/bushdump/config.toml (camera details, output dir).
Sync state (the per-type `dt` watermark) lives next to it as state.json. Neither
is committed — see .gitignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "bushdump"
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_PATH = CONFIG_DIR / "state.json"


@dataclass(slots=True)
class Config:
    ssid: str
    password: str
    camera_host: str
    output_dir: Path

    # TODO: load_config() / load_state() / save_state() once CLI is fleshed out.
