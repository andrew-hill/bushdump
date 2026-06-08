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
GET /cmd/info/4                          # clock + timezone
GET /cmd/info/5                          # extended HW/FW/BLE/battery info
GET /cmd/getSetting                      # all user-facing settings
GET /cmd/getParaSetting                  # enum/lookup tables for settings
POST /cmd/setSetting {"data":{"k":v}}    # mutate a setting
GET /cmd/standby/reset                   # keep-alive (call every ~20s)
GET /cmd/standby/now                     # turn WiFi off
GET /list/detail/forward/<from_id>/<n>   # file listing, paginated
GET /file/<id>/<JPG|MP4>                 # full file download
GET /thumb/<id>/JPG                      # thumbnail
GET /cmd/delete/<id>/<JPG|MP4>           # delete (see safety note)
```

### File listing JSON fields

```json
{"id": 1, "type": 1, "date": "2026-05-10 13:00:01", "size": 3109844, "uid": "83b0084b"}
```

| Field  | Meaning                                              |
|--------|------------------------------------------------------|
| `id`   | numeric file ID; **paginate by using the last `id` as the next `from_id`** |
| `type` | `1` = photo (JPG), `2` = video (MP4)                 |
| `date` | local time, format `YYYY-MM-DD HH:MM:SS` (sorts lexicographically) |
| `size` | bytes                                                |
| `uid`  | opaque ID; not needed for download                   |

## Sync logic

Each file's `date` string is the sync watermark. `YYYY-MM-DD HH:MM:SS` sorts
correctly as a string, so save the newest downloaded file's `date`; on the
next run pull anything whose `date > watermark`.

Pagination: `/list/detail/forward/<from_id>/<page_size>` returns files with
`id > from_id` (i.e. start with `from_id=0` for the first page; use the last
`id` from each page as the next `from_id`).

Keep-alive: hit `/cmd/standby/reset` every ~20s during a sync, otherwise the
camera will idle out and drop the AP mid-download.

## ⚠️ Safety

- `/cmd/delete/<id>/<JPG|MP4>` permanently removes files from the SD card.
  BushDump does not call it by default — downloads are copies, the SD card
  keeps the originals. Only wire up delete if explicitly asked.
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

- github.com/vondruska/gardepro-fetcher — Linkiing platform; traffic analysis
  vs. GardePro E9P; source of the `AT+WAKEPULSE=10` claim and the `/cmd` +
  `/list` + `/file` endpoint list.
- github.com/fede2cr/camtrap-control — Linkiing platform; independent Python
  client useful for cross-checking endpoint shapes and JSON conventions.
- geekitguide.com/wifi-ble-trailcam-investigation-part-2 — legacy OEM;
  source of the `BT_Key_On`/`0xFF01` wake and the `/Storage` endpoints.
- github.com/fearthis4/wifi-ble-trailcam-investigations — legacy OEM;
  companion to the geekitguide write-up.
