# Trail camera WiFi/BLE API (reverse engineered)

BushDump targets the **Linkiing/Telink-based GardePro** platform (E6PMB,
E8 2.0 Pro, E9P, ...). Cameras advertise a BLE peripheral plus a private WiFi
AP; communication is unencrypted HTTP over the AP (LAN-only, no internet).

See [`camera-models.md`](camera-models.md) for the registry of which models
this doc has been verified against.

## Step 1 — Enable WiFi via BLE

Connect to the camera's BLE peripheral and write the AT command to its Nordic
UART **TX-capable** characteristic (note: *not* the standard NUS RX):

- **Service UUID**: `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART)
- **Characteristic UUID**: `6e400004-b5a3-f393-e0a9-e50e24dcca9e` (write, notify)
- **Payload**: ASCII `AT+WAKEPULSE=10\r\n` (hex `41542b57414b4550554c53453d31300d0a`)
- **Expected reply**: `OK\r\n` via notification on the same characteristic

The WiFi AP comes up ~1–2 seconds after the OK.

- **SSID format**: `CAM8Z8_<wifi-mac-hex>` (e.g. `CAM8Z8_385CFB2540D4`)
- **WPA2 password**: `1234567890` (factory default; user-settable via the
  vendor app — assume the default unless the user told us otherwise)

## Step 2 — HTTP API

Gateway is `http://192.168.8.1:8080`. All responses are JSON with a
`{"code": 0, "data": ...}` envelope. There's no "enter storage mode" step;
the HTTP server is up as soon as the AP is.

```
GET /cmd/info/1                          # brand/product/version
GET /cmd/info/2                          # battery, temperature, ext power
GET /cmd/info/3                          # SD: total/used, photo/video count
GET /cmd/info/4                          # clock + timezone, e.g. {"clock":"2026-06-14 16:58:55","tz":"Australia/Sydney"}
POST /cmd/setGmtClock {"data":"YYYY-MM-DD HH:MM:SS"}   # set clock in UTC; camera applies tz for display (firmware variant A)
POST /cmd/setGmtClock2 {"data":"YYYY-MM-DD HH:MM:SS"}  # set clock (firmware variant B)
GET /cmd/info/5                          # extended HW/FW/BLE/battery info
GET /cmd/getSetting                      # all user-facing settings
GET /cmd/getParaSetting                  # enum/lookup tables for settings
POST /cmd/setSetting {"data":{"k":v}}    # mutate a setting
GET /cmd/standby/reset                   # keep-alive (call every ~20s)
GET /cmd/standby/now                     # turn WiFi off
GET /cmd/reboot                          # reboot camera (WiFi drops; no reliable response)
GET /cmd/resetFact                       # factory reset (destructive; WiFi drops)
GET /cmd/format/start                    # format SD card (destructive — wipes all media)
GET /cmd/format/result                   # poll SD format status (see response note below)
GET /list/detail/forward/<from_id>/<n>   # file listing, paginated (forward = direction; backward may exist)
GET /file/<id>/<JPG|MP4>                 # full file download
GET /thumb/<id>/<JPG|MP4>               # thumbnail (MP4 variant unverified)
GET /cmd/delete/<id>/<JPG|MP4>           # delete (see safety note)
POST /media/pic/take                     # trigger a remote photo capture
POST /media/pic/result                   # poll result of last photo capture
POST /media/video/start                  # start remote video recording
POST /media/video/stop                   # stop remote video recording
GET /media/getIrStatus                   # get IR / night-vision status
POST /media/setDayNightMode {"data":{"DayNightMode":<mode>}}  # set day/night mode (values from /cmd/getParaSetting)
```

### File listing JSON fields

```json
{"id": 1, "type": 1, "date": "2026-05-10 13:00:01", "size": 3109844, "uid": "83b0084b"}
```

| Field     | Meaning                                              |
|-----------|------------------------------------------------------|
| `id`      | numeric file ID; **paginate by using the last `id` as the next `from_id`** |
| `type`    | `1` = photo (JPG), `2` = video (MP4)                 |
| `date`    | local time, format `YYYY-MM-DD HH:MM:SS` (sorts lexicographically) |
| `size`    | bytes                                                |
| `uid`     | opaque ID; not needed for download                   |
| `aitags`  | AI tag payload (e.g. `{"tags":[]}`); not needed for download; safely ignored |

