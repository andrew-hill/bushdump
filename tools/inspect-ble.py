"""Connect to a BLE peripheral and dump its GATT table + Device Information.

Use this first to figure out what platform a camera is on:
  - Linkiing/Telink (BushDump-supported): Nordic UART Service `6e400001-...`
  - Legacy GardePro/Dsoon OEM: a service at `0000ff00-...`
  - Something else: probably a different OEM; you'll need to reverse-engineer
    the wake protocol from scratch.

Usage: uv run python tools/inspect-ble.py <ble-address>
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakClient, BleakScanner

DEVICE_INFO_CHARS = {
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Rev",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Rev",
    "00002a23-0000-1000-8000-00805f9b34fb": "System ID",
    "00002a50-0000-1000-8000-00805f9b34fb": "PnP ID",
}

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
LEGACY_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"


def classify(service_uuids: set[str]) -> str:
    if NUS_SERVICE in service_uuids:
        return "Linkiing/Telink (BushDump-supported — try tools/wake.py)"
    if LEGACY_SERVICE in service_uuids:
        return "Legacy GardePro/Dsoon OEM (try tools/wake.py --legacy)"
    return "Unknown platform — neither Nordic UART nor 0xFF00 service present"


async def inspect(address: str) -> None:
    print(f"Scanning for {address}...")
    device = await BleakScanner.find_device_by_address(address, timeout=15.0)
    if device is None:
        print("  Not found. Is the camera powered on and nearby?")
        return
    print(f"  Found: {device.name or '(unnamed)'}\n")

    async with BleakClient(device, timeout=20.0) as client:
        service_uuids: set[str] = set()
        for service in client.services:
            service_uuids.add(service.uuid)
            print(f"Service {service.uuid}  ({service.description})")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"  Char  {char.uuid}  [{props}]  ({char.description})")
                for desc in char.descriptors:
                    print(f"    Desc {desc.uuid}")

        print("\n--- Device Info ---")
        for uuid, label in DEVICE_INFO_CHARS.items():
            try:
                value = await client.read_gatt_char(uuid)
                try:
                    decoded = value.decode("utf-8").rstrip("\x00")
                except UnicodeDecodeError:
                    decoded = value.hex()
                print(f"  {label}: {decoded!r}")
            except Exception as e:
                print(f"  {label}: (read failed: {e})")

        print(f"\n--- Likely platform ---\n  {classify(service_uuids)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run python tools/inspect-ble.py <ble-address>")
        raise SystemExit(2)
    asyncio.run(inspect(sys.argv[1]))
