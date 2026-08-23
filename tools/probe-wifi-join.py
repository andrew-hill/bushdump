"""Test the full wake → detect → join → talk chain on macOS 26, one step at a time.

Three things are unproven on Tahoe and this settles all of them:

  1. Does the BLE ack arrive, and does the AP actually come up?
  2. Does `ssid_present()` detect the AP by name, given macOS won't *report*
     names? (CoreWLAN still filters by one — that's the whole trick.)
  3. Does `networksetup -setairportnetwork` still join? Every previous attempt
     was interrupted before finishing, so this has never been confirmed.

On site, run:

    uv run python tools/probe-wifi-join.py frontgate

It stops at the first hard failure and says which step broke, so a partial run
is still a useful result. Writes a timestamped log to `scratch/`.

It WILL join the camera's AP, dropping your normal WiFi — same as `bushdump
sync`. Nothing is downloaded and nothing on the camera is modified.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = REPO_ROOT / "scratch"


class Tee:
    """Print to the terminal and the log at once — you may be offline on site."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("w", encoding="utf-8")

    def write(self, text: str) -> None:
        sys.__stdout__.write(text)
        self._file.write(text)

    def flush(self) -> None:
        sys.__stdout__.flush()
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def step(n: int, title: str) -> None:
    print(f"\n{'=' * 60}\nSTEP {n}: {title}\n{'=' * 60}")


def probe(cam, wake_attempts: int, poll_seconds: float) -> None:
    from bushdump import ble, wifi
    from bushdump.camera import CameraClient

    print(f"camera : {cam.name}")
    print(f"ssid   : {cam.ssid}")
    print(f"ble    : {cam.ble_address}")
    print(f"host   : {cam.camera_host}")

    step(1, "BLE wake")
    ack = False
    for attempt in range(1, wake_attempts + 1):
        try:
            reply = asyncio.run(ble.wake_wifi(cam.ble_address))
        except Exception as exc:
            print(f"  attempt {attempt}: wake failed: {exc!r}")
            continue
        if reply is None:
            print(f"  attempt {attempt}: no ack (unreliable — may have woken anyway)")
        else:
            print(f"  attempt {attempt}: ack {reply.decode('utf-8', 'replace').strip()!r}")
            ack = True
            break
    print(f"  => ack received: {ack}")

    step(2, "ssid_present() — can we detect the AP by name?")
    print("  (None = rate-limited, NOT absent. Expect roughly half to be None.)")
    deadline = time.monotonic() + poll_seconds
    seen = False
    results: list[bool | None] = []
    while time.monotonic() < deadline:
        present = wifi.ssid_present(cam.ssid)
        results.append(present)
        print(f"  {time.strftime('%H:%M:%S')}  ssid_present -> {present}")
        if present is True:
            seen = True
            break
        time.sleep(3.0)
    print(f"  => detected: {seen}")
    tally = (results.count(True), results.count(False), results.count(None))
    print(f"  => tally: True={tally[0]} False={tally[1]} None={tally[2]}")

    # macOS auto-joins known networks the moment they appear, which would make
    # step 3 look successful while doing nothing. Reaching the camera *before*
    # we issue any join means macOS got there first.
    step(3, "pre-join check — did macOS auto-join behind our back?")
    pre_joined = False
    try:
        with CameraClient(cam.camera_host, timeout=3.0) as client:
            pre_joined = client.is_ready()
    except Exception as exc:
        print(f"  probe failed: {exc!r}")
    print(f"  => camera reachable BEFORE any join command: {pre_joined}")
    if pre_joined:
        print("  !! macOS auto-joined — step 4 proves nothing on this run.")
        print("     Forget the network and rerun to test networksetup properly.")

    step(4, "networksetup join — THE unverified one")
    joined = False
    try:
        t0 = time.monotonic()
        wifi.join(cam.ssid, cam.password, timeout=45.0)
        joined = True
        print(f"  => JOINED in {time.monotonic() - t0:.1f}s")
    except Exception as exc:
        print(f"  => join FAILED: {exc}")

    step(5, "HTTP — are we really talking to the camera?")
    try:
        with CameraClient(cam.camera_host) as client:
            ready = client.wait_until_ready(timeout=25.0)
            print(f"  => camera ready: {ready}")
            if ready:
                print(f"  {client.describe()}")
    except Exception as exc:
        print(f"  => HTTP failed: {exc!r}")

    step(6, "verdict")
    print(f"  BLE ack        : {ack}")
    print(f"  AP detected    : {seen}")
    print(f"  auto-joined    : {pre_joined}  (True = networksetup result is not evidence)")
    print(f"  networksetup   : {'JOINED' if joined else 'FAILED'}")
    print("\n  You are on the camera's AP — rejoin your normal WiFi when done.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name", help="camera name from your config")
    ap.add_argument("--wake-attempts", type=int, default=3)
    ap.add_argument("--poll-seconds", type=float, default=30.0, help="how long to hunt for the AP")
    args = ap.parse_args()

    from bushdump import config

    cam = config.load_config().cameras.get(args.name)
    if cam is None:
        print(f"Unknown camera {args.name!r}", file=sys.stderr)
        return 1

    SCRATCH_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = SCRATCH_DIR / f"probe-wifi-join-{args.name}-{stamp}.log"
    tee = Tee(log_path)
    sys.stdout = tee  # type: ignore[assignment]
    try:
        print(f"# probe-wifi-join  {datetime.now().isoformat(timespec='seconds')}")
        probe(cam, args.wake_attempts, args.poll_seconds)
    finally:
        sys.stdout = sys.__stdout__  # type: ignore[assignment]
        tee.close()
    print(f"\nSaved: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