## Sync logic

Each file's `date` string is the sync watermark. `YYYY-MM-DD HH:MM:SS` sorts
correctly as a string, so save the newest downloaded file's `date`; on the
next run pull anything whose `date > watermark`.

Pagination: `/list/detail/forward/<from_id>/<page_size>` returns files with
`id > from_id` (i.e. start with `from_id=0` for the first page; use the last
`id` from each page as the next `from_id`).

Keep-alive: hit `/cmd/standby/reset` every ~20s during a sync, otherwise the
camera will idle out and drop the AP mid-download.

### Additional JPEG metadata (proprietary)

In JPG files from available GardePro cameras, the EXIF/MakerNote payload
also carries an ASCII timezone marker such as `tz:Australia/Sydney`.

Additionally, observed on timelapse-mode JPEGs; absent on manually-triggered captures.

A JPEG COM segment (`FF FE`, 1028 bytes total) is appended **after** the
standard EOI (`FF D9`). The payload is 1024 bytes: 64 × 16-byte records,
with the first record all-zero.

Known record fields (little-endian):

| Offset | Type     | Description |
|--------|----------|-------------|
| 0      | `uint8`  | `seq` — frame index, 1-based (0 in the first/zero record) |
| 1      | `uint8`  | `type_flag` — observed values: `0x00`, `0x49`, `0x5a`, `0x90`, `0xee` |
| 2–3    | `uint16` | padding (zero) |
| 4–7    | `uint32` | always `0x400` (= 1024) |
| 8–11   | `uint32` | varies per file — possible per-frame exposure or quality metric |
| 12–15  | `uint32` | varies per file — possible per-frame exposure or quality metric |

Full field semantics are unknown. Pass `--extract-com` to `tools/validate-files.py`
to extract this block to a `<filename>.COM.bin` sidecar for offline analysis.

### `/cmd/format/result` response

Poll until `data.status` (or `data.result`) is one of `"done"`, `"finish"`,
`"finished"`, `1`, or `True`. The field name and value vary by firmware.

## ⚠️ Safety

- `/cmd/delete/<id>/<JPG|MP4>` permanently removes files from the SD card.
  BushDump does not call it by default — downloads are copies, the SD card
  keeps the originals. Only wire up delete if explicitly asked.
- `/cmd/format/start` wipes the entire SD card. Do not call without explicit
  user confirmation.
- `/cmd/resetFact` and `/cmd/reboot` drop the WiFi AP before sending a
  response — wrap in try/except; a connection error is expected and normal.
- Skip `/cmd/standby/now` if the user might want to keep using the AP after
  the sync.

## What varies by model

The references below have all reported the same general protocol, but the
following details are the ones most likely to drift between models or
firmware revisions:

- **BLE wake characteristic UUID** — `6e400004-...` is what our E6PMB uses,
  but the gardepro-fetcher author saw the wake at handle `0x001e` (which is
  a different characteristic on their E9P). If `OK\r\n` doesn't come back,
  fall back to enumerating GATT and trying each writable characteristic.
- **BLE wake payload** — `AT+WAKEPULSE=10\r\n` is reported across multiple
  models, but the `10` may be a duration parameter; some firmwares may want
  a different number or a totally different AT command.
- **WiFi WPA2 password** — `1234567890` is the factory default we've seen
  but is user-settable via the GardePro Mobile app. Always check the
  per-camera config first.
- **HTTP gateway IP** — `192.168.8.1` for current Linkiing firmware. Older
  firmwares may differ; `arp -a` after joining the AP is the fallback.
- **Endpoint paths** — the `/cmd`, `/list`, `/file` shapes appear stable
  across the Linkiing fleet, but specific sub-endpoints (e.g. live stream)
  may be model-specific.
- **`type` enum values** — `1`=photo, `2`=video for our E6PMB; other models
  may add more types (timelapse, audio) with higher values.
- **`/cmd/info/N`** — the N=1..5 split here matches our E6PMB; other models
  may have a different N range or different fields per N.
- **File listing response envelope** — `/list/detail/forward/` wraps the file
  array under `data.list` on some firmware, `data.files` on others, or bare
  `data` (array directly) on others. Check all three before erroring.
- **File timestamp field name** — most firmware uses `date`; some use `time`.
  Both are `YYYY-MM-DD HH:MM:SS` strings.
