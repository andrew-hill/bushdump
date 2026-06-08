"""BLE: list nearby devices and wake a camera's WiFi AP.

Waking writes an AT command to the camera's Nordic UART TX-capable
characteristic, after which the camera brings up its WiFi access point. See
docs/camera-api.md.

On macOS, BLE peripherals are identified by a CoreBluetooth UUID (not a MAC).
`discover` lists everything nearby so you can pick yours in `bushdump register`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from bleak import BleakClient, BleakError, BleakScanner

WAKE_CHARACTERISTIC_UUID = "6e400004-b5a3-f393-e0a9-e50e24dcca9e"
WIFI_ON_PAYLOAD = b"AT+WAKEPULSE=10\r\n"


async def discover(timeout: float = 8.0) -> list[tuple[str, str | None]]:
    """Scan for BLE devices, returning (address, name) for each one found."""
    devices = await BleakScanner.discover(timeout=timeout)
    return [(d.address, d.name) for d in devices]


async def watch(
    seconds: float = 10.0,
    on_found: Callable[[str, str | None], None] | None = None,
) -> list[tuple[str, str | None]]:
    """Live-scan for BLE devices, calling `on_found(address, name)` as each new
    device appears. Returns the accumulated (address, name) list after `seconds`.
    """
    found: dict[str, str | None] = {}

    def callback(device, adv) -> None:
        if device.address not in found:
            name = device.name or (adv.local_name if adv else None)
            found[device.address] = name
            if on_found is not None:
                on_found(device.address, name)

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.stop()
    return list(found.items())


async def wake_wifi(address: str, timeout: float = 20.0) -> bytes | None:
    """Connect to the camera by BLE address and enable its WiFi AP.

    On macOS, `BleakClient(address)` without a recent scan can silently appear
    to succeed but deliver no writes — pre-scanning refreshes the
    CoreBluetooth cache.

    Returns the camera's notification reply (typically `b'OK\\r\\n'` on the
    Linkiing platform) or `None` if no reply arrived in 3s. Callers can use
    this to confirm the camera actually accepted the wake.
    """
    device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if device is None:
        raise RuntimeError(f"BLE device {address} not found within {timeout:.0f}s")

    reply: bytes | None = None
    reply_event = asyncio.Event()

    def on_notify(_sender, data: bytearray) -> None:
        nonlocal reply
        reply = bytes(data)
        reply_event.set()

    async with BleakClient(device, timeout=timeout) as client:
        with contextlib.suppress(BleakError):
            await client.start_notify(WAKE_CHARACTERISTIC_UUID, on_notify)
        await client.write_gatt_char(WAKE_CHARACTERISTIC_UUID, WIFI_ON_PAYLOAD, response=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(reply_event.wait(), timeout=3.0)
    return reply
