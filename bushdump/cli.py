"""Command-line entry point for BushDump.

Heavy deps (bleak, httpx) are imported lazily inside command handlers so that
`--help` and the test suite don't require them.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import functools
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from typing import IO, TYPE_CHECKING

import argcomplete

from bushdump import __version__, config, sync

if TYPE_CHECKING:
    from bushdump import health
    from bushdump.camera import CameraClient

MEDIA_TYPES = ("Photo", "Video")
_MEDIA_TYPE_CODE = {"Photo": 1, "Video": 2}

# Set by cmd_sync before calling into _sync_one/_wake_and_report; reset after.
_log_file: IO[str] | None = None
_verbose: bool = False


def _out(msg: str = "", *, err: bool = False) -> None:
    """Print to stdout/stderr and tee to the log file if open."""
    print(msg, file=sys.stderr if err else sys.stdout)
    if _log_file is not None:
        print(msg, file=_log_file)


def _vout(msg: str = "") -> None:
    """Verbose line: always to log file; stdout only if --verbose."""
    if _log_file is not None:
        print(msg, file=_log_file)
    if _verbose:
        print(msg)


def _ansi(text: str, code: str) -> str:
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text


def _warn_line(w: health.Warning) -> None:
    code = "1;33" if w.level == "warn" else "1;31"
    _out(_ansi(f"  ! {w.message}", code), err=True)


def _out_conflicts(conflicts: list[str]) -> None:
    _out("\nWARNING: filename conflict(s) — files were saved under alternate names:", err=True)
    for c in conflicts:
        _out(f"  {c}", err=True)
    _out("Review these in your output directory.", err=True)


def _fmt_eta(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _open_log(spec: str | None) -> IO[str] | None:
    """Open a log file from a --log argument value. None → logging disabled."""
    if spec is None:
        return None
    if spec == "auto":
        log_dir = config.CONFIG_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = log_dir / f"sync-{ts}.log"
    else:
        from pathlib import Path

        path = Path(spec).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w", encoding="utf-8")


def _is_expected_camera_error(exc: Exception) -> bool:
    """True for routine camera/network failures that should not print a traceback."""
    module = type(exc).__module__
    return isinstance(exc, (RuntimeError, FileNotFoundError)) or module.startswith("httpx")


def _handle_expected_camera_errors(
    func: Callable[[argparse.Namespace], int],
) -> Callable[[argparse.Namespace], int]:
    @functools.wraps(func)
    def wrapper(args: argparse.Namespace) -> int:
        try:
            return func(args)
        except Exception as e:
            if not _is_expected_camera_error(e):
                raise
            print(f"Error: {e}", file=sys.stderr)
            return 1

    return wrapper


def _prompt_clock_sync_with_timeout(
    client: CameraClient, drift_secs: float, timeout: float = 5.0
) -> None:
    """Offer to sync the camera clock now; auto-No after `timeout` seconds."""
    import select

    prompt = f"  Set camera clock now? [y/N] (auto-No in {timeout:.0f}s) "
    _out(prompt, err=True)
    try:
        if not sys.stderr.isatty() or not select.select([sys.stdin], [], [], timeout)[0]:
            _out("  (timed out — skipping clock sync)", err=True)
            return
        answer = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        _out("\n  (cancelled — skipping clock sync)", err=True)
        return
    if answer != "y":
        return
    from datetime import UTC

    now_utc = datetime.datetime.now(UTC)
    client.set_clock(now_utc)
    from bushdump.camera import parse_info4

    ti = parse_info4(client.time_info())
    if ti is not None:
        residual = abs((ti.clock_utc - datetime.datetime.now(UTC)).total_seconds())
        _out(f"  Clock set. Residual drift: {residual:.0f}s", err=True)
    else:
        _out("  Clock set.", err=True)


def _run_health_checks(
    client: CameraClient,
    cam: config.Camera,
    *,
    interactive: bool,
) -> None:
    from bushdump import health

    stats = client.stats()
    time_info = client.parsed_time_info()
    warnings = health.evaluate(
        stats,
        time_info,
        expect_ext_power=cam.expect_ext_power,
    )
    for w in warnings:
        _warn_line(w)
    if interactive and time_info is not None:
        clock_warnings = [w for w in warnings if w.code == "clock_drift"]
        if clock_warnings:
            drift = (time_info.clock_utc - datetime.datetime.now(datetime.UTC)).total_seconds()
            _prompt_clock_sync_with_timeout(client, drift)


def cmd_cameras(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    if not cfg.cameras:
        print("No cameras configured. Run `bushdump register` (or edit the config).")
        return 0
    for name, cam in cfg.cameras.items():
        print(name)
        print(f"    ssid:   {cam.ssid or '(unset)'}")
        print(f"    ble:    {cam.ble_address or '(unset)'}")
        print(f"    output: {cam.output_dir}")
    return 0


def _wake_join(cam: config.Camera, attempts: int = 3) -> None:
    """BLE-wake then WiFi-join a camera (shared by stats/ls/clock/sync).

    The camera's BLE wake is flaky: it can silently no-op, leaving the AP off so
    the join never finds the network. We retry wake+join as a unit — re-waking
    between tries is what clears most transient failures, so each join gets a
    shorter timeout and we loop rather than waiting out one long join.
    """
    from bushdump import wifi

    if cam.ssid and wifi.current_ssid() == cam.ssid:
        _out(f"Already on '{cam.ssid}' — skipping wake+join.")
        return

    if not cam.ble_address:
        _out("No BLE address configured — skipping wake (turn WiFi on yourself).")
        _out(f"Joining WiFi '{cam.ssid}'...")
        wifi.join(cam.ssid, cam.password)
        return

    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        _wake_and_report(cam.ble_address, cam.name)
        _out(f"Joining WiFi '{cam.ssid}'...")
        try:
            wifi.join(cam.ssid, cam.password, timeout=15.0)
            return
        except RuntimeError as e:
            last_err = e
            if attempt < attempts:
                _out(f"  (attempt {attempt}/{attempts} couldn't reach the AP — re-waking)")
    assert last_err is not None
    raise last_err


def _resolve_camera(name: str) -> config.Camera | None:
    """Look up a camera by name; print an error and return None if not found."""
    cfg = config.load_config()
    cam = cfg.cameras.get(name)
    if cam is None:
        print(
            f"Unknown camera {name!r}. Configured: {', '.join(cfg.cameras) or '(none)'}",
            file=sys.stderr,
        )
    return cam


@_handle_expected_camera_errors
def cmd_stats(args: argparse.Namespace) -> int:
    from bushdump.camera import CameraClient

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1
    _wake_join(cam)
    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print("Camera did not respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print("Camera ready.")
        s = client.stats()
        sd_pct = round(s.sd_used_kb / s.sd_total_kb * 100) if s.sd_total_kb else 0
        sd_used_gb = s.sd_used_kb / (1024 * 1024)
        sd_total_gb = s.sd_total_kb / (1024 * 1024)
        ext = "  (ext power)" if s.ext_power else ""
        print(f"Battery:     {s.battery}%{ext}")
        print(f"Temperature: {s.temperature}°C")
        print(f"SD card:     {sd_used_gb:.1f} / {sd_total_gb:.1f} GB used ({sd_pct}%)")
        print(f"Files:       {s.photo_count} photos, {s.video_count} videos")
        _run_health_checks(client, cam, interactive=False)
    return 0


@_handle_expected_camera_errors
def cmd_settings(args: argparse.Namespace) -> int:
    from bushdump.camera import CameraClient

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1
    _wake_join(cam)
    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print("Camera did not respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print("Camera ready.")
        try:
            settings = client.get_settings()
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
    for key, value in settings.items():
        print(f"{key}: {value}")
    return 0


@_handle_expected_camera_errors
def cmd_clock(args: argparse.Namespace) -> int:
    import json

    from bushdump.camera import CameraClient

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1
    _wake_join(cam)
    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print("Camera did not respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print("Camera ready.")
        now_utc = datetime.datetime.now(datetime.UTC)
        now_local = datetime.datetime.now()
        print(f"Laptop UTC:   {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Laptop local: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
        ti = client.time_info()
        print("Camera /cmd/info/4:")
        print(json.dumps(ti, indent=2) if ti is not None else "  (no JSON response)")

        if not args.sync:
            return 0

        sync_time = datetime.datetime.now(datetime.UTC)
        try:
            answer = input(
                f"\nSet camera clock to {sync_time.strftime('%Y-%m-%d %H:%M:%S')} UTC? [y/N] "
            )
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0
        if answer.strip().lower() != "y":
            print("Cancelled.")
            return 0

        client.set_clock(sync_time)
        ti = client.time_info()
        print("\nCamera /cmd/info/4 after sync:")
        print(json.dumps(ti, indent=2) if ti is not None else "  (no JSON response)")
    return 0


@_handle_expected_camera_errors
def cmd_ls(args: argparse.Namespace) -> int:
    from bushdump.camera import CameraClient

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1
    _wake_join(cam)
    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print("Camera did not respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print("Camera ready.")
        state = config.load_state()
        cam_state = state.get(cam.name, {})
        print("Listing files...")

        def on_page(n: int) -> None:
            print(f"  ... {n} files")

        all_files = client.list_all_files(on_page=on_page)
        total = 0
        pending = 0
        for media in MEDIA_TYPES:
            type_code = _MEDIA_TYPE_CODE[media]
            watermark = cam_state.get(media)
            available = [f for f in all_files if f.type == type_code]
            to_dl = {f.id for f in sync.files_to_download(available, watermark)}
            for f in available:
                marker = "*" if f.id in to_dl else " "
                size_kb = f.size // 1024
                print(f"  {marker} {f.name}  {f.date}  {size_kb:>8} KB")
                total += 1
                if f.id in to_dl:
                    pending += 1
    print(f"\n{total} files on camera — {pending} would be downloaded (* = new).")
    return 0


@_handle_expected_camera_errors
def cmd_keepalive(args: argparse.Namespace) -> int:
    from bushdump.camera import CameraClient

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1
    _wake_join(cam)
    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print("Camera did not respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print(f"Camera ready — keeping alive every {args.interval:.0f}s. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(args.interval)
                ok = client.keep_alive()
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                print(f"  {ts}  {'ok' if ok else 'FAILED'}")
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    global _log_file, _verbose
    import httpx

    from bushdump import ble

    _verbose = args.verbose
    log = _open_log(args.log)
    _log_file = log
    try:
        caffeine = subprocess.Popen(["caffeinate", "-i"])
    except Exception:
        caffeine = None
    try:
        if log:
            _out(f"Logging to {log.name}")

        cfg = config.load_config()
        if not cfg.cameras:
            _out("No cameras configured. Run `bushdump register` first.", err=True)
            return 1

        if args.name:
            if args.name not in cfg.cameras:
                _out(
                    f"Unknown camera {args.name!r}. Configured: {', '.join(cfg.cameras)}",
                    err=True,
                )
                return 1
            cameras = [cfg.cameras[args.name]]
        elif args.manual_wifi:
            # Skip BLE discovery — manual mode is the escape hatch for when
            # BLE is unavailable or cameras have no stored address.
            cameras = list(cfg.cameras.values())
            _out(f"Manual WiFi mode — will prompt for: {', '.join(c.name for c in cameras)}")
        else:
            from bleak.exc import BleakBluetoothNotAvailableError

            _out("Scanning for nearby cameras...")
            try:
                present = {addr for addr, _ in asyncio.run(ble.discover(timeout=args.scan_timeout))}
            except BleakBluetoothNotAvailableError:
                _out(
                    "Bluetooth unavailable — use --manual-wifi to sync without BLE.",
                    err=True,
                )
                return 1
            cameras = sync.cameras_present(cfg.cameras.values(), present)
            if not cameras:
                _out(f"None of your cameras are nearby. Configured: {', '.join(cfg.cameras)}")
                return 1
            _out(f"Found nearby: {', '.join(c.name for c in cameras)}")

        state = config.load_state()
        total = 0
        all_conflicts: list[str] = []
        failed = False
        for cam in cameras:
            try:
                n, conflicts = _sync_one(cam, state, args)
                total += n
                all_conflicts.extend(conflicts)
            except KeyboardInterrupt:
                _out("\nInterrupted — progress saved up to last completed file.", err=True)
                if all_conflicts:
                    _out_conflicts(all_conflicts)
                return 1
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
                RuntimeError,
            ):
                lines = traceback.format_exc().strip().splitlines()
                _out("\n".join(lines[-4:]), err=True)
                failed = True
            except Exception:
                _out(traceback.format_exc(), err=True)
                failed = True

        _out(f"\nDone — {total} new file(s).")
        if all_conflicts:
            _out_conflicts(all_conflicts)
        _out("(Still on the camera's WiFi — rejoin your normal network when you're done.)")
        return 1 if failed else 0
    finally:
        if caffeine is not None:
            caffeine.terminate()
        if log:
            log.close()
        _log_file = None
        _verbose = False


def _sync_one(cam: config.Camera, state: dict, args: argparse.Namespace) -> tuple[int, list[str]]:
    from bushdump.camera import CameraClient

    _out(f"\n=== {cam.name} ===")

    if args.manual_wifi:
        input(f"Join WiFi '{cam.ssid}' (password: {cam.password}), then press Enter...")
    else:
        _wake_join(cam)

    downloaded_count = 0
    with CameraClient(cam.camera_host) as client:
        _out("Waiting for camera to respond...")
        if not client.wait_until_ready():
            _out(f"  {cam.name}: camera did not respond over HTTP — skipping.", err=True)
            return 0, []
        _out("Camera ready.")
        _run_health_checks(client, cam, interactive=True)

        cam_state = state.setdefault(cam.name, {})
        conflicts: list[str] = []
        last_alive = time.monotonic()
        _out("Listing files...")

        def _on_page(n: int) -> None:
            _out(f"  … {n} files")

        all_files = client.list_all_files(on_page=_on_page)
        for media in MEDIA_TYPES:
            type_code = _MEDIA_TYPE_CODE[media]
            watermark = cam_state.get(media)
            available = [f for f in all_files if f.type == type_code]
            todo = sync.files_to_download(available, watermark)
            if getattr(args, "retry", False):
                todo_ids = {f.id for f in todo}
                extra = [
                    f
                    for f in available
                    if f.id not in todo_ids
                    and (cam.output_dir / f.name).with_name(f.name + ".error.txt").exists()
                ]
                todo = sorted(extra + todo, key=lambda f: f.date)
            truly_new = sum(1 for f in todo if watermark is None or f.date > watermark)
            _out(f"{media}: {truly_new} new of {len(available)}")
            todo_bytes = sum(f.size for f in todo)
            done_bytes = 0
            avg_bytes = 0
            avg_elapsed = 0.0
            for done_count, f in enumerate(todo, 1):
                now = time.monotonic()
                if now - last_alive > 15:
                    ok = client.keep_alive()
                    last_alive = now
                    _vout(f"  [keep-alive → {'ok' if ok else 'failed'}]")
                t0 = time.monotonic()
                is_retry = (
                    getattr(args, "retry", False)
                    and (cam.output_dir / f.name).with_name(f.name + ".error.txt").exists()
                )
                saved = client.download(f, cam.output_dir, retry=is_retry)
                file_elapsed = time.monotonic() - t0
                done_bytes += f.size
                if saved is not None:
                    downloaded_count += 1
                    avg_bytes += f.size
                    avg_elapsed += file_elapsed
                    parts: list[str] = [f"{done_count}/{len(todo)}"]
                    if file_elapsed > 0.01:
                        parts.append(f"{f.size / file_elapsed / 1_000_000:.1f} MB/s")
                    if avg_elapsed > 0.1:
                        avg_rate = avg_bytes / avg_elapsed
                        remaining = todo_bytes - done_bytes
                        eta_str = _fmt_eta(remaining / avg_rate)
                        parts.append(f"ETA {eta_str} ({avg_rate / 1_000_000:.1f} MB/s avg)")
                    if is_retry:
                        parts.append("retry")
                    saved_name = saved.name
                    if saved_name != f.name:
                        _out(f"  ! {f.name} conflicts — saved as {saved_name}", err=True)
                        conflicts.append(f"{cam.name}: {f.name} saved as {saved_name}")
                    _out(f"  ↓ {saved_name}  [{', '.join(parts)}]")
                    if saved.with_name(saved.name + ".error.txt").exists():
                        _out(
                            f"  ! validation failed — see {saved.name}.error.txt",
                            err=True,
                        )
                else:
                    _vout(f"  = {f.name}  [{done_count}/{len(todo)}]  (already on disk)")
                # Advance even for already-on-disk files — clean re-runs
                # shouldn't re-check this window.
                cam_state[media] = f.date
                config.save_state(state)

        if not args.keep_awake:
            client.power_off()

    return downloaded_count, conflicts


_CAMERA_BLE_HINTS = ("cam8z8", "trail cam", "gardepro", "dsoon", "campark")


def _is_camera_ble(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower()
    return any(h in low for h in _CAMERA_BLE_HINTS)


def _mark(is_cam: bool) -> str:
    """Filled diamond for camera candidates; outline diamond otherwise."""
    return "◆" if is_cam else "◇"


def _format_candidate_row(row: str, is_cam: bool, *, tty: bool | None = None) -> str:
    if not is_cam:
        return row
    if tty is None:
        tty = sys.stdout.isatty()
    if not tty:
        return row
    return f"\033[1;33m{row}\033[0m"


def _print_ble_found(address: str, name: str | None) -> None:
    is_cam = _is_camera_ble(name)
    row = f"  {_mark(is_cam)}  {name or '(unnamed)'}   {address}"
    print(_format_candidate_row(row, is_cam))


def _print_wifi_found(ssid: str) -> None:
    from bushdump import wifi

    is_cam = wifi.is_likely_camera_ssid(ssid)
    row = f"  {_mark(is_cam)}  {ssid}"
    print(_format_candidate_row(row, is_cam))


def _sorted_devices(devices: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Camera candidates first, then other named devices, then unnamed."""

    def key(d: tuple[str, str | None]) -> tuple[int, str]:
        addr, name = d
        if _is_camera_ble(name):
            return (0, (name or "").lower())
        if name is not None:
            return (1, name.lower())
        return (2, addr.lower())

    return sorted(devices, key=key)


