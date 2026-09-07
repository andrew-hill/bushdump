# Trail camera WiFi/BLE API (reverse engineered)

BushDump targets the **Linkiing/Telink-based GardePro** platform (E6PMB,
E8 2.0 Pro, E9P, ...). Cameras advertise a BLE peripheral plus a private WiFi
AP; communication is unencrypted HTTP over the AP (LAN-only, no internet).

See [`camera-models.md`](camera-models.md) for the registry of which models
this doc has been verified against, and [`raw-samples.md`](raw-samples.md) for
the anonymised raw dumps behind these claims.

## Step 1 — Enable WiFi via BLE

Connect to the camera's BLE peripheral and write the AT command to its Nordic
UART **TX-capable** characteristic (note: *not* the standard NUS RX):

- **Service UUID**: `6e400001-b5a3-f393-e0a9-e50e24dcca9e` (Nordic UART)
- **Characteristic UUID**: `6e400004-b5a3-f393-e0a9-e50e24dcca9e` (write, notify)
- **Payload**: ASCII `AT+WAKEPULSE=10\r\n` (hex `41542b57414b4550554c53453d31300d0a`)
- **Expected reply**: `OK\r\n` via notification on the same characteristic

- **SSID format**: `CAM8Z8_<wifi-mac-hex>` (e.g. `CAM8Z8_AABBCC112233`)
- **WPA2 password**: `1234567890` (factory default; user-settable via the
  vendor app — assume the default unless the user told us otherwise)

### AP detection delay — budget ~25s before trusting a "no"

Measured on an E6PMB (2026-08-22, macOS 26): BLE ack at T+0, first positive
detection via `scanForNetworksWithName_` at ~T+25s.

**This is detection latency, not radio boot time — the two can't be separated
with the APIs available.** macOS schedules WiFi scans itself and serves results
from its own cache, so a negative may mean "the AP isn't up" or merely "macOS
hasn't rescanned since it came up". In that run the two negatives (T+6s, T+9s)
returned essentially instantly, while the positive took ~13s inside the call —
consistent with the negatives being cache reads and only the last being a real
scan. So the older "~1–2 seconds after the OK" figure is *not* contradicted by
this data; the AP may well have been up long before we could see it.

**~25s is nonetheless the number to code against.** Whatever the split between
camera and host, that is the realistic macOS-to-camera round trip from wake
command to usable detection, and a client has to tolerate it. A negative scan
result is weak evidence for ~25s after a wake, so don't re-wake or give up on
the strength of one. Poll, and let the join attempt be the real arbiter.

**To measure the true boot delay** you'd need a path that doesn't go through the
scan cache — e.g. attempt `networksetup -setairportnetwork` immediately after the
ack and time how long until it associates. Not yet done.

### Directed-scan throttling — expect unknowns between real answers

Measured off-site at bushdump's own 3s poll interval (macOS 26, 2026-08-24),
scanning for a *configured camera's* SSID while that camera was out of range:

| poll | in-call | result |
|------|---------|--------|
| 1 | ~7s | definite `False` |
| 2–4 | ~0s | `Resource busy` (`NSPOSIXErrorDomain` 16) |
| 5 | ~7s | definite `False` |
| 6–8 | ~0s | `Resource busy` |

**A real scan takes ~7s and answers; the next ~3 polls are throttled and answer
nothing.** Roughly two thirds of polls come back unknown, so a caller that reads
a run of unknowns as a verdict will be wrong most of the time. Poll through them
and keep the last definite answer.

**Do not benchmark this with a made-up SSID.** A name macOS considers
implausible returns instantly from cache without triggering a radio scan, and so
never throttles — 99/99 clean over 300s in that configuration, which looks like
"throttling isn't real" and is purely an artefact of the name. Only a scan for a
name macOS will actually go looking for exercises the rate limiter.

### The ack is one-directional evidence

`OK\r\n` means the camera accepted the wake. **Silence means nothing.** An
E8 2.0 Pro was observed taking three wake attempts with no ack at all while
bringing its AP up regardless — the reply is a 3s notification timeout, not a
failure signal. Never treat a missing ack as "still asleep"; verify by looking
for the AP or by trying the join.

### GATT table

Both modules expose Device Information (`0000180a`) plus the Nordic UART
service. Nothing in the table changes across a wake — all readable
characteristics are static identity fields — so **GATT cannot be polled to
detect whether WiFi came up**.

One field is valuable though: **System ID (`00002a23`) carries the WiFi MAC**
as a standard EUI-48 → EUI-64 expansion, on modules that program it:

```
hex = aabbcc fffe 112233
       └─┬──┘ └┬─┘ └─┬──┘
      MAC[0:3] pad  MAC[3:6]     -> AABBCC112233 -> CAM8Z8_AABBCC112233
```

