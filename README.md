# BushDump

A simple CLI to dump photos and videos from a GardePro/Dsoon trail camera to
your laptop over the camera's own WiFi — without using the phone app.

It wakes the camera's WiFi over Bluetooth, joins the access point, and pulls new
files using date-based incremental sync (only what you haven't already grabbed).

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

```bash
uv run bushdump --help
```

## Development

```bash
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

See [CLAUDE.md](CLAUDE.md) for contributor/agent guidance and
[docs/camera-api.md](docs/camera-api.md) for the reverse-engineered camera API.

## License

MIT — see [LICENSE](LICENSE).
