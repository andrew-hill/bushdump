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

## Supported macOS versions

**macOS 15 (Sequoia), 26 (Tahoe) and 27 (Golden Gate) are all supported.** Keep
it that way: nothing branches on the OS version, and nothing should — every
WiFi path asks each source in turn and uses the first that answers, so a
machine degrades on its own.

Since macOS 26 a command-line process is not told what any WiFi network is
called — not the one it is joined to, not the ones in range. 27 goes further:
`networksetup -getairportnetwork` claims "not associated", the ARP table is
empty and every MAC reads `02:00:00:00:00:00`. Only an app bundle holding
Location permission gets names, and a CLI cannot hold it (macOS attributes the
permission to the terminal). So:

- **`BushDumpWiFiHelper.app`** (`helper/`, built into `~/Applications` by
  `bushdump helper install`) holds that permission and is asked through `open`.
  It names the current network and lists the ones in range. It is optional:
  everything works without it, only slower.
- **Recognising the camera's network** (`wifi.identify`) is what lets
  `_wake_join` skip a BLE wake and a join it does not need. Sources, best first:
  `networksetup` (truthful on 15 only, so believed only when it names the
  camera), the helper, then a `ProfileID` — a per-saved-network token in the
  dynamic store, which names nothing and so only counts once it is recorded
  as `profile_id` in `config.toml`. That happens automatically whenever the
  helper confirms we are on the camera's network (`_remember_profile`), and by
  hand with `bushdump identify <name>` on a Mac that has no helper.
  With none of them the full wake+join runs, and says how to avoid it.
