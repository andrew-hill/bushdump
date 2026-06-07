# Trail camera WiFi/BLE API (reverse engineered)

GardePro / Dsoon trail cameras share an OEM platform. The camera creates its own
WiFi hotspot; communication is unencrypted HTTP over that local AP.

## Step 1 — Enable WiFi via BLE

Write to the camera's BLE characteristic to bring its WiFi AP up:

- **Service UUID**: `0xFF00`
- **Characteristic UUID**: `0xFF01`
- **Value**: `BT_Key_On` — hex `42-54-5F-4B-65-79-5F-4F-6E`

After the write, the WiFi AP comes up:

- **Default SSID**: `Trail Cam Pro ****`
- **Default password**: `12345678`

## Step 2 — HTTP API

Camera IP is `192.168.1.8` (check with `arp -a` if different).

```
GET /SetMode?Storage                    # enter storage mode first
GET /Storage?GetDirFileInfo             # count of files
GET /Storage?GetFilePage=0&type=Photo   # listing JSON (see fields below)
GET /Storage?GetFilePage=0&type=Video
GET /Storage?GetFileThumb=<fid>         # thumbnail
GET /Storage?Download=<fid>             # full file download
GET /Storage?Delete=<fid>               # delete (see safety note)
GET /SetMode?PhotoCapture               # enable live stream
GET / on port 8221                      # live stream endpoint
GET /Misc?PowerOff                      # turn off WiFi
```

### File listing JSON fields

| Field | Meaning            |
|-------|--------------------|
| `n`   | filename           |
| `dt`  | Unix timestamp     |
| `s`   | size (bytes)       |
| `fid` | file ID            |

## Sync logic

Each file has a `dt` Unix timestamp and a `fid`. Implement date-based
incremental sync by saving the timestamp of the newest downloaded file, then on
the next run only pulling files whose `dt` is greater than that watermark.

Pagination: increment the page number in `GetFilePage` until no more files are
returned.

## ⚠️ Safety

`Delete` permanently removes files from the camera's SD card. BushDump does not
call it by default — downloads are copies, the SD card keeps the originals. Only
wire up `Delete` if explicitly asked.

## References

- geekitguide.com/wifi-ble-trailcam-investigation-part-2
- github.com/fearthis4/wifi-ble-trailcam-investigations
