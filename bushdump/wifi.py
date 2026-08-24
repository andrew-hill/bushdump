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
from dataclasses import dataclass

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

    Scans are rate-limited hard, so a scan we couldn't complete returns None,
    never False. Treating None as absence would re-wake a camera that is
    already up.

    Measured at bushdump's own 3s poll interval (macOS 26, 2026-08-24): a real
    scan takes ~7s and answers definitely, then the next ~3 calls come back
    `Resource busy` instantly, and the cycle repeats — about two thirds unknown
    overall. Asking about a name macOS considers implausible is *not*
    representative: those return instantly from cache without ever scanning, and
    never throttle. So callers must expect unknowns interleaved with real
    answers, and must not conclude anything from a run of them.
    """
    try:
        from CoreWLAN import CWWiFiClient
    except Exception:
        return None
    try:
        interface = CWWiFiClient.sharedWiFiClient().interface()
        # A switched-off radio still hands back a live CWInterface — power is a
        # property of it, not its absence — so ask before reading anything into
        # an empty result.
        if interface is None or not interface.powerOn():
            return None
        networks, err = interface.scanForNetworksWithName_error_(ssid, None)
        if err is not None:
            return None
        return bool(networks)
    except Exception:
        return None


def _seen_from_scan(powered: bool, scan_failed: bool, cached: int) -> int | None:
    """Turn one raw CoreWLAN scan into `diagnose_scan`'s three-state `seen`.

    None means no scan happened: the radio is off, or the scan errored and the
    result cache had nothing to fall back on. Zero means a scan really did run
    and the air was empty. Conflating the two is what made `bushdump wifi`
    answer "No WiFi networks in range." with WiFi switched off — a live
    `CWInterface` comes back either way, so emptiness alone proves nothing.
    """
    if not powered:
        return None
    if cached:
        return cached
    return None if scan_failed else 0


def _scan_raw() -> tuple[int | None, list[str]]:
    """Scan once, returning (networks seen, readable SSIDs).

    `networks seen` is None when the scan never ran — no framework, no WiFi
    interface, the radio switched off, or the scan itself errored with nothing
    cached to fall back on. Zero means it ran and the air really was empty.

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
        powered = bool(interface.powerOn())
        _, err = interface.scanForNetworksWithName_error_(None, None)
        cached = interface.cachedScanResults() or []
        seen = _seen_from_scan(powered, err is not None, len(cached))
        if seen is None:
            return None, []
        return seen, [n.ssid() for n in cached if n.ssid()]
    except Exception:
        return None, []


_CAMERA_SSID_HINTS = ("cam8z8", "trail cam")


def is_likely_camera_ssid(ssid: str) -> bool:
    low = ssid.lower()
    return any(h in low for h in _CAMERA_SSID_HINTS)


def rank_ssids(ssids: list[str]) -> list[str]:
    """Dedupe and sort SSIDs, surfacing likely trail cameras first."""
    unique = sorted(set(ssids))
    return sorted(unique, key=lambda s: (not is_likely_camera_ssid(s), s.lower()))


@dataclass(frozen=True)
class ScanOutcome:
    """What one watch window produced, and whether it managed to scan at all.

    `scanned` is the difference between "we looked and the air was quiet" and
    "we never got to look". Callers need it for their exit codes: under macOS 26
    redaction an empty `ssids` is the permanent, expected state and not a
    failure, whereas a radio that never scanned is.
    """

    ssids: list[str]
    problem: str | None
    scanned: bool


def watch_ssids(
    seconds: float = 8.0,
    on_found: Callable[[str], None] | None = None,
) -> ScanOutcome:
    """Repeatedly scan for `seconds`, calling `on_found(ssid)` as each new network
    appears (the camera AP can take a few seconds to come up).

    `problem` explains an empty list — missing framework, macOS 26 name
    redaction, or genuinely empty air — diagnosed from the busiest scan of the
    watch itself, not a separate scan that might miss.
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
    framework = corewlan_available()
    return ScanOutcome(
        ssids=ranked,
        problem=diagnose_scan(framework, most_seen, len(ranked)),
        scanned=framework and most_seen is not None,
    )


def wait_for_ssid(ssid: str, timeout: float = 40.0, interval: float = 3.0) -> bool | None:
    """Poll until `ssid` is in range. True / False / None ("couldn't tell").

    The old version listed every SSID and looked for a match, which on macOS 26
    never matched anything — that's what made `bushdump wake` hang forever at
    site. This asks about one name instead, which still works under redaction.

    Timeout defaults generously: even after a camera acks its BLE wake, the AP
    took ~25s to become *detectable* — part radio boot, part macOS scan
    scheduling, and the two can't be told apart from here.

    Only report None when *no* poll in the whole window managed a real answer.
    Throttled scans are the common case, not the exception — see `ssid_present`
    — so treating "we saw an unknown at some point" as the verdict discarded
    every definite answer that came with it, and this returned "couldn't tell"
    for a camera it had confirmed absent three times over.
    """
    deadline = time.monotonic() + timeout
    answered = False
    while True:
        present = ssid_present(ssid)
        if present is True:
            return True
        if present is False:
            answered = True
        if time.monotonic() >= deadline:
            return False if answered else None
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


def join_succeeded(returncode: int, stdout: str) -> bool:
    """Did `networksetup -setairportnetwork` actually join?

    It reports failure on *stdout* and still exits 0 — "Could not find network
    X." comes back as a clean exit — so the exit status alone always reads as
    success. Any output at all means it did not join. That error line is also
    one of the few SSID-bearing strings macOS 26 does not redact, so it is worth
    keeping intact for the caller to report.
    """
    return returncode == 0 and not stdout.strip()


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
        if join_succeeded(result.returncode, result.stdout):
            return
        last_err = (result.stdout + result.stderr).strip()
        time.sleep(interval)
    raise RuntimeError(f"Failed to join {ssid!r} within {timeout:.0f}s: {last_err}")
