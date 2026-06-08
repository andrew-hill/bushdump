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
    """List nearby SSIDs. Calls CoreWLAN's active scan when it'll fire, but
    also reads `cachedScanResults` — macOS rate-limits explicit scans so the
    cache (kept fresh by the OS itself) is the more reliable source when a
    new network has just come up.
    """
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return []
    try:
        interface = CWWiFiClient.sharedWiFiClient().interface()
        if interface is None:
            return []
        # Best-effort active scan; macOS may throttle this to once every ~30s
        # but a successful one populates the cache.
        interface.scanForNetworksWithName_error_(None, None)
        cached = interface.cachedScanResults()
        if not cached:
            return []
        return [n.ssid() for n in cached if n.ssid()]
    except Exception:
        return []


_CAMERA_SSID_HINTS = ("cam8z8", "trail cam")


def is_likely_camera_ssid(ssid: str) -> bool:
    low = ssid.lower()
    return any(h in low for h in _CAMERA_SSID_HINTS)


def rank_ssids(ssids: list[str]) -> list[str]:
    """Dedupe and sort SSIDs, surfacing likely trail cameras first."""
    unique = sorted(set(ssids))
    return sorted(unique, key=lambda s: (not is_likely_camera_ssid(s), s.lower()))


def scan_ssids() -> list[str]:
    """One CoreWLAN scan, ranked. Empty if scanning is unavailable."""
    return rank_ssids(_scan_once())


def watch_ssids(
    seconds: float = 8.0,
    on_found: Callable[[str], None] | None = None,
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
    return rank_ssids(list(seen))


def wait_for_ssid(ssid: str, timeout: float = 20.0) -> bool:
    """Poll CoreWLAN until `ssid` appears or `timeout` expires. Returns True if found."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ssid in _scan_once():
            return True
        time.sleep(0.5)
    return False


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


def current_ssid(interface: str | None = None) -> str | None:
    """Return the SSID the machine is currently connected to, or None."""
    try:
        iface = interface or find_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportnetwork", iface],
            capture_output=True,
            text=True,
        )
        line = result.stdout.strip()
        prefix = "Current Wi-Fi Network: "
        if line.startswith(prefix):
            return line[len(prefix) :]
    except Exception:
        pass
    return None


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
