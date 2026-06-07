# CLAUDE.md

Guidance for coding agents (Claude Code, Cursor, Codex, etc.) working in this repo.
`AGENTS.md` is a symlink to this file.

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

```bash
uv sync                              # install deps into .venv
uv run bushdump --help               # run the CLI
uv run pytest                        # run tests
uv run pytest tests/test_sync.py -q  # one file
uv run ruff check .                  # lint
uv run ruff format .                 # format
```

## Project structure

- `bushdump/ble.py` — wake the camera's WiFi over BLE (service 0xFF00 / char 0xFF01)
- `bushdump/camera.py` — HTTP client wrapping the camera's `/Storage` + `/SetMode` API
- `bushdump/sync.py` — incremental sync logic (compare `dt` against saved watermark)
- `bushdump/config.py` — load config + persist last-sync state
- `bushdump/cli.py` — argument parsing / entry point
- `tests/` — pytest; mock BLE and HTTP, no real camera needed
- `docs/camera-api.md` — the reverse-engineered camera API reference

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

- Lean and behaviour-focused. Test our logic (sync watermark math, API
  response parsing, filename/path handling), not the libraries.
- **No real camera in tests** — mock `httpx` responses and BLE. Tests must
  pass offline and in CI.
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
