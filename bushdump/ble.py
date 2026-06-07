"""Wake the camera's WiFi AP over BLE.

Writes the magic value to service 0xFF00 / characteristic 0xFF01, after which
the camera brings up its WiFi access point. See docs/camera-api.md.
"""

from __future__ import annotations

SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
WIFI_ON_PAYLOAD = b"BT_Key_On"  # hex 42-54-5F-4B-65-79-5F-4F-6E


async def wake_wifi(address: str) -> None:
    """Connect to the camera by BLE address and enable its WiFi AP.

    TODO: implement with bleak (BleakClient.write_gatt_char).
    """
    raise NotImplementedError
