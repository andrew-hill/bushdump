# AGENTS.md

Guidance for coding agents (Claude Code, Cursor, Codex, etc.) working in this repo.
This is the canonical instructions file; `CLAUDE.md` is a symlink to it (Claude
Code only reads `CLAUDE.md`).

## What this is

BushDump is a small CLI that pulls photos/videos off a GardePro/Dsoon trail
camera onto a local disk over the camera's own WiFi AP — no phone app required.
Flow: wake the camera's WiFi via BLE → join its AP → talk to its HTTP API →
download new files (date-based incremental sync).

It is a personal, single-user tool. Favour simplicity and readability over
generality. No service to deploy, no multi-tenancy, no scale concerns.

## Tech stack

- **Python 3.12+**, managed with **uv** (`uv sync`, `uv run`)
- **bleak** — cross-platform BLE (the WiFi-wake step)
- **httpx** — HTTP client for the camera API
- **pytest** — tests
- **ruff** — lint + format (run before every commit)

## Commands

`./bd` is a thin wrapper for `uv run bushdump` — use it to run the CLI.

```bash
uv sync                              # install deps into .venv (first-time setup)
./bd init                            # write a config template to ~/.config/bushdump
./bd discover                        # live-list nearby BLE devices
./bd wifi                            # live-list WiFi networks
./bd wifi <ble-address>              # wake that camera first, then list WiFi (its AP appears)
./bd add                             # guided: register a camera (pick from live lists)
./bd list                            # show configured cameras
./bd sync                            # scan and sync every nearby configured camera
./bd sync frontgate                  # sync just one camera
./bd sync --manual-wifi              # skip auto WiFi; prompt you to join each AP
uv run pytest                        # run tests
uv run pytest tests/test_sync.py -q  # one file
uv run ruff check .                  # lint
uv run ruff format .                 # format
```

## Project structure

- `bushdump/ble.py` — BLE: `watch()` (live scan), `discover()` (snapshot), `wake_wifi()`; deps imported lazily
- `bushdump/wifi.py` — macOS WiFi: list networks via CoreWLAN (`scan_ssids`/`watch_ssids`, Location-gated), join an AP via `networksetup` (no auto-restore)
- `bushdump/camera.py` — HTTP client for `/Storage` + `/SetMode`; `httpx` imported lazily so pure helpers stay testable without it
- `bushdump/sync.py` — pure logic: `files_to_download`/`next_watermark` (watermark) and `cameras_present` (match scanned addresses to config)
- `bushdump/config.py` — multi-camera config (`[cameras.<name>]`) + per-camera sync state
- `bushdump/cli.py` — subcommands (`init`, `list`, `discover`, `wifi`, `add`, `sync`); orchestrates the flows
- `tests/` — pytest; pure logic only, no real camera/BLE/WiFi needed
- `docs/camera-api.md` — the reverse-engineered camera API reference
- `docs/camera-models.md` — registry of which models have been confirmed against `camera-api.md`
- `tools/` — standalone diagnostic scripts (`inspect-ble`, `wake`, `probe-http`) for adding a new model or stepping through the BLE/WiFi/HTTP flow manually; see `tools/README.md`

## Multi-camera model

Each camera is a `[cameras.<name>]` section (short human name) with its own
`ble_address` + `ssid`; top-level keys are defaults. Each camera downloads to
`<output_dir>/<name>/`, with its own per-type watermark in `state.json`.

## sync flow

`sync` (no name): BLE-scan for presence → `cameras_present` matches configured
cameras by stored address → sync each. `sync <name>` targets one directly. Per
camera: BLE wake → join AP (retries until up) → **poll** camera HTTP until ready
→ storage mode → per media type list+paginate, download files newer than the
saved watermark → save watermark → power off camera WiFi. We do **not**
auto-restore your normal WiFi — the laptop stays on the (last) camera's AP; you
rejoin your usual network yourself. Races are handled by polling, not fixed
sleeps. `--manual-wifi` swaps BLE+auto-join for a "join the AP, press Enter" prompt.

## discover / wifi / add flow

`discover` (read-only) live-lists BLE devices only. `wifi` (read-only)
live-lists WiFi networks; pass a BLE address and it wakes that camera first so
its AP shows up (otherwise the camera AP is off and won't appear). `add` is the
guided setup: live-watch BLE → pick → BLE-wake → live-watch WiFi (re-scan for
the AP-boot delay) → pick SSID → password → join + confirm camera (shows
`describe()`) → name it to save, or bail. Discovery lists *all* nearby
devices/networks to pick from (no fragile filtering); `rank_ssids` only surfaces
likely cameras first. WiFi listing needs Location permission; falls back to
manual SSID entry otherwise.

## Code philosophy

- **Idiomatic Python**: type hints on public functions, f-strings, pathlib,
  dataclasses for structured data, stdlib over deps where reasonable.
- **DRY / small functions / clear names.** One responsibility per module.
- **Campsite rule**: leave code better than you found it.
- **No dead code**: delete it, don't comment it out. Pre-launch, no legacy.
- **Errors are explicit**: the camera is flaky hardware — handle timeouts,
  connection drops, and partial downloads deliberately. Resumable/idempotent
  where it matters.

## Testing strategy

- Lean and behaviour-focused. Test our pure logic (watermark math, response
  parsing, config/state, camera-presence matching, SSID ranking), not libraries.
- **No real camera/BLE/WiFi in tests.** I/O-bound bits (`watch`, `watch_ssids`,
  `describe`, `CameraClient` calls, interactive CLI helpers) are left untested by
  design — keep logic in pure functions so it stays testable.
- Run `uv run pytest` after every change to touched code.

## ⚠️ Safety rules for agents

- **NEVER call the camera's `Delete` endpoint** unless the user explicitly
  asks for that feature in that session. Downloads must never delete originals
  by default.
- **Download is a copy, not a move** — the camera's SD card keeps the files.
- **The camera HTTP API is unencrypted and LAN-only** (its own AP). That's
  expected; don't add TLS/auth theatre. But never expose it beyond the AP.
- **Never commit captured media, the WiFi password, or sync-state files.**
  See `.gitignore`.

## Camera API

The device API (endpoints, BLE wake sequence, sync semantics) is documented in
[`docs/camera-api.md`](docs/camera-api.md). Read it before touching `camera.py`
or `ble.py`.
