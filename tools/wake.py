"""Wake a camera's WiFi AP over BLE.

Default is the Linkiing/Telink GardePro platform (BushDump-supported):
  - write `AT+WAKEPULSE=10\\r\\n` to NUS char `6e400004-...`
  - expect notification `OK\\r\\n` back

`--legacy` switches to the original GardePro/Dsoon OEM:
  - write `BT_Key_On` to char `0000ff01-...`
  - no reply expected

`--probe-all` ignores the platform default and writes the (chosen) wake
payload to every writable characteristic on the device, listening for
notifications on every notify-capable characteristic. Useful when you have
an unknown camera and don't yet know which characteristic accepts the wake.

Usage:
  uv run python tools/wake.py <ble-address>
  uv run python tools/wake.py <ble-address> --legacy
  uv run python tools/wake.py <ble-address> --probe-all
"""

from __future__ import annotations

import argparse
import asyncio

from bleak import BleakClient, BleakScanner

LINKIING_CHAR = "6e400004-b5a3-f393-e0a9-e50e24dcca9e"
LINKIING_PAYLOAD = b"AT+WAKEPULSE=10\r\n"

LEGACY_CHAR = "0000ff01-0000-1000-8000-00805f9b34fb"
LEGACY_PAYLOAD = b"BT_Key_On"


def on_notify(label: str, sender, data: bytearray) -> None:
    try:
        printable = repr(data.decode("utf-8"))
    except UnicodeDecodeError:
        printable = data.hex()
    print(f"  <- [{label}] {printable}")


async def wake_one(client: BleakClient, char: str, payload: bytes) -> None:
    print(f"-> writing {payload!r} to {char}")
    await client.write_gatt_char(char, payload, response=True)


async def probe_all(client: BleakClient, payload: bytes) -> None:
    writable: list[str] = []
    notifiable: list[str] = []
    for service in client.services:
        for char in service.characteristics:
            if "write" in char.properties or "write-without-response" in char.properties:
                writable.append(char.uuid)
            if "notify" in char.properties:
                notifiable.append(char.uuid)

    for uuid in notifiable:
        try:
            await client.start_notify(uuid, lambda s, d, u=uuid: on_notify(u, s, d))
        except Exception as e:
            print(f"  (couldn't subscribe {uuid}: {e})")

    for uuid in writable:
        print(f"-> writing {payload!r} to {uuid}")
        try:
            await client.write_gatt_char(uuid, payload, response=True)
        except Exception as e:
            print(f"   write failed: {e}")
            continue
        await asyncio.sleep(3.0)
        print("   (check WiFi list — did an AP appear?)")


async def main(address: str, legacy: bool, probe: bool) -> None:
    print(f"Scanning for {address}...")
    device = await BleakScanner.find_device_by_address(address, timeout=15.0)
    if device is None:
        print("  Not found. Is the camera powered on and nearby?")
        return
    print(f"  Found: {device.name or '(unnamed)'}")

    char = LEGACY_CHAR if legacy else LINKIING_CHAR
    payload = LEGACY_PAYLOAD if legacy else LINKIING_PAYLOAD

    async with BleakClient(device, timeout=20.0) as client:
        if probe:
            await probe_all(client, payload)
        else:
            try:
                await client.start_notify(char, lambda s, d: on_notify(char, s, d))
            except Exception as e:
                print(f"  (couldn't subscribe {char}: {e})")
            await wake_one(client, char, payload)
            await asyncio.sleep(5.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("address", help="BLE address (macOS CoreBluetooth UUID or MAC)")
    parser.add_argument("--legacy", action="store_true", help="use the GardePro/Dsoon OEM wake")
    parser.add_argument("--probe-all", action="store_true", help="write to every writable char")
    args = parser.parse_args()
    asyncio.run(main(args.address, args.legacy, args.probe_all))