- **Listing networks** (`bushdump wifi`, `register`'s pick-list) takes names
  from the helper; without it you type the SSID in by hand. `ssid_present` needs
  neither — CoreWLAN filters a scan by a name it won't report — so *presence*
  checks are unaffected.

None of this is a correctness problem, so don't "fix" a missing source by
assuming its answer — an unrecognised network always gets the full wake+join.
Don't bring back the gateway MAC either: `arp` is empty on 27. See the macOS
sections of `TODO.md` for what is and isn't redacted, and `docs/camera-api.md`
for the measurements behind the timeouts.

## Commands

Install once with `uv tool install --editable /path/to/bushdump` — then `bushdump` is on your PATH.
In the repo, `./bd` is a thin `uv run` wrapper you can use without installing.

```bash
uv sync                                    # install deps into .venv (first-time setup)
bushdump ble                               # live-list nearby BLE devices
bushdump wifi                              # live-list WiFi networks
bushdump wake <name>                       # wake a configured camera's WiFi over BLE
bushdump register                          # guided: register a camera (pick from live lists)
bushdump cameras                           # show configured cameras
bushdump stats <name>                      # battery, SD usage, file counts
bushdump ls <name>                         # preview which files would be downloaded
bushdump sync                              # scan and sync every nearby configured camera
bushdump sync frontgate                    # sync just one camera
bushdump sync --manual-wifi                # skip auto WiFi; prompt you to join each AP
bushdump stats <name> --joined             # already on the camera's WiFi: skip wake+join
bushdump helper install                    # build + install the app that reads WiFi names (macOS 26+)
bushdump helper status                     # is it installed and allowed? which network are we on?
bushdump identify <name>                   # record a camera network's ProfileID (fallback without the helper)
bushdump keepalive <name>                  # keep camera WiFi alive (pings every 10s; Ctrl+C to stop)
uv run pytest                              # run tests
uv run pytest tests/test_sync.py -q        # one file
uv run ruff check .                        # lint
uv run ruff format .                       # format
```

## Project structure

- `bushdump/ble.py` — BLE: `watch()` (live scan), `discover()` (snapshot), `wake_wifi()`; deps imported lazily
- `bushdump/wifi.py` — macOS WiFi: `watch_ssids` lists networks via CoreWLAN, `ssid_present`/`wait_for_ssid` check one AP by name (the only thing that works under macOS 26 SSID redaction), `diagnose_scan` explains an unusable scan; join an AP via `networksetup` (no auto-restore); `observe`/`identify` decide whether the network we are on is a given camera's
- `bushdump/helper.py` — builds, installs and queries `BushDumpWiFiHelper.app`; `parse_reply` is the pure part. Source for the app is `helper/` (Swift + Info.plist); the built app is never committed
- `bushdump/camera.py` — HTTP client for the Linkiing platform (`/cmd/info/N`, `/list/detail/forward/`, `/file/`); `httpx` imported lazily so pure helpers stay testable without it
- `bushdump/sync.py` — pure logic: `files_to_download`/`next_watermark` (watermark) and `cameras_present` (match scanned addresses to config)
- `bushdump/backup.py` — pure backup watermark logic: `date_from_name`, `parse_rsync_pending`, `media_names_of_kind`, `safe_watermark`, `advance_watermark`
- `bushdump/prune.py` — prune candidate logic: `classify_for_prune`, `cutoffs_by_media` (per-type cutoff), `scan_local_dir`, `parse_cutoff`; dataclasses `LocalFile`, `PruneVerdict`
- `bushdump/config.py` — multi-camera config (`[cameras.<name>]`) + per-camera sync state (`state.json`) + backup watermarks (`backups.json`)
- `bushdump/cli.py` — subcommands (`cameras`, `ble`, `wifi`, `wake`, `helper`, `identify`, `stats`, `settings`, `clock`, `ls`, `keepalive`, `register`, `sync`, `backup`, `prune`, `completions`); orchestrates the flows
- `tests/` — pytest; pure logic only, no real camera/BLE/WiFi needed
- `docs/camera-api.md` — the reverse-engineered camera API reference
- `docs/camera-models.md` — registry of which models have been confirmed against `camera-api.md`
- `tools/` — standalone diagnostic scripts (`inspect-ble`, `wake`, `probe-http`, `probe-ssid-sources`, `probe-wifi-join`, `validate-files`) for adding a new model or stepping through the BLE/WiFi/HTTP flow manually; see `tools/README.md`
- `scratch/` — gitignored scratch space for logs, terminal output, temp notes, code-review output, etc. Everything inside is ignored. Check here for context notes the user may have left.

## Multi-camera model

Each camera is a `[cameras.<name>]` section (short human name) with its own
`ble_address` + `ssid`; top-level keys are defaults. Each camera downloads to
`<output_dir>/<name>/`, with its own per-type watermark in `state.json`.

`ble_address` is a per-host CoreBluetooth id, not the camera's own, so all of
them go stale at once on a new Mac and `sync` reports no cameras nearby — see
`ble.py` before debugging the wake path.

## sync flow

`sync` (no name): BLE-scan for presence → `cameras_present` matches configured
cameras by stored address → sync each. `sync <name>` targets one directly. Per
camera: BLE wake → join AP (retries until up, or gives up early when BLE saw no
camera *and* no AP) → **poll** camera HTTP until ready
→ per media type list+paginate, download files newer than the
saved watermark → save watermark → power off camera WiFi. We do **not**
auto-restore your normal WiFi — the laptop stays on the (last) camera's AP; you
rejoin your usual network yourself. Races are handled by polling, not fixed
sleeps. `--manual-wifi` swaps BLE+auto-join for a "join the AP, press Enter" prompt.

The wake+join is skipped whenever `wifi.identify` says we are already on that
camera's network — at the start, or part-way through if macOS auto-joins while
we are still waking. Enter cuts the current wait short. `--joined` (any camera
command) skips it on your say-so, refusing only a network known to be something
else; `prune` additionally refuses unless it is positively this camera's.

## ble / wifi / register flow

`ble` (read-only) live-lists BLE devices only. `wifi` (read-only) live-lists
WiFi networks; it takes no camera argument — use `wake <name>` first if you
want a camera's AP up, since otherwise it is off and won't appear. `register` is the
guided setup: it writes the config template if missing, then live-watches BLE →
pick → BLE-wake → live-watches WiFi (re-scan for the AP-boot delay) → pick SSID
→ password → join + confirm camera (shows `describe()`) → name it to save, or
bail. Discovery lists *all* nearby devices/networks to pick from (no fragile
filtering); `rank_ssids` only surfaces likely cameras first. WiFi listing needs
Location permission to scan at all — a separate gate from the name redaction in
"Supported macOS versions" above, and the one that bites on Sequoia.

## Code philosophy

- **Idiomatic Python**: type hints on public functions, f-strings, pathlib,
  dataclasses for structured data, stdlib over deps where reasonable.
- **DRY / small functions / clear names.** One responsibility per module.
- **Campsite rule**: leave code better than you found it.
- **No dead code**: delete it, don't comment it out. Pre-launch, no legacy.
- **No dead TODO items**: remove completed items from `TODO.md` outright — don't tick them off and leave them.
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

## Git workflow

- **Only commit or push when explicitly asked.** Never auto-commit.
- **Housekeeping and small, low-risk feature changes go straight to `main`.**
- **Branch + PR for the bigger stuff**: architectural changes, large features
  that need multiple rounds of testing or on-site (hardware) verification, or
  anything with significant blast radius / risk — so it can go through a more
  formal review process. PRs merge with merge commits.
- When in doubt about which bucket a change falls in, ask.
- **Attribution: this repo is Claude-permissive.** Commits and PR text carry a
  `Co-Authored-By:` trailer for whichever assistant did the work. No
  `Claude-Session:` URL, though — a per-session link is no use to anyone reading
  the history later, and some existing commits carry one only because they
  predate this line. This is the repo declaring its own posture, so nothing
  outside it needs updating to match.

## ⚠️ Safety rules for agents

- **`CameraClient.delete()` is exposed only via `bushdump prune`** (dry-run
  default, typed `DELETE <count>` token, requires backup watermark + local size
  match + no `.error.txt` sidecar). Never call `CameraClient.delete()` from
  any other path, and never add a shortcut that bypasses these guards.
- **Download is a copy, not a move** — the camera's SD card keeps the files.
- **The camera HTTP API is unencrypted and LAN-only** (its own AP). That's
  expected; don't add TLS/auth theatre. But never expose it beyond the AP.
- **Never commit captured media, the WiFi password, or sync-state files.**
  See `.gitignore`.
- **The WiFi password is a factory default** (`1234567890`), not a user secret —
  it's a shared, well-known default for this camera platform. Logging or printing
  it in diagnostic output is acceptable; it has no higher sensitivity than the
  SSID itself.

## Camera API

The device API (endpoints, BLE wake sequence, sync semantics) is documented in
[`docs/camera-api.md`](docs/camera-api.md). Read it before touching `camera.py`
or `ble.py`.

Keep `docs/camera-api.md` and `docs/camera-models.md` up to date as we learn
new things about how the cameras behave — confirmed endpoints, field variations,
timing quirks, power-off behaviour, etc. When updating these docs:

- **Use**: camera model names/numbers (E6PMB, E8 2.0 Pro), firmware versions,
  hardware identifiers, MAC address patterns, and protocol-level strings
  (SSIDs like `CAM8Z8_<mac>`, BLE peripheral names like `CAM8Z8_<loc>_G_E6PMB`).
- **Omit**: user-assigned camera names (`east`, `norw`), local directory paths,
  specific physical locations, and any personal information.