Strip the `fffe` from the middle and the SSID follows from the format above.
This lets a client derive the SSID over BLE alone, without a WiFi scan — which
matters on hosts that hide network names from scanning APIs.

**Whose MAC is it?** Unresolved. System ID conventionally holds the *BLE*
MAC, so the vendor may be deriving the SSID from the BLE module's address
rather than the WiFi radio's. The observed value matched the SSID exactly,
which is all a client needs — but don't assume it identifies the WiFi
interface.

**Not universal.** Silicon Labs BG22 modules (GATT Hardware Revision
`RF_BM_BG22A1A2`, manufacturer "Shenzhen RF-star Technology") program it;
Telink `LK8625_V1.6` modules ("Shenzhen Linkiing Technology") return
`000000fffe000000`. Check for all-zero before trusting it, and keep a fallback.
Full dumps of both in [`raw-samples.md`](raw-samples.md).

### The wake is an AT command set

`AT+WAKEPULSE=10\r\n` is an AT command, not an opaque blob, which implies a
wider command surface — plausibly including a WiFi-status query worth having.

**It is the camera's command set, not the BLE module's.** RF-star publish an AT
list for the BG22 module (`ATEXIT`, `ATNAME`, `ATMAC`, `ATROLE`, `ATPOWER`,
`ATADS`, `ATADV_DATA`, `ATRSP_DATA`, `ATLE_CODED`, `ATADV_EXT`, `ATBEACON`,
`ATSCAN`, `ATSCAN_PHY`, `ATS_NAME`, `ATSEND`, `ATCONNECT`) — no `WAKEPULSE`,
and the syntax has no `+`. So the module is almost certainly a transparent
BLE-to-UART bridge and our command is consumed by the camera MCU behind it.
Vendor docs therefore won't tell us the camera's commands; only probing will.

**Probe with care**: AT sets commonly include `AT+RST` and `AT+RESTORE`, and
these are cameras you may only be able to reach in person. Query forms (`AT`,
`AT+HELP`, `AT+GMR`, `AT+X?`) only; never the `=` setter form; and deny-list
`RST`/`RESTORE`/`RESET`/`DEFAULT`/`ERASE`/`FORMAT`. Beware also that module-level
commands like `ATEXIT` could drop the bridge out of transparent mode.

#### Module references

**RF-star `RF_BM_BG22A1A2`** (Silicon Labs EFR32BG22) — documentation is good:

- [AT command list](https://www.manualslib.com/manual/2913372/Rf-Star-Efr32bg22.html?page=31)
- [RF-BM-BG22A1(I) hardware datasheet](https://www.rfstariot.com/uploadfile/downloads/RF-BM-BG22A1(I)%20Hardware%20Datasheet%20V1.3_20230526.pdf)
- [Product page](https://www.rfstariot.com/rf-bm-bg22a1-ble5-2-efr32bg22-module_p80.html)

**Linkiing `LK8625_V1.6`** (Telink TLSR8250, BLE 5.0) — **no public AT command
manual found** (searched 2026-08-23). Only the underlying SoC datasheets are
available, which document the chip rather than Linkiing's firmware:

- [TLSR8251 datasheet](https://www.mouser.com/datasheet/2/1039/DS_TLSR8251_E_Datasheet_for_Telink_BLE_2bIEEE802_1-2301453.pdf) (same family)
- [LK8620 FCC filing](https://fcc.report/FCC-ID/2abyn042/6139269.pdf) — sibling module, RF data only

Since the camera's `AT+WAKEPULSE=10` is almost certainly passed through both
modules to the same MCU, the absent Linkiing docs may not matter: whatever
command set we discover on one camera should apply to the other.

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
GET /cmd/delete/<id>/<JPG|MP4>           # delete (see safety note; used by `bushdump prune` behind dry-run + typed token + backup watermark)
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
  Response contract: `{"code": 0, ...}` on success; non-zero `code` or unexpected
  shape is treated as failure. `CameraClient.delete()` raises `RuntimeError` on
  any non-success so a mid-batch failure surfaces immediately.
  BushDump exposes this only via `bushdump prune` (dry-run default, typed
  `DELETE <count>` token, requires backup watermark + local size match + no
  `.error.txt` sidecar). Never call `CameraClient.delete()` from any other path.
  Id-reuse false positives are made vanishingly unlikely by the live
  `date` + `id` + byte-`size` match in `classify_for_prune`.
  **File-count stats after deletion vary by model**: E6PMB updates `/cmd/info/N`
  (file count + SD used) only after a sleep/wake cycle; E8 2.0 Pro reflects
  deletions immediately.
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