def cmd_ble(args: argparse.Namespace) -> int:
    from bleak.exc import BleakBluetoothNotAvailableError

    from bushdump import ble

    print(f"Watching for BLE devices for {args.timeout:.0f}s...")
    try:
        if not asyncio.run(ble.watch(args.timeout, _print_ble_found)):
            print("  (none found)")
    except BleakBluetoothNotAvailableError:
        print(
            "Bluetooth unavailable — check macOS Privacy & Security settings.\n"
            "To sync without BLE: manually join the camera's WiFi AP,"
            " then run `bushdump sync --manual-wifi`.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_wifi(args: argparse.Namespace) -> int:
    from bushdump import wifi

    if not wifi.corewlan_available():
        print("WiFi scan unavailable — Location permission off?", file=sys.stderr)
        return 1
    timeout = args.timeout if args.timeout is not None else 8.0
    print(f"Watching for WiFi networks for {timeout:.0f}s...")
    if not wifi.watch_ssids(timeout, _print_wifi_found):
        print("  (none found)")
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    from bushdump import wifi

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1
    if not cam.ble_address:
        print(f"Camera {args.name!r} has no BLE address configured.", file=sys.stderr)
        return 1
    _wake_and_report(cam.ble_address, cam.name)
    if cam.ssid and wifi.corewlan_available():
        print(f"Waiting for AP '{cam.ssid}' to appear...")
        if wifi.wait_for_ssid(cam.ssid, 20.0):
            print(f"AP '{cam.ssid}' is up.")
        else:
            print(f"AP '{cam.ssid}' did not appear within 20s.")
    return 0


def _wake_and_report(address: str, label: str) -> None:
    """Wake the camera by address, printing the camera's ack on success."""
    from bleak.exc import BleakBluetoothNotAvailableError

    from bushdump import ble

    _out(f"Waking {label} over BLE to bring its WiFi up...")
    try:
        reply = asyncio.run(ble.wake_wifi(address))
    except BleakBluetoothNotAvailableError:
        _out("  (Bluetooth unavailable — check macOS Privacy & Security settings.)")
        return
    except Exception as e:
        _out(f"  (BLE wake failed: {e})")
        return
    if reply is None:
        _out("  (no ack from camera — WiFi may still be coming up)")
        return
    try:
        text = reply.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = reply.hex()
    _out(f"  camera ack: {text!r}")


def _pick_ble_device(timeout: float) -> tuple[str, str | None] | None:
    from bleak.exc import BleakBluetoothNotAvailableError

    from bushdump import ble

    while True:
        print(f"\nWatching for BLE devices for {timeout:.0f}s...")
        try:
            devices = _sorted_devices(asyncio.run(ble.watch(timeout, _print_ble_found)))
        except BleakBluetoothNotAvailableError:
            print(
                "Bluetooth unavailable — check macOS Privacy & Security settings.",
                file=sys.stderr,
            )
            return None
        if devices:
            print("\nDevices found:")
            for i, (addr, name) in enumerate(devices):
                is_cam = _is_camera_ble(name)
                sym = (_mark(True) + "  ") if is_cam else "   "
                row = f"  {f'[{i}]':<4}  {sym}{name or '(unnamed)'}   {addr}"
                print(_format_candidate_row(row, is_cam))
        prefix = "Pick a number, " if devices else ""
        choice = input(f"{prefix}[r] to watch again, blank to cancel: ").strip().lower()
        if choice == "":
            return None
        if choice == "r":
            continue
        if choice.isdigit() and devices and 0 <= int(choice) < len(devices):
            return devices[int(choice)]
        print("Didn't understand that.")


def _pick_ssid(timeout: float) -> str | None:
    from bushdump import wifi

    if not wifi.corewlan_available():
        print("\nWiFi scanning unavailable (Location permission off?).")
        return input("Enter the camera's WiFi SSID manually: ").strip() or None

    while True:
        print(f"\nWatching for WiFi networks for {timeout:.0f}s (the AP can take a few seconds)...")
        ssids = wifi.watch_ssids(timeout, _print_wifi_found)
        if ssids:
            print("\nNetworks found:")
            for i, ssid in enumerate(ssids):
                is_cam = wifi.is_likely_camera_ssid(ssid)
                sym = (_mark(True) + "  ") if is_cam else "   "
                row = f"  {f'[{i}]':<4}  {sym}{ssid}"
                print(_format_candidate_row(row, is_cam))
        prefix = "Pick a number, " if ssids else ""
        raw = input(f"{prefix}[r] watch again, [m] enter manually, blank to cancel: ")
        choice = raw.strip().lower()
        if choice == "":
            return None
        if choice == "r":
            continue
        if choice == "m":
            return input("SSID: ").strip() or None
        if choice.isdigit() and ssids and 0 <= int(choice) < len(ssids):
            return ssids[int(choice)]
        print("Didn't understand that.")


def _prompt_name(prompt: str) -> str | None:
    while True:
        name = input(prompt).strip()
        if not name:
            return None
        if config.camera_exists(name):
            print(f"'{name}' already exists — pick another.")
            continue
        return name


def cmd_register(args: argparse.Namespace) -> int:
    from bushdump import wifi
    from bushdump.camera import CameraClient

    if config.write_config_template():
        print(f"Wrote config template to {config.CONFIG_PATH}")

    device = _pick_ble_device(args.timeout)
    if device is None:
        print("Cancelled.")
        return 1
    address, adv_name = device

    _wake_and_report(address, adv_name or address)

    ssid = _pick_ssid(args.wifi_timeout)
    if not ssid:
        print("No WiFi selected. Cancelled.")
        return 1

    entered = input(f"WiFi password [{config.DEFAULT_PASSWORD}]: ").strip()
    password = entered or config.DEFAULT_PASSWORD

    print(f"\nJoining '{ssid}'...")
    try:
        wifi.join(ssid, password)
    except Exception as e:
        print(f"Couldn't join WiFi: {e}", file=sys.stderr)
        return 1

    with CameraClient() as client:
        print("Waiting for the camera to respond...")
        if not client.wait_until_ready():
            print("Camera didn't respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print("\nConnected: " + client.describe())

    name = _prompt_name("\nEnter a short name to save this camera (blank to discard): ")
    if name is None:
        print("Discarded — nothing saved.")
        return 1

    config.add_camera(name, ble_address=address, ssid=ssid, password=password)
    print(f"Saved '{name}'. Try it with:  bushdump sync {name}")
    return 0


def _backup_one(
    cam: config.Camera,
    args: argparse.Namespace,
    cfg: config.AppConfig,
    backups: dict,
    rsync_bin: str,
    src: str,
    dst: str,
) -> dict:
    """Run backup for one camera; print progress. Mutates backups[cam.name] in-place."""
    from bushdump.backup import (
        advance_watermark,
        date_from_name,
        media_names_of_kind,
        parse_rsync_extra,
        parse_rsync_pending,
        parse_rsync_transfer_count,
        rsync_has_summary,
        safe_watermark,
        validate_watermark,
    )
    from bushdump.prune import scan_local_dir

    _FAIL: dict = {
        "ok": False, "media": {}, "warnings": [], "pending_by_type": [], "wm_advanced": [],
    }
    _KIND = {"Photo": "JPG", "Video": "MP4"}

    cam_backups = backups.setdefault(cam.name, {})

    for media, wm in cam_backups.items():
        if not validate_watermark(wm):
            print(
                f"Error: backup watermark for {cam.name}/{media} in "
                f"{config.BACKUPS_PATH} is not a valid timestamp: {wm!r}\n"
                f"Expected format: YYYY-MM-DD HH:MM:SS (zero-padded, e.g. "
                f"2026-06-12 07:40:00)",
                file=sys.stderr,
            )
            return _FAIL

    if not args.verify_only and not args.dry_run:
        print(f"  Transferring → {dst} ...")
        transfer_cmd = [rsync_bin, "-rlt", "--partial", "--stats"] + cfg.backup.args
        if args.verbose:
            transfer_cmd.append("-v")
        transfer_cmd += [src, dst]
        proc = subprocess.Popen(transfer_cmd, stdout=subprocess.PIPE, text=True)
        stats_lines: list[str] = []
        assert proc.stdout
        for line in proc.stdout:
            if args.verbose:
                sys.stdout.write(line)
                sys.stdout.flush()
            stats_lines.append(line)
        proc.wait()
        stats_text = "".join(stats_lines)
        transferred = parse_rsync_transfer_count(stats_text)
        if transferred is not None:
            t_str = f"{transferred} file(s) transferred"
            print(f"  {_ansi(t_str, '32') if transferred > 0 else t_str}.")
        if proc.returncode != 0:
            print(
                f"  rsync transfer exited {proc.returncode} — continuing to verify ...",
                file=sys.stderr,
            )

    verify_cmd = [rsync_bin, "-rltnv", "--delete", "--itemize-changes"]
    if args.checksum:
        verify_cmd.append("-c")
    verify_cmd += [src, dst]
    method = "byte-checked (rsync -c)" if args.checksum else "size+mtime verified"
    print(f"  Verifying ({method}) ...")
    verify_result = subprocess.run(verify_cmd, capture_output=True, text=True)
    if args.verbose and verify_result.stdout.strip():
        print(verify_result.stdout.rstrip())
    if verify_result.returncode != 0:
        print(f"  rsync verify failed (exit {verify_result.returncode}):", file=sys.stderr)
        if verify_result.stderr.strip():
            print(verify_result.stderr.strip(), file=sys.stderr)
        print("  Backup watermark NOT advanced.", file=sys.stderr)
        return _FAIL

    if not rsync_has_summary(verify_result.stdout):
        print(
            "  Error: rsync produced no completion summary — connection may have failed silently. "
            "Backup watermark NOT advanced.",
            file=sys.stderr,
        )
        return _FAIL

    pending_names = parse_rsync_pending(verify_result.stdout)
    extra_names = parse_rsync_extra(verify_result.stdout)
    local_files = scan_local_dir(cam.output_dir)
    local_names_all = set(local_files.keys())

    def _date_range(names: set[str]) -> str:
        dates = sorted(d for n in names if (d := date_from_name(n)) is not None)
        return f"{dates[0][:10]} → {dates[-1][:10]}" if dates else "—"

    warnings: list[str] = []
    wm_advanced: list[str] = []
    pending_by_type: list[str] = []
    media_results: dict[str, dict] = {}

    for media in args.media:
        kind = _KIND[media]
        local_names = media_names_of_kind(local_names_all, kind)
        pending_canon = media_names_of_kind(pending_names, kind)
        sidecar_blocked = media_names_of_kind(
            {n for n, lf in local_files.items() if lf.has_error_sidecar}, kind
        )
        blocked = pending_canon | sidecar_blocked
        computed = safe_watermark(local_names, blocked)
        stored = cam_backups.get(media)
        new_wm, regressed = advance_watermark(computed, stored)
        if regressed:
            warnings.append(
                f"{media}: computed watermark ({computed}) is before stored "
                f"({stored}) — keeping stored"
            )
        if not args.dry_run and new_wm is not None:
            cam_backups[media] = new_wm

        total_local = len(local_names)
        confirmed_names = local_names - pending_canon
        on_server = len(confirmed_names)
        still_pending = len(pending_canon)
        did_advance = new_wm != stored

        if args.dry_run:
            if did_advance:
                wm_tag = f"{stored or '(none)'} → {new_wm}  [would advance, dry-run]"
            elif stored is not None:
                wm_tag = f"{new_wm}  [no change]"
            else:
                wm_tag = "(none)"
        elif did_advance:
            wm_tag = f"{stored or '(none)'} → {new_wm}  [advanced]"
            wm_advanced.append(f"{media}: {stored or '(none)'} → {new_wm}")
        elif stored is not None:
            wm_tag = f"{new_wm}  [no change]"
        else:
            wm_tag = "(none)"

        pending_col = _ansi(str(still_pending), "1;33") if still_pending > 0 else _ansi("0", "2")
        print(f"  {media}: {total_local} local, {on_server} on server, {pending_col} pending")

        if did_advance and not args.dry_run:
            wm_colored = _ansi(wm_tag, "32")
        elif "[no change]" in wm_tag or wm_tag == "(none)":
            wm_colored = _ansi(wm_tag, "2")
        else:
            wm_colored = wm_tag
        print(f"    watermark:  {wm_colored}")

        if on_server:
            print(f"    confirmed:  {on_server:>5} files  {_date_range(confirmed_names)}")
        if still_pending:
            label = "to transfer" if args.dry_run else "pending"
            print(f"    {label}:    {still_pending:>5} files  {_date_range(pending_canon)}")
        if args.dry_run and pending_canon:
            for name in sorted(pending_canon):
                print(f"      + {name}")

        if still_pending:
            pending_by_type.append(f"{still_pending} {media}")

        behind_wm = {
            n for n in pending_canon
            if new_wm and (d := date_from_name(n)) and d <= new_wm
        }
        if behind_wm:
            warnings.append(
                f"{media}: {len(behind_wm)} pending file(s) are not in sync on server"
                f" — investigate the files listed above"
            )
        sidecar_behind_wm = {
            n for n in sidecar_blocked
            if new_wm and (d := date_from_name(n)) and d <= new_wm
        }
        if sidecar_behind_wm:
            warnings.append(
                f"{media}: {len(sidecar_behind_wm)} file(s) have .error.txt sidecars"
                f" at or behind the watermark — watermark has advanced past a broken file"
            )
        server_extra = media_names_of_kind(extra_names, kind)
        if server_extra:
            warnings.append(
                f"{media}: {len(server_extra)} file(s) on server not present locally"
            )

        media_results[media] = {
            "local": total_local,
            "backed": on_server,
            "pending": still_pending,
            "wm_advanced": did_advance and not args.dry_run,
            "wm_is_none": new_wm is None,
            "regressed": regressed,
            "behind_wm_count": len(behind_wm),
            "sidecar_blocked_count": len(sidecar_blocked),
            "sidecar_behind_wm_count": len(sidecar_behind_wm),
            "server_extra_count": len(server_extra),
        }

    all_canonical = (
        media_names_of_kind(pending_names, "JPG") | media_names_of_kind(pending_names, "MP4")
    )
    if pending_names - all_canonical:
        warnings.append(
            f"{len(pending_names - all_canonical)} non-media file(s) differ from server"
        )
    all_canonical_extra = (
        media_names_of_kind(extra_names, "JPG") | media_names_of_kind(extra_names, "MP4")
    )
    extra_noncanon = extra_names - all_canonical_extra
    if extra_noncanon:
        warnings.append(
            f"{len(extra_noncanon)} non-media file(s) on server not present locally"
        )

    if warnings:
        print(f"\n  {len(warnings)} warning(s):")
        for w in warnings:
            print(_ansi(f"    [!] {w}", "1;33"))

    summary_parts = []
    if wm_advanced:
        summary_parts.append("watermark advanced: " + ", ".join(wm_advanced))
    else:
        summary_parts.append("watermark unchanged")
    if pending_by_type:
        summary_parts.append(_ansi("pending: " + ", ".join(pending_by_type), "1;33"))
    else:
        summary_parts.append(_ansi("nothing pending", "32"))
    print("\n  Result: " + "  ·  ".join(summary_parts))

    if args.dry_run:
        print("  (dry-run — nothing transferred, watermark not saved)")

    return {
        "ok": True,
        "media": media_results,
        "warnings": warnings,
        "pending_by_type": pending_by_type,
        "wm_advanced": wm_advanced,
    }


@_handle_expected_camera_errors
def cmd_backup(args: argparse.Namespace) -> int:
    """Rsync local output → NAS, verify, advance backup watermark."""
    import shutil

    cfg = config.load_config()
    if not cfg.cameras:
        print("No cameras configured. Run `bushdump register` first.", file=sys.stderr)
        return 1

    if args.name:
        if args.name not in cfg.cameras:
            print(
                f"Unknown camera {args.name!r}. Configured: {', '.join(cfg.cameras) or '(none)'}",
                file=sys.stderr,
            )
            return 1
        cameras = [cfg.cameras[args.name]]
    else:
        cameras = list(cfg.cameras.values())

    base = (getattr(args, "to", None) or "").strip() or cfg.backup.target or ""
    if not base:
        print(
            "Error: no rsync target configured. Add a [backup] section to config or pass --to.",
            file=sys.stderr,
        )
        return 1

    rsync_bin = cfg.backup.rsync_bin
    if shutil.which(rsync_bin) is None:
        print(f"Error: rsync binary {rsync_bin!r} not found on PATH.", file=sys.stderr)
        return 1

    backups = config.load_backups()
    all_ok = True
    all_results: list[tuple[config.Camera, dict]] = []

    _DIV_W = 56
    for cam in cameras:
        header = f"── {cam.name} "
        header += "─" * max(0, _DIV_W - len(header))
        print(_ansi(header, "1"))

        src = str(cam.output_dir).rstrip("/") + "/"
        dst = base.rstrip("/") + "/" + cam.name + "/"

        result = _backup_one(cam, args, cfg, backups, rsync_bin, src, dst)
        all_results.append((cam, result))

        if result["ok"] and not args.dry_run:
            config.save_backups(backups)
        if not result["ok"]:
            all_ok = False

    if len(cameras) > 1:
        _print_backup_summary(all_results, args)

    return 0 if all_ok else 1


def _print_backup_summary(
    all_results: list[tuple[config.Camera, dict]],
    args: argparse.Namespace,
) -> None:
    """Print the multi-camera summary table."""
    DIV_W = 72
    media_types = list(args.media)
    name_w = max(len(cam.name) for cam, _ in all_results)

    # Measure column widths from actual data before printing anything.
    backed_w = max(
        len("backed"),
        max(
            (
                len(f"{mr.get('backed', 0)}/{mr.get('local', 0)}")
                for _, r in all_results
                for m in media_types
                for mr in [r["media"].get(m, {})]
            ),
            default=0,
        ),
    )
    pending_w = max(
        len("pending"),
        max(
            (
                len(str(r["media"].get(m, {}).get("pending", 0)))
                for _, r in all_results
                for m in media_types
            ),
            default=0,
        ),
    )
    # One media block: backed + 2sp + pending + space + flag(1). 2-space gap between blocks.
    block_w = backed_w + 2 + pending_w + 2

    header = "══ Summary "
    header += "═" * max(0, DIV_W - len(header))
    print(f"\n{_ansi(header, '1')}")

    prefix = " " * (name_w + 2)

    # Row 1: media type names centred over each block.
    row1 = prefix
    for i, m in enumerate(media_types):
        sep = "  " if i < len(media_types) - 1 else ""
        row1 += m.center(block_w) + sep
    print(row1.rstrip())

    # Row 2: column labels; ≡ marks the flag position after each pending column.
    row2 = prefix
    for i, _ in enumerate(media_types):
        sep = "  " if i < len(media_types) - 1 else ""
        row2 += "backed".rjust(backed_w) + "  " + "pending".rjust(pending_w) + " ≡" + sep
    row2 += "   Result"
    print(row2)

    def _media_flag(mr: dict) -> tuple[str, str]:
        """Return (symbol, ansi_code) status flag for one media column."""
        # Priority: red > yellow-! > yellow-~ > green > blank.
        if mr.get("local", 0) == 0 and mr.get("server_extra_count", 0) == 0:
            return " ", ""
        if (mr.get("behind_wm_count", 0) > 0
                or mr.get("sidecar_behind_wm_count", 0) > 0
                or mr.get("regressed", False)):
            return "✗", "1;31"
        # Files exist on server but not locally — down to one copy.
        if mr.get("server_extra_count", 0) > 0:
            return "!", "1;33"
        # Pending/blocked but watermark is honest.
        if (mr.get("pending", 0) > 0
                or mr.get("sidecar_blocked_count", 0) > 0
                or (mr.get("local", 0) > 0 and mr.get("wm_is_none", False))):
            return "~", "1;33"
        return "✓", "32"

    # Data rows — backed  pending flag; Result column at right edge.
    flags_used: set[str] = set()
    for cam, result in all_results:
        line = cam.name.ljust(name_w + 2)
        for i, media in enumerate(media_types):
            sep = "  " if i < len(media_types) - 1 else ""
            if not result["ok"]:
                line += "error".ljust(block_w) + sep
                flags_used.add("✗")
                continue
            mr = result["media"].get(media, {})
            backed = mr.get("backed", 0)
            total = mr.get("local", 0)
            pending = mr.get("pending", 0)
            backed_raw = f"{backed}/{total}"
            backed_plain = backed_raw.rjust(backed_w)
            backed_str = (
                " " * (backed_w - len(backed_raw)) + _ansi(backed_raw, "2")
                if backed == 0 and total == 0
                else backed_plain
            )
            flag_char, flag_code = _media_flag(mr)
            if flag_char not in (" ", "✓"):
                flags_used.add(flag_char)
            marker = _ansi(flag_char, flag_code) if flag_code else flag_char
            # Pad with plain spaces first so ANSI codes don't disturb width.
            p_num = str(pending)
            p_pad = " " * (pending_w - len(p_num))
            p_colored = p_pad + (_ansi(p_num, "1;33") if pending > 0 else _ansi(p_num, "2"))
            line += backed_str + "  " + p_colored + " " + marker + sep

        # Result column.
        line += "   "
        if not result["ok"]:
            line += _ansi("failed", "1;31")
        else:
            pending_by_type = result.get("pending_by_type", [])
            if pending_by_type:
                line += _ansi(", ".join(pending_by_type) + " pending", "1;33")
            else:
                line += _ansi("nothing pending", "32")
        print(line)

    # Legend: only show symbols that actually appeared in the table.
    _LEGEND = [
        ("~", "1;33", "files not yet on server (watermark held back)"),
        ("!", "1;33", "file on server not present locally — down to one copy"),
        ("✗", "1;31", "watermark inconsistency — do not prune"),
    ]
    if flags_used:
        print()
        for flag, code, meaning in _LEGEND:
            if flag in flags_used:
                print(f"  {_ansi(flag, code)}  {meaning}")


@_handle_expected_camera_errors
def cmd_prune(args: argparse.Namespace) -> int:
    """List or delete old backed-up files from the camera SD card."""
    from bushdump.camera import CameraClient
    from bushdump.prune import PruneVerdict, classify_for_prune, parse_cutoff, scan_local_dir

    cam = _resolve_camera(args.name)
    if cam is None:
        return 1

    try:
        cutoff = parse_cutoff(args.before)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _wake_join(cam)

    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print("Camera did not respond over HTTP — wrong network?", file=sys.stderr)
            return 1
        print("Camera ready.")
        print("Listing files...")
        all_files = client.list_all_files()

        local = scan_local_dir(cam.output_dir)
        cam_backups = config.load_backups().get(args.name, {})

        all_verdicts: list[PruneVerdict] = []
        total_deletable = 0
        total_skipped = 0
        total_bytes = 0

        for media in args.media:
            type_code = _MEDIA_TYPE_CODE[media]
            files = [f for f in all_files if f.type == type_code]
            backup_watermark = cam_backups.get(media)
            verdicts = classify_for_prune(
                files,
                local=local,
                backup_watermark=backup_watermark,
                cutoff_date=cutoff,
            )
            all_verdicts.extend(verdicts)
            for v in verdicts:
                if v.file.date >= cutoff:
                    continue
                size_kb = v.file.size // 1024
                if v.deletable:
                    print(f"  DELETE  {v.file.name}  {v.file.date}  {size_kb:>8} KB")
                    total_deletable += 1
                    total_bytes += v.file.size
                else:
                    print(f"  SKIP: {v.reason:<40}  {v.file.name}")
                    total_skipped += 1

        size_mb = total_bytes / 1_000_000
        print(f"\n{total_deletable} deletable, {total_skipped} skipped, {size_mb:.1f} MB")

        if not args.confirm:
            print("Dry-run — nothing deleted. Pass --confirm to delete.")
            return 0

        if not sys.stdin.isatty():
            print("Error: --confirm requires an interactive terminal.", file=sys.stderr)
            return 1

        if total_deletable == 0:
            print("Nothing to delete.")
            return 0

        token = f"DELETE {total_deletable}"
        print(
            f"\nPermanently delete {total_deletable} files ({size_mb:.1f} MB) from "
            f"{args.name}'s SD card — cannot be undone."
        )
        try:
            answer = input(f"Type  {token}  to proceed: ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 0
        if answer.strip() != token:
            print("Token mismatch — cancelled.")
            return 0

        deleted = 0
        delete_failed = False
        for v in all_verdicts:
            if not v.deletable:
                continue
            try:
                client.delete(v.file)
                print(f"deleted {v.file.name}")
                deleted += 1
            except Exception as e:
                print(f"Error deleting {v.file.name}: {e}", file=sys.stderr)
                print(f"Stopped after {deleted}/{total_deletable} deletions.", file=sys.stderr)
                delete_failed = True
                break

        if delete_failed:
            return 1

    return 0


def cmd_completions(args: argparse.Namespace) -> int:
    print(argcomplete.shellcode([args._prog], shell=args.shell), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bushdump",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
  Setup:
    cameras, cams     list configured cameras
    register, reg     register a new camera (guided)

  Sync:
    ls                list files on the camera (* = would be downloaded)
    sync, s           download new files from nearby cameras

  Inspect and Troubleshoot:
    ble               scan for nearby BLE devices
    wifi              scan for nearby WiFi networks
    wake, w           wake a camera's WiFi over BLE
    stats, st         show battery, SD usage, and file counts
    settings          show current camera settings (read-only)
    clock             show raw camera clock response; optionally sync to UTC
    keepalive, ka     keep the camera's WiFi alive (Ctrl+C to stop)

  Maintenance (SD card):
    backup            rsync local output to NAS; advance backup watermark
    prune             delete old backed-up files from the camera SD card
""",
        usage="%(prog)s [--version] <command> ...",
        add_help=False,
    )
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="show this message and exit; use after a command for command-specific help and usage",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", help=argparse.SUPPRESS)

    p_cameras = sub.add_parser("cameras", aliases=["cams"], help="list configured cameras")
    p_cameras.set_defaults(func=cmd_cameras)

    p_ble = sub.add_parser("ble", help="scan for nearby BLE devices (read-only)")
    p_ble.add_argument("--timeout", type=float, default=10.0, help="BLE watch seconds")
    p_ble.set_defaults(func=cmd_ble)

    p_wifi = sub.add_parser("wifi", help="scan for nearby WiFi networks (read-only)")
    p_wifi.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="WiFi watch seconds (default: 8)",
    )
    p_wifi.set_defaults(func=cmd_wifi)

    p_wake = sub.add_parser("wake", aliases=["w"], help="wake a camera's WiFi over BLE")
    p_wake.add_argument("name", help="camera name (from `bd cameras`)")
    p_wake.set_defaults(func=cmd_wake)

    p_stats = sub.add_parser(
        "stats",
        aliases=["st"],
        help="show battery, SD usage, and file counts for a camera",
    )
    p_stats.add_argument("name", help="camera name (from `bd cameras`)")
    p_stats.set_defaults(func=cmd_stats)

    p_settings = sub.add_parser(
        "settings",
        help="show current camera settings (read-only)",
    )
    p_settings.add_argument("name", help="camera name (from `bd cameras`)")
    p_settings.set_defaults(func=cmd_settings)

    p_clock = sub.add_parser(
        "clock",
        help="show raw camera clock response; optionally sync to UTC",
    )
    p_clock.add_argument("name", help="camera name (from `bd cameras`)")
    p_clock.add_argument(
        "--sync",
        action="store_true",
        help="set camera clock to current UTC, show before/after response",
    )
    p_clock.set_defaults(func=cmd_clock)

    p_ls = sub.add_parser("ls", help="list files on the camera (* = would be downloaded)")
    p_ls.add_argument("name", help="camera name (from `bd cameras`)")
    p_ls.set_defaults(func=cmd_ls)

    p_keepalive = sub.add_parser(
        "keepalive",
        aliases=["ka"],
        help="keep the camera's WiFi alive (Ctrl+C to stop)",
    )
    p_keepalive.add_argument("name", help="camera name (from `bd cameras`)")
    p_keepalive.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="seconds between pings (default: 10)",
    )
    p_keepalive.set_defaults(func=cmd_keepalive)

    p_register = sub.add_parser(
        "register",
        aliases=["reg"],
        help="register a new camera (guided; pick from live BLE+WiFi lists)",
    )
    p_register.add_argument("--timeout", type=float, default=10.0, help="BLE watch seconds")
    p_register.add_argument("--wifi-timeout", type=float, default=8.0, help="WiFi watch seconds")
    p_register.set_defaults(func=cmd_register)

    p_sync = sub.add_parser("sync", aliases=["s"], help="download new files from nearby cameras")
    p_sync.add_argument("name", nargs="?", help="sync only this camera (default: all nearby)")
    p_sync.add_argument(
        "--manual-wifi",
        action="store_true",
        help="skip BLE+auto-join; prompt you to join each AP yourself",
    )
    p_sync.add_argument(
        "--keep-awake",
        action="store_true",
        help="don't power the camera's WiFi off when done",
    )
    p_sync.add_argument(
        "--scan-timeout",
        type=float,
        default=8.0,
        help="seconds to scan for nearby cameras (default: 8)",
    )
    p_sync.add_argument(
        "--log",
        nargs="?",
        const="auto",
        default=None,
        metavar="FILE",
        help="tee output to a log file (auto-named under ~/.config/bushdump/logs/ if omitted)",
    )
    p_sync.add_argument(
        "--verbose",
        action="store_true",
        help="show extra detail on stdout (keep-alive, etc.); always included in log",
    )
    p_sync.add_argument(
        "--retry",
        action="store_true",
        help="re-download files that previously failed validation (.error.txt sidecars)",
    )
    p_sync.set_defaults(func=cmd_sync)

    p_backup = sub.add_parser(
        "backup",
        help="rsync local output to NAS; advance backup watermark",
    )
    p_backup.add_argument(
        "name", nargs="?", default=None, help="camera name (default: all cameras)"
    )
    p_backup.add_argument(
        "--to",
        dest="to",
        default="",
        metavar="BASE",
        help="rsync base target (overrides config [backup] target); camera name is appended automatically",
    )
    p_backup.add_argument(
        "--checksum",
        action="store_true",
        help="use rsync -c for byte-level verify (slower)",
    )
    p_backup.add_argument(
        "--verbose",
        action="store_true",
        help="show rsync output during transfer and verify",
    )
    p_backup.add_argument(
        "--dry-run",
        action="store_true",
        help="skip transfer; show what would be synced without advancing the watermark",
    )
    p_backup.add_argument(
        "--verify-only", "-v",
        action="store_true",
        help="skip transfer; re-verify and advance watermark",
    )
    p_backup.add_argument(
        "--media",
        nargs="*",
        choices=MEDIA_TYPES,
        default=list(MEDIA_TYPES),
        metavar="TYPE",
        help="media types to process (Photo, Video; default: both)",
    )
    p_backup.set_defaults(func=cmd_backup)

    p_prune = sub.add_parser(
        "prune",
        help="delete old backed-up files from the camera SD card (dry-run by default)",
    )
    p_prune.add_argument("name", help="camera name (from `bd cameras`)")
    p_prune.add_argument(
        "--before",
        required=True,
        metavar="DATE",
        help="delete files with date before this (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
    )
    p_prune.add_argument(
        "--media",
        nargs="*",
        choices=MEDIA_TYPES,
        default=list(MEDIA_TYPES),
        metavar="TYPE",
        help="media types to process (Photo, Video; default: both)",
    )
    p_prune.add_argument(
        "--confirm",
        action="store_true",
        help="actually delete (requires typed DELETE <count> token)",
    )
    p_prune.set_defaults(func=cmd_prune)

    p_completions = sub.add_parser("completions", help="print shell completion script")
    p_completions.add_argument("shell", choices=["zsh", "bash", "fish"])
    p_completions.set_defaults(func=cmd_completions, _prog=parser.prog)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
