"""Hunt for a camera's WiFi SSID in places other than a WiFi scan.

macOS 26 redacts SSIDs from every scanning API, so `register` can no longer show
a live list of networks. This asks the camera itself instead, from two angles:

  1. BLE  — dump every readable GATT characteristic, hunting for the WiFi MAC.
            The SSID is `CAM8Z8_<wifi-mac-hex>`, so the MAC is as good as the name.
  2. HTTP — dump the settings/info endpoints, hunting for a self-reported SSID.

On site, just run:

    uv run python tools/probe-ssid-sources.py

No arguments needed — it probes every configured camera and writes a timestamped
log to `scratch/`. Safe to run repeatedly; each run is a separate file, so you
can diff them to see whether anything changes over time.

    uv run python tools/probe-ssid-sources.py --ble-only    # skip the WiFi part
    uv run python tools/probe-ssid-sources.py --http-only   # already on an AP

Anything MAC- or SSID-shaped is flagged `<-- LOOK`.

The only thing written to the camera is the standard BLE wake — the same one
`bushdump sync` sends — because its WiFi AP has to be up before you can join it.
GATT is dumped both before and after that wake: the WiFi MAC may only appear
once the radio is actually on, so the diff between the two is itself a clue.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

from bushdump.camera import DEFAULT_HOST

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = REPO_ROOT / "scratch"

# A WiFi MAC rendered any of the ways a device might plausibly report it.
MAC_RE = re.compile(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}|\b[0-9A-Fa-f]{12}\b")
SSID_RE = re.compile(r"CAM8Z8_\w+")


class Tee:
    """Write to the terminal and the log file at once.

    On site you want to watch it happen *and* keep the transcript — and you may
    not have a network to paste it anywhere afterwards.
    """

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", encoding="utf-8")

    def write(self, text: str) -> None:
        sys.__stdout__.write(text)
        self._file.write(text)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def show(label: str, value: str) -> None:
    """Print a probe result, flagging anything MAC- or SSID-shaped."""
    found = set(MAC_RE.findall(value)) | set(SSID_RE.findall(value))
    flag = f"   <-- LOOK: {', '.join(sorted(found))}" if found else ""
    print(f"  {label}: {value[:300]}{flag}")


def resolve_targets(targets: list[str]) -> list[tuple[str, str]]:
    """Turn camera names into (label, ble_address); bare addresses pass through.

    No argument means every configured camera with a stored address — on site
    you want one command, not a lookup.
    """
    from bushdump import config

    cameras = config.load_config().cameras
    if not targets:
        return [(c.name, c.ble_address) for c in cameras.values() if c.ble_address]
    resolved = []
    for name in targets:
        cam = cameras.get(name)
        if cam is None:
            # Not a configured name — assume it's a bare BLE address.
            resolved.append((name, name))
        elif cam.ble_address:
            resolved.append((cam.name, cam.ble_address))
        else:
            # Say so plainly; passing None to bleak just prints "scan failed".
            print(f"  {cam.name}: no BLE address configured — skipping BLE probe.")
    return resolved


async def probe_ble(address: str) -> None:
    """Dump the GATT table and every readable characteristic.

    Every step is wrapped: a camera that wanders out of range mid-dump must not
    take the run down, because the next camera may still be reachable.
    """
    from bleak import BleakScanner

    print(f"\n=== BLE: {address} ===")
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=20.0)
    except Exception as exc:
        print(f"  scan failed: {exc!r}")
        return
    if device is None:
        print("  not found over BLE — out of range, or asleep. Try again closer.")
        return

    print(f"  found {device.name!r}")
    show("advertised name", device.name or "")

    await _dump_with_retries(device, "before wake")

    # Bring the AP up, or there is nothing for you to join at the prompt.
    print("\n  --- waking WiFi ---")
    try:
        from bushdump import ble

        reply = await ble.wake_wifi(address)
        if reply is None:
            print("  no ack — WiFi may still be coming up (the ack is unreliable)")
        else:
            print(f"  camera ack: {reply.decode('utf-8', 'replace').strip()!r}")
    except Exception as exc:
        print(f"  wake failed: {exc!r}")

    # The WiFi MAC may only be populated once the radio is on.
    await asyncio.sleep(3.0)
    await _dump_with_retries(device, "after wake")


async def _dump_with_retries(device, label: str) -> None:
    """Dump GATT, retrying — these connects fail intermittently."""
    print(f"\n  --- GATT {label} ---")
    for attempt in range(1, 4):
        try:
            await _dump_gatt(device)
            return
        except Exception as exc:
            print(f"  connect/dump attempt {attempt}/3 failed: {exc!r}")
            await asyncio.sleep(2.0)
    print("  giving up on this dump — move closer and rerun.")


async def _dump_gatt(device) -> None:
    """Connect and print every service, characteristic and readable value."""
    from bleak import BleakClient

    async with BleakClient(device, timeout=25.0) as client:
        for service in client.services:
            print(f"\n  Service {service.uuid} ({service.description})")
            for char in service.characteristics:
                props = ",".join(char.properties)
                if "read" not in char.properties:
                    print(f"    {char.uuid} [{props}] ({char.description}) — not readable")
                    continue
                try:
                    raw = bytes(await client.read_gatt_char(char.uuid))
                except Exception as exc:
                    print(f"    {char.uuid} [{props}] read failed: {exc!r}")
                    continue
                text = raw.decode("utf-8", "replace").strip()
                show(f"  {char.uuid} ({char.description})", f"{text!r} hex={raw.hex()}")


def probe_http(host: str) -> None:
    """Dump the settings and info endpoints, hunting for a self-reported SSID."""
    from bushdump.camera import CameraClient

    print(f"\n=== HTTP: {host} ===")
    try:
        with CameraClient(host) as client:
            if not client.wait_until_ready(timeout=20.0):
                print("  no answer — are you on the camera's AP?")
                return
            for path in ("/cmd/getSetting", "/cmd/getParaSetting"):
                try:
                    show(path, client._client.get(path).text)
                except Exception as exc:
                    print(f"  {path} failed: {exc!r}")
            # /cmd/info/N — N varies by model, so sweep a small range.
            for n in range(8):
                try:
                    show(f"/cmd/info/{n}", client._client.get(f"/cmd/info/{n}").text)
                except Exception as exc:
                    print(f"  /cmd/info/{n} failed: {exc!r}")
    except Exception as exc:
        print(f"  HTTP probe failed: {exc!r}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Hunt for camera SSIDs via BLE GATT and the camera's HTTP API.",
        epilog="No arguments: probe every configured camera. Log lands in scratch/.",
    )
    ap.add_argument("targets", nargs="*", help="camera names or BLE addresses")
    ap.add_argument("--ble-only", action="store_true", help="dump GATT, skip the WiFi part")
    ap.add_argument("--http-only", action="store_true", help="skip BLE; already on an AP")
    ap.add_argument(
        "--host", default=DEFAULT_HOST, help=f"camera HTTP host (default {DEFAULT_HOST})"
    )
    args = ap.parse_args()

    SCRATCH_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = SCRATCH_DIR / f"probe-ssid-{stamp}.log"
    tee = Tee(log_path)
    sys.stdout = tee  # type: ignore[assignment]
    try:
        print(f"# probe-ssid-sources  {datetime.now().isoformat(timespec='seconds')}")
        print(f"# log: {log_path}")

        if not args.http_only:
            for label, address in resolve_targets(args.targets):
                print(f"\n########## {label} ##########")
                asyncio.run(probe_ble(address))

        if not args.ble_only:
            while True:
                sys.stdout = sys.__stdout__  # type: ignore[assignment]
                answer = input("\nJoin a camera's AP from the WiFi menu, then Enter (q = done): ")
                sys.stdout = tee  # type: ignore[assignment]
                if answer.strip().lower() == "q":
                    break
                probe_http(args.host)

        print("\nAnything flagged 'LOOK' is a candidate.")
        print("Record findings in docs/camera-api.md; the log above is saved.")
    finally:
        sys.stdout = sys.__stdout__  # type: ignore[assignment]
        tee.close()

    print(f"\nSaved: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
