# BushDump

A simple CLI to dump photos and videos from GardePro/Dsoon trail cameras to your
laptop over each camera's own WiFi — without using the phone app.

It wakes a camera's WiFi over Bluetooth, joins the access point, and pulls new
files using date-based incremental sync (only what you haven't already grabbed).
Register several cameras and `./bd sync` grabs from whichever are nearby.

> **Note:** BushDump copies files; it never deletes from the camera's SD card.

## Requirements

- [uv](https://docs.astral.sh/uv/) for dependency management. On macOS:
  ```bash
  brew install uv
  ```
  (uv fetches the right Python version automatically — no separate Python install needed.)
- A Bluetooth + WiFi capable machine (developed on macOS)

## Install

```bash
uv sync
```

## Usage

`./bd` is a wrapper around the CLI so you don't have to type `uv run` each time.

Register each camera once (guided — pick from live lists, no typing long codes):

```bash
./bd register        # detect a camera, pick its BLE device + WiFi, give it a name
./bd cameras         # show configured cameras
```

Then sync whenever you like:

```bash
./bd sync             # scan and sync every nearby configured camera
./bd sync frontgate   # sync just one
./bd ls frontgate     # preview which files would be downloaded
./bd stats frontgate  # battery, SD usage, file counts
./bd ble              # read-only: live-list nearby BLE devices
./bd wifi [ble-addr]  # live-list WiFi networks (wake a camera first if given)
./bd --help           # all commands and flags
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

## License

MIT — see [LICENSE](LICENSE).
