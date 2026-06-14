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
- HTTP file download (`/file/<id>/JPG`): ✅ confirmed — hundreds of JPGs,
  ~1.1 MB/s average.
- Sync to completion: ✅ confirmed — 2572 photos, clean final count, no traceback.
- Clock sync (`/cmd/setGmtClock` + `/cmd/info/4`): ✅ confirmed — camera was ~3s
  ahead of laptop; after `bd clock --sync` the clock updated correctly and held.
- Power-off (`/cmd/standby/now`): ✅ confirmed — this model drops the TCP
  connection before completing the HTTP response; the AP goes down as expected.
  The resulting `httpx.RemoteProtocolError` is suppressed in `power_off()`.

### GardePro E8 2.0 Pro — ✅ Confirmed

- BLE peripheral name: `CAM8Z8_<location>_G_E8 2`
- WiFi SSID: `CAM8Z8_<wifi-mac-hex>`
- WPA2: `1234567890` ✅
- `/cmd/info/1`: `{"brand":"GardePro","product":"E8 2.0 Pro","model":"E8V2P","ver":"V9.2.108 MCU V2.67"}`
- `/cmd/info/5` hardware: `h/w 9.4.9.2S.2`, BLE module `TL_v1.0.5_2025.01.08`
- BLE wake char: `6e400004-b5a3-f393-e0a9-e50e24dcca9e`, same as E6PMB.
  Confirmed `OK\r\n` reply via `--probe-all` (consistent across two separate
  sessions). The CLI sometimes shows "(no ack)" — this is a 3s notification
  timeout, not a failure; the AP comes up regardless.
- **Wake reliability**: BLE `connect()` occasionally times out on the first
  attempt (macOS CoreBluetooth race); a second attempt always succeeds. This
  is a BLE distance/timing issue, not a characteristic mismatch.
- HTTP confirmed: `/cmd/info/1..5`, `/cmd/getSetting`, `/cmd/getParaSetting`,
  `/cmd/standby/reset`, `/list/detail/forward/`
- `/cmd/info/2` field variation: uses `voltage` (0–100 scale) and
  `vol_value` (raw mV) instead of `battery`. Both were observed on external
  power, so the meaning at battery-only levels is still unknown. `parse_info2`
  falls back to `voltage` automatically.
- `/cmd/info/4` field variation: uses key `tz` (not `timezone`) for timezone.
- `/cmd/getSetting` has extra fields vs E6PMB: `sound_level`,
  `false_trigger_suppression`, `auxiliary_pir`, `tl_hours_sw1`,
  `temperature_format`, `cellular`, `instant_upload`, `cellular_interval`,
  `cellular_start_time`, `gps_start_time`, `thumbnail_quality`,
  `cell_hd_quality`, `cellular_instant`.
- HTTP photo download (`/file/<id>/JPG`): ✅ confirmed — 1127 JPGs in a
  single run, ~1.4–1.7 MB/s average.
- Sync to completion: ✅ confirmed — 2569 photos, clean final count, no traceback.
- Keep-alive (`/cmd/standby/reset`): ✅ confirmed — held connection across
  thousands of files over multiple hours.
- Resume/watermark: ✅ confirmed — interrupted and resumed correctly from
  the last completed file.
- Clock sync (`/cmd/setGmtClock` + `/cmd/info/4`): ✅ confirmed — camera was ~29s
  behind laptop; after `bd clock --sync` the clock updated correctly and held.
- Power-off (`/cmd/standby/now`): ✅ confirmed — this model completes the
  HTTP response cleanly before the AP drops.
- Videos: ❓ not confirmed — no videos on SD card during test.

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