- **`/cmd/info/2` battery field** — most firmware uses `battery` (0–100 %);
  some use `voltage` (same scale) with a companion `vol_value` (raw mV). Both
  were seen only on external power, so what they each mean at battery-only
  levels is unknown. `parse_info2()` tries `battery` first, then `voltage`.
- **`/cmd/info/4` timezone field** — key may be `tz` or `timezone`; the
  `clock` value format (`YYYY-MM-DD HH:MM:SS`) is unchanged. `clock` is **local
  time**, and the tz is reported in one of two forms across firmware builds: an
  IANA zone name (`"Australia/Sydney"`, seen on E6PMB hardware) or a numeric UTC
  offset in minutes (`600`). `parse_info4()` handles both and converts to UTC.
  `setGmtClock` takes a **UTC** time; the camera re-applies its tz for display,
  so after setting UTC the `clock` field reads back as local time.
- **`/cmd/setGmtClock` vs `/cmd/setGmtClock2`** — both set the camera clock
  with the same payload; they exist as separate endpoints for different firmware
  builds of the same camera line (not different models). Try `setGmtClock` first;
  if the clock doesn't change after the call (verify by re-reading `/cmd/info/4`),
  try `setGmtClock2`. Source: camtrap-control docstrings + CLI `--variant` flag.

## Re-adding legacy OEM (`0xFF00` BLE / `192.168.1.8`) support

BushDump used to target the early GardePro/Dsoon OEM platform — a completely
different stack. If a legacy camera ever needs supporting, here's the
playbook (no code remains; everything below is the recoverable spec):

**BLE wake**
- Service UUID `0000ff00-0000-1000-8000-00805f9b34fb`
- Characteristic UUID `0000ff01-0000-1000-8000-00805f9b34fb`
- Payload: ASCII `BT_Key_On` (hex `42-54-5F-4B-65-79-5F-4F-6E`)
- No reply expected; AP comes up after a short delay.

**WiFi**
- SSID format: `Trail Cam Pro ****`
- WPA2 default: `12345678`

**HTTP API** (gateway `http://192.168.1.8:80`, **must** call
`/SetMode?Storage` first to enable the storage endpoints):

```
GET /SetMode?Storage                    # enter storage mode (required)
GET /Storage?GetDirFileInfo             # count of files
GET /Storage?GetFilePage=<n>&type=Photo # listing page, increments n until empty
GET /Storage?GetFilePage=<n>&type=Video
GET /Storage?GetFileThumb=<fid>         # thumbnail
GET /Storage?Download=<fid>             # full file download
GET /Storage?Delete=<fid>               # delete (safety: opt-in only)
GET /SetMode?PhotoCapture               # live stream mode
GET / on port 8221                      # live stream endpoint
GET /Misc?PowerOff                      # WiFi off
```

File listing JSON fields: `n` (filename), `dt` (unix timestamp — **integer**,
not a date string), `s` (size bytes), `fid` (file ID).

Sync watermark is the integer `dt` of the newest file. Pagination is by
incrementing the `GetFilePage` page index until an empty page comes back.

**What porting would touch:**

- `bushdump/ble.py`: a second wake path (different service+char+payload)
  and a way to choose between them per camera (config flag or BLE-side
  service-discovery probe).
- `bushdump/camera.py`: parallel client class for the `/Storage` API; the
  storage-mode prep step; `dt` (int) vs `date` (string) field handling.
- `bushdump/sync.py`: per-platform watermark type (int vs string).
- `bushdump/config.py`: a `platform = "linkiing" | "legacy"` field per
  camera (or auto-detect by gateway IP after joining the AP).

## References

- https://github.com/vondruska/gardepro-fetcher — Linkiing platform; traffic analysis
  vs. GardePro E9P; source of the `AT+WAKEPULSE=10` claim and the `/cmd` +
  `/list` + `/file` endpoint list.
- https://github.com/fede2cr/camtrap-control — Linkiing platform; independent Python
  client useful for cross-checking endpoint shapes and JSON conventions.
- https://geekitguide.com/wifi-ble-trailcam-investigation-part-2 — legacy OEM;
  source of the `BT_Key_On`/`0xFF01` wake and the `/Storage` endpoints.
- https://github.com/fearthis4/wifi-ble-trailcam-investigations — legacy OEM;
  companion to the geekitguide write-up.
