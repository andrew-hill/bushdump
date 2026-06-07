"""WiFi on macOS: list nearby networks (CoreWLAN) and join an AP.

Listing SSIDs uses CoreWLAN, which Apple gates behind Location Services — if the
permission isn't granted, scans come back empty and the caller falls back to
manual SSID entry. Joining the camera's AP drops your normal WiFi (the camera AP
has no internet); we don't auto-restore it — you rejoin your usual network
yourself when you're done.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

# --- listing networks (CoreWLAN) -------------------------------------------


def corewlan_available() -> bool:
    """True if CoreWLAN can be imported (pyobjc framework present)."""
    try:
        from CoreWLAN import CWWiFiClient  # noqa: F401

        return True
    except Exception:
        return False


def _scan_once() -> list[str]:
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return []
    try:
        interface = CWWiFiClient.sharedWiFiClient().interface()
        if interface is None:
            return []
        networks, error = interface.scanForNetworksWithName_error_(None, None)
        if error is not None or networks is None:
            return []
        return [n.ssid() for n in networks if n.ssid()]
    except Exception:
        return []


def rank_ssids(ssids: list[str], name_hint: str = "Trail Cam") -> list[str]:
    """Dedupe and sort SSIDs, surfacing likely trail cameras (name_hint) first."""
    hint = name_hint.lower()
    unique = sorted(set(ssids))
    return sorted(unique, key=lambda s: (hint not in s.lower(), s.lower()))


def scan_ssids(name_hint: str = "Trail Cam") -> list[str]:
    """One CoreWLAN scan, ranked. Empty if scanning is unavailable."""
    return rank_ssids(_scan_once(), name_hint)


def watch_ssids(
    seconds: float = 8.0,
    on_found: Callable[[str], None] | None = None,
    name_hint: str = "Trail Cam",
) -> list[str]:
    """Repeatedly scan for `seconds`, calling `on_found(ssid)` as each new network
    appears (the camera AP can take a few seconds to come up). Returns ranked SSIDs.
    """
    seen: set[str] = set()
    deadline = time.monotonic() + seconds
    first = True
    while first or time.monotonic() < deadline:
        first = False
        for ssid in _scan_once():
            if ssid not in seen:
                seen.add(ssid)
                if on_found is not None:
                    on_found(ssid)
        time.sleep(0.5)
    return rank_ssids(list(seen), name_hint)


# --- joining / leaving an AP (networksetup) --------------------------------


def parse_wifi_interface(hardware_ports: str) -> str | None:
    """Find the Wi-Fi device name (e.g. en0) in `networksetup -listallhardwareports`."""
    current_is_wifi = False
    for line in hardware_ports.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            current_is_wifi = "Wi-Fi" in line or "AirPort" in line
        elif line.startswith("Device:") and current_is_wifi:
            return line.split(":", 1)[1].strip()
    return None


def find_wifi_interface() -> str:
    out = subprocess.run(
        ["networksetup", "-listallhardwareports"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    iface = parse_wifi_interface(out)
    if iface is None:
        raise RuntimeError("Could not find a Wi-Fi interface via networksetup")
    return iface


def join(
    ssid: str,
    password: str,
    interface: str | None = None,
    timeout: float = 30.0,
    interval: float = 2.0,
) -> None:
    """Join the AP, retrying until success or `timeout` (handles the AP-boot race)."""
    iface = interface or find_wifi_interface()
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["networksetup", "-setairportnetwork", iface, ssid, password],
            capture_output=True,
            text=True,
        )
        # networksetup prints an error line to stdout but still exits 0, so we
        # treat any non-empty output as failure.
        if result.returncode == 0 and not result.stdout.strip():
            return
        last_err = (result.stdout + result.stderr).strip()
        time.sleep(interval)
    raise RuntimeError(f"Failed to join {ssid!r} within {timeout:.0f}s: {last_err}")
