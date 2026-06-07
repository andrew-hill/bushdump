# Camera models — what we know

A registry of the camera models BushDump has been verified against, plus
models referenced by the upstream reverse-engineering sources that *should*
work but haven't been confirmed first-hand.

See [`camera-api.md`](camera-api.md) for the protocol details. Update this
file whenever a new camera is verified.

## Confirmation legend

- ✅ **Confirmed** — we've actually seen this work on hardware we own.
- 🟡 **Partial** — some steps verified, others inferred. Specifics noted.
- 📚 **Reported** — claim comes from a community source, not first-hand.

## Linkiing/Telink platform

These all share the `AT+WAKEPULSE=10\r\n` BLE wake, `192.168.8.1:8080` HTTP,
WPA2 default `1234567890`, and the `/cmd` + `/list` + `/file` endpoint
shape described in `camera-api.md`. Per-model deviations called out below.

### GardePro E6PMB — ✅ Confirmed

- BLE peripheral name: `CAM8Z8_<location>_G_E6PMB` (location is user-set)
- WiFi SSID: `CAM8Z8_<wifi-mac-hex>`
- WPA2: `1234567890` (factory default — `password: -1` in `/cmd/getSetting`
  means "no user override")
- Firmware seen: `V6.2.115` (MCU `V213`), BLE module `SL_v0.3.2_2022.09.15`
- BLE wake char: `6e400004-b5a3-f393-e0a9-e50e24dcca9e`, expects `OK\r\n`
- HTTP confirmed: `/cmd/info/1..5`, `/cmd/getSetting`, `/cmd/getParaSetting`,
  `/cmd/standby/reset`, `/list/detail/forward/0/10`
- `type` enum seen: `1` (photo). No videos on the SD card yet, so `2`
  unconfirmed but follows the convention.
- HTTP file download (`/file/<id>/JPG`) not yet exercised end-to-end.

### GardePro E8 2.0 Pro — 🟡 Partial

- BLE peripheral name: `CAM8Z8_<location>_G_E8`
- WiFi SSID: `CAM8Z8_<wifi-mac-hex>` (e.g. `CAM8Z8_A4C13896B3B0`)
- WPA2 join: ✅ confirmed with `1234567890`
- BLE wake: not yet directly probed; AP came up after some interaction —
  assume it's the same as E6PMB until probed.
- HTTP API: not yet probed; assumed identical to E6PMB pending verification.

### GardePro E9P — 📚 Reported

Source: [vondruska/gardepro-fetcher](https://github.com/vondruska/gardepro-fetcher).

- BLE wake char: reported at handle `0x001e` (which is a different
  characteristic from our E6PMB's `6e400004-...` — handle numbering is
  per-connection, so this may resolve to the same UUID or a different one
  on E9P). Same `AT+WAKEPULSE=10\r\n` payload.
- WPA2 default: `1234567890`
- HTTP: `192.168.8.1:8080`, same `/cmd` + `/list` + `/file` shape.

### Other models referenced by sources — 📚 Reported

[fede2cr/camtrap-control](https://github.com/fede2cr/camtrap-control) is the
broader independent Python client for this platform; check that repo's
README and issues for the list of models its author and contributors have
exercised. Treat anything that matches the
`CAM8Z8_*` SSID pattern and presents the Linkiing manufacturer string in
`/cmd/info/5` as a strong signal that this protocol applies.

## Legacy OEM platform (`0xFF00` BLE / `192.168.1.8`)

No longer supported by BushDump — see "Re-adding legacy OEM support" in
`camera-api.md` for the spec if a legacy camera ever shows up.

### Original GardePro / Dsoon OEM — 📚 Reported

Sources:
- [geekitguide.com part 2](https://geekitguide.com/wifi-ble-trailcam-investigation-part-2/)
- [fearthis4/wifi-ble-trailcam-investigations](https://github.com/fearthis4/wifi-ble-trailcam-investigations)

- BLE wake: `BT_Key_On` to service `0xFF00` / char `0xFF01`
- WiFi SSID pattern: `Trail Cam Pro ****`
- WPA2 default: `12345678`
- HTTP: `192.168.1.8:80`, `/SetMode?Storage` then `/Storage?...` endpoints
