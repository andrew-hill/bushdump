"""WiFi on macOS: list nearby networks (CoreWLAN) and join an AP.

Listing SSIDs uses CoreWLAN, which Apple gates two separate ways. Location
Services has always been required to *scan*. Since macOS 26 reading a network's
*name* additionally needs the restricted `com.apple.developer.networking.wifi-info`
entitlement, so on Tahoe scans succeed and come back nameless no matter what
Location is set to — see `REDACTED_HINT`. `diagnose_scan` exists to keep those
apart, and `ssid_present` works under redaction by asking about one name rather
than reading any.

Joining the camera's AP drops your normal WiFi (the camera AP has no internet);
we don't auto-restore it — you rejoin your usual network yourself when you're
done.
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


REDACTED_HINT = (
    "macOS is hiding WiFi network names from this process. Since macOS 26 that "
    "needs Apple's `com.apple.developer.networking.wifi-info` entitlement, which "
    "requires a provisioning profile we don't have — Location permission alone is "
    "not enough. Join the AP from the macOS WiFi menu and enter the SSID manually."
)


def diagnose_scan(framework: bool, seen: int | None, named: int) -> str | None:
    """Explain why a scan came back unusable, or None if it's fine.

    Kept pure so the distinctions are testable: the framework is missing, the
    radio never scanned, the names are redacted, or there is genuinely nothing
    in range. Conflating those is what makes a permission gate look like a
    broken API — or a switched-off radio look like empty air.

    `seen` is None when no scan completed at all, which is not the same as a
    scan that completed and found nothing.
    """
    if not framework:
        return "CoreWLAN unavailable — is the pyobjc WiFi framework installed?"
    if named:
        return None
    if seen is None:
        return "Could not scan for WiFi networks — is WiFi switched off?"
    if seen:
        return REDACTED_HINT
    return "No WiFi networks in range."


def ssid_present(ssid: str) -> bool | None:
    """Is an AP with exactly this name in range? None means "couldn't tell".

    macOS 26 won't report a network's name, but CoreWLAN still *filters* by one:
    scanning for a specific SSID returns the matching networks (names blanked)
    and nothing at all for a name that isn't there. So a known AP can be
    confirmed without ever reading a name.

    Scans are rate-limited hard — roughly half come back `Resource busy` even
    seconds apart — so a scan we couldn't complete returns None, never False.
    Treating None as absence would re-wake a camera that is already up.
    """
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return None
    try:
        interface = CWWiFiClient.sharedWiFiClient().interface()
        if interface is None:
            return None
        networks, err = interface.scanForNetworksWithName_error_(ssid, None)
        if err is not None:
            return None
        return bool(networks)
    except Exception:
        return None


def _scan_raw() -> tuple[int | None, list[str]]:
    """Scan once, returning (networks seen, readable SSIDs).

    `networks seen` is None when the scan never ran — no framework, no WiFi
    interface (radio switched off), or CoreWLAN raised. Zero means it ran and
    the air really was empty.

    Those two disagree under redaction: a perfectly healthy scan comes back with
    plenty of networks carrying valid RSSI/channel/security and not one name.

    Fires a best-effort active scan (macOS may throttle it to ~once every 30s)
    but reads `cachedScanResults` — the OS keeps the cache fresh itself, so it
    is the more reliable source when a new network has just come up.
    """
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return None, []
    try:
        interface = CWWiFiClient.sharedWiFiClient().interface()
        if interface is None:
            return None, []
        interface.scanForNetworksWithName_error_(None, None)
        cached = interface.cachedScanResults()
        if not cached:
            return 0, []
        return len(cached), [n.ssid() for n in cached if n.ssid()]
    except Exception:
        return None, []


def saved_ssids() -> list[str]:
    """SSIDs macOS has saved for this interface.

    Not a scan — these are remembered networks, in range or not. But
    `networksetup` does not redact them, so a camera you have joined before is
    still nameable on macOS 26. Empty if the lookup fails.
    """
    try:
        iface = find_wifi_interface()
        out = subprocess.run(
            ["networksetup", "-listpreferredwirelessnetworks", iface],
            capture_output=True,
            text=True,
        ).stdout
    except Exception:
        return []
    return rank_ssids([ln.strip() for ln in out.splitlines()[1:] if ln.strip()])


_CAMERA_SSID_HINTS = ("cam8z8", "trail cam")


def is_likely_camera_ssid(ssid: str) -> bool:
    low = ssid.lower()
    return any(h in low for h in _CAMERA_SSID_HINTS)


def rank_ssids(ssids: list[str]) -> list[str]:
    """Dedupe and sort SSIDs, surfacing likely trail cameras first."""
    unique = sorted(set(ssids))
    return sorted(unique, key=lambda s: (not is_likely_camera_ssid(s), s.lower()))


def watch_ssids(
    seconds: float = 8.0,
    on_found: Callable[[str], None] | None = None,
) -> tuple[list[str], str | None]:
    """Repeatedly scan for `seconds`, calling `on_found(ssid)` as each new network
    appears (the camera AP can take a few seconds to come up).

    Returns (ranked SSIDs, problem). `problem` explains an empty list — missing
    framework, macOS 26 name redaction, or genuinely empty air — diagnosed from
    the busiest scan of the watch itself, not a separate scan that might miss.
    """
    names: set[str] = set()
    most_seen: int | None = None
    deadline = time.monotonic() + seconds
    first = True
    while first or time.monotonic() < deadline:
        first = False
        seen, found = _scan_raw()
        if seen is not None:
            most_seen = seen if most_seen is None else max(most_seen, seen)
        for ssid in found:
            if ssid not in names:
                names.add(ssid)
                if on_found is not None:
                    on_found(ssid)
        time.sleep(0.5)
    ranked = rank_ssids(list(names))
    return ranked, diagnose_scan(corewlan_available(), most_seen, len(ranked))


def wait_for_ssid(ssid: str, timeout: float = 40.0, interval: float = 3.0) -> bool | None:
    """Poll until `ssid` is in range. True / False / None ("couldn't tell").

    The old version listed every SSID and looked for a match, which on macOS 26
    never matched anything — that's what made `bushdump wake` hang forever at
    site. This asks about one name instead, which still works under redaction.

    Timeout defaults generously: even after a camera acks its BLE wake, the AP
    took ~25s to become *detectable* — part radio boot, part macOS scan
    scheduling, and the two can't be told apart from here.
    """
    deadline = time.monotonic() + timeout
    saw_unknown = False
    while True:
        present = ssid_present(ssid)
        if present is True:
            return True
        if present is None:
            saw_unknown = True
        if time.monotonic() >= deadline:
            return None if saw_unknown else False
        time.sleep(interval)


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
