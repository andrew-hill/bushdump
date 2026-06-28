# BushDump

A simple CLI to dump photos and videos from GardePro/Dsoon trail cameras to your
laptop over each camera's own WiFi — without using the phone app.

It wakes a camera's WiFi over Bluetooth, joins the access point, and pulls new
files using date-based incremental sync (only what you haven't already grabbed).
Register several cameras and `bushdump sync` grabs from whichever are nearby.

> **Note:** BushDump copies files; it never deletes from the camera's SD card.

## Why "BushDump"?

Trail cameras are used in the bush — strapped to trees, watching game trails,
sitting out in the weather for weeks. When you finally hike out to check them,
the job is simple: dump whatever's on the card. BushDump is that job, automated.
The name doubles as a mild joke at the expense of every proprietary phone app
that insists on cloud uploads, accounts, and subscriptions for what is
fundamentally a file copy.

## Requirements

- [uv](https://docs.astral.sh/uv/) for dependency management. On macOS:
  ```bash
  brew install uv
  ```
  (uv fetches the right Python version automatically — no separate Python install needed.)
- macOS with Bluetooth + WiFi hardware. BushDump uses macOS's built-in
  CoreWLAN/`networksetup` tooling for WiFi scanning and joining.
- **rsync** (for `backup`): macOS ships rsync 2.6.9, which is missing options
  like `--chown`. If you need those, install a modern version:
  ```bash
  brew install rsync
  ```
  Then set `rsync_bin` in your `[backup]` config to the installed binary
  (typically `/opt/homebrew/bin/rsync` on Apple Silicon; run `which rsync` to confirm).

## Installation

Install as a uv tool so `bushdump` is available on your PATH:

```bash
uv tool install --editable /path/to/bushdump
```

Enable tab-completion in zsh (add to `~/.zshrc`):

```bash
eval "$(bushdump completions zsh)"
```

For bash, add to `~/.bashrc`:

```bash
eval "$(bushdump completions bash)"
```

> **Development:** to run without installing, use `./bd` (a thin `uv run` wrapper in the repo root).

## Usage

Register each camera once (guided — pick from live lists, no typing long codes):

```bash
bushdump register        # detect a camera, pick its BLE device + WiFi, give it a name
bushdump cameras         # show configured cameras
```

Then preview or sync whenever you like:

```bash
bushdump sync             # scan and sync every nearby configured camera
bushdump sync frontgate   # sync just one
bushdump ls frontgate     # preview which files would be downloaded
```

If hardware or WiFi is being awkward, the inspection commands are useful on
their own:

```bash
bushdump stats frontgate  # battery, SD usage, file counts
bushdump ble              # read-only: live-list nearby BLE devices
bushdump wifi             # read-only: live-list WiFi networks
bushdump wake frontgate   # wake the camera's WiFi without syncing
bushdump keepalive frontgate  # keep the camera's WiFi awake until Ctrl+C
bushdump --help           # all commands and flags
```

> **macOS permissions:** the first BLE use prompts for Bluetooth access (approve
> it). Listing WiFi networks needs Location Services — if it's off, `register`
> falls back to typing the SSID manually.

## Development

```bash
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

See [AGENTS.md](AGENTS.md) for contributor/agent guidance (`CLAUDE.md` is a
symlink to it) and [docs/camera-api.md](docs/camera-api.md) for the
reverse-engineered camera API.

## Credits

BushDump's implementation is original, but the camera protocol work builds on
community reverse-engineering notes from:

- [vondruska/gardepro-fetcher](https://github.com/vondruska/gardepro-fetcher)
  for GardePro E9P Linkiing protocol details, including the BLE WiFi wake
  command, default WiFi behavior, keepalive endpoint, and `/cmd` + `/list` +
  `/file` HTTP shape.
- [fede2cr/camtrap-control](https://github.com/fede2cr/camtrap-control) for an
  independent Python implementation of the Linkiing camera API, useful for
  cross-checking endpoint names, settings, clock, and JSON conventions.
- [Geek IT Guide's Dsoon trailcam investigation](https://geekitguide.com/wifi-ble-trailcam-investigation-part-2/)
  and [fearthis4/wifi-ble-trailcam-investigations](https://github.com/fearthis4/wifi-ble-trailcam-investigations)
  for the older Dsoon/GardePro OEM BLE and `/Storage` API notes documented in
  [docs/camera-api.md](docs/camera-api.md).

## Disclaimer

BushDump is a personal project shared so others can learn from it, adapt it, or
debug similar cameras. Trail cameras and their wireless APIs are flaky, firmware
varies, and this code may fail or need changes for your setup. Use it at your
own risk, and do not rely on it as polished or guaranteed software.

## License

MIT — see [LICENSE](LICENSE).
