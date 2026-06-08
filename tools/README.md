# Camera diagnostic tools

Small standalone scripts for figuring out whether BushDump can support a new
camera model, and for stepping through the BLE → WiFi → HTTP flow manually
when something stops working.

Read [`../docs/camera-api.md`](../docs/camera-api.md) first for the protocol
this targets, and [`../docs/camera-models.md`](../docs/camera-models.md) for
the registry of confirmed models. Update both when you confirm a new model.

## When to use these

You should reach for these tools when:

- You want to add a new camera model and need to know what platform it's on.
- BushDump's normal flow fails and you want to find out at which step.
- You're contributing a fix and need to verify the camera actually behaves
  the way the docs claim.

If you just want to sync a camera that's already known to work — use the
top-level `./bd` CLI, not these tools.

## The flow

There are three stages, and one tool per stage. Each works for both the
Linkiing/Telink GardePro platform (the supported one) and the legacy
GardePro/Dsoon OEM (no longer supported by BushDump, but the diagnostic
still works).

### 1. `inspect-ble.py` — identify the BLE platform

Dump the camera's GATT services and Device Information strings. Use the
output to figure out whether the camera is Linkiing, legacy, or something
unknown.

```bash
./bd ble                                            # find the BLE address
uv run python tools/inspect-ble.py <ble-address>
```

The script ends with a "Likely platform" hint based on which services are
exposed:

- **Linkiing** → Nordic UART Service (`6e400001-...`) present → BushDump's
  supported path. Move to `wake.py` (no flag).
- **Legacy** → service `0000ff00-...` present → use `wake.py --legacy`.
- **Unknown** → neither — you'll need to capture the official app's BLE
  traffic (Android HCI snoop log + Wireshark) to find the wake protocol
  before BushDump can help.

The Device Info section often pins the manufacturer (e.g.
`Shenzhen Linkiing Technology Co.,Ltd.`) and module model (e.g.
`LK8625_V1.6`), both useful for searching for prior reverse-engineering work.

### 2. `wake.py` — bring the WiFi AP up

Write the wake payload over BLE. Default targets Linkiing; `--legacy` targets
the OEM platform; `--probe-all` writes the chosen payload to every writable
characteristic, useful when you suspect the right char has moved between
firmware revisions.

```bash
uv run python tools/wake.py <ble-address>             # Linkiing wake
uv run python tools/wake.py <ble-address> --legacy    # OEM wake
uv run python tools/wake.py <ble-address> --probe-all # blanket probe
```

For Linkiing cameras you should see `<- 'OK\r\n'` on the same characteristic
the wake was written to. The AP comes up 1–2 seconds later.

Then join the AP from your OS WiFi menu using the WPA2 default for the
platform (Linkiing: `1234567890`, legacy: `12345678`), or whatever password
the camera's app shows.

### 3. `probe-http.py` — confirm the HTTP API

Once joined to the camera AP, hit the documented endpoints to confirm the
HTTP shape matches what BushDump expects. Default targets Linkiing
(`192.168.8.1:8080`); `--legacy` targets the OEM (`192.168.1.8:80`).

```bash
uv run python tools/probe-http.py                    # Linkiing
uv run python tools/probe-http.py --legacy           # OEM
uv run python tools/probe-http.py --host X --port Y  # custom
```

Camera APs have no internet — if you're running this from a session where
you can't be online at the same time, redirect output to a file and review
when you reconnect:

```bash
uv run python tools/probe-http.py > probe.out 2>&1
```

For Linkiing cameras the first endpoint (`/cmd/info/1`) returns the brand,
product code, and firmware. That's your ground-truth for the model.

## Adding support for a new model

The diagnostic flow above plus the `What varies by model` section of
[`../docs/camera-api.md`](../docs/camera-api.md) should cover most cases.
When something checks out:

1. Add an entry to [`../docs/camera-models.md`](../docs/camera-models.md)
   with the confirmation legend (`✅` / `🟡` / `📚`) and any per-model
   deviations you spotted.
2. If a setting in BushDump itself needs to change (e.g. the wake
   characteristic UUID, default password, gateway IP) make the change small
   and surgical — the variance section in `camera-api.md` lists the places
   most likely to drift.

If the camera turns out to be a totally different platform (`inspect-ble.py`
classifies it as unknown), the next step is capturing the manufacturer's
official app talking to the camera — usually an Android HCI snoop log
inspected in Wireshark — and figuring out the wake bytes from there. That's
out of scope for this tool suite.
