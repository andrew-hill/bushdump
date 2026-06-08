"""Command-line entry point for BushDump.

Heavy deps (bleak, httpx) are imported lazily inside command handlers so that
`--help` and the test suite don't require them.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
import time
import traceback
from typing import IO

from bushdump import __version__, config, sync

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


def _wake_join(cam: config.Camera) -> None:
    """BLE-wake then WiFi-join a camera (shared by stats/ls)."""
    from bushdump import wifi

    if cam.ssid and wifi.current_ssid() == cam.ssid:
        print(f"Already on '{cam.ssid}' — skipping wake+join.")
        return
    if cam.ble_address:
        _wake_and_report(cam.ble_address, cam.name)
    else:
        print("No BLE address configured — skipping wake (turn WiFi on yourself).")
    print(f"Joining WiFi '{cam.ssid}'...")
    wifi.join(cam.ssid, cam.password)


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
    return 0


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
        all_files = client.list_all_files()
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
    from bushdump import ble

    _verbose = args.verbose
    log = _open_log(args.log)
    _log_file = log
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
            _out("Scanning for nearby cameras...")
            present = {addr for addr, _ in asyncio.run(ble.discover(timeout=args.scan_timeout))}
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
                _out("\nInterrupted — progress saved.", err=True)
                if all_conflicts:
                    _out_conflicts(all_conflicts)
                return 1
            except Exception:
                _out(traceback.format_exc(), err=True)
                failed = True

        _out(f"\nDone — {total} new file(s).")
        if all_conflicts:
            _out_conflicts(all_conflicts)
        _out("(Still on the camera's WiFi — rejoin your normal network when you're done.)")
        return 1 if failed else 0
    finally:
        if log:
            log.close()
        _log_file = None
        _verbose = False


def _sync_one(cam: config.Camera, state: dict, args: argparse.Namespace) -> tuple[int, list[str]]:
    from bushdump import wifi
    from bushdump.camera import CameraClient

    _out(f"\n=== {cam.name} ===")

    if args.manual_wifi:
        input(f"Join WiFi '{cam.ssid}' (password: {cam.password}), then press Enter...")
    elif cam.ssid and wifi.current_ssid() == cam.ssid:
        _out(f"Already on '{cam.ssid}' — skipping wake+join.")
    else:
        if cam.ble_address:
            _wake_and_report(cam.ble_address, cam.name)
        else:
            _out("No BLE address configured — skipping wake (turn WiFi on yourself).")
        _out(f"Joining WiFi '{cam.ssid}'...")
        wifi.join(cam.ssid, cam.password)

    downloaded_count = 0
    with CameraClient(cam.camera_host) as client:
        _out("Waiting for camera to respond...")
        if not client.wait_until_ready():
            _out(f"  {cam.name}: camera did not respond over HTTP — skipping.", err=True)
            return 0, []
        _out("Camera ready.")

        cam_state = state.setdefault(cam.name, {})
        conflicts: list[str] = []
        last_alive = time.monotonic()
        all_files = client.list_all_files()
        for media in MEDIA_TYPES:
            type_code = _MEDIA_TYPE_CODE[media]
            watermark = cam_state.get(media)
            available = [f for f in all_files if f.type == type_code]
            todo = sync.files_to_download(available, watermark)
            _out(f"{media}: {len(todo)} new of {len(available)}")
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
                saved = client.download(f, cam.output_dir)
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
                    saved_name = saved.name
                    if saved_name != f.name:
                        _out(f"  ! {f.name} conflicts — saved as {saved_name}", err=True)
                        conflicts.append(f"{cam.name}: {f.name} saved as {saved_name}")
                    _out(f"  ↓ {saved_name}  [{', '.join(parts)}]")
                else:
                    _vout(f"  = {f.name}  [{done_count}/{len(todo)}]  (already on disk)")
                # Advance even for already-on-disk files — clean re-runs
                # shouldn't re-check this window.
                cam_state[media] = f.date
            config.save_state(state)

        if not args.keep_awake:
            client.power_off()

    return downloaded_count, conflicts


def _print_ble_found(address: str, name: str | None) -> None:
    print(f"  • {name or '(unnamed)'}   {address}")


def _print_wifi_found(ssid: str) -> None:
    print(f"  • {ssid}")


def _sorted_devices(devices: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Named devices first, then alphabetical — easier to spot the camera."""
    return sorted(devices, key=lambda d: (d[1] is None, (d[1] or "").lower()))


def cmd_ble(args: argparse.Namespace) -> int:
    from bushdump import ble

    print(f"Watching for BLE devices for {args.timeout:.0f}s...")
    if not asyncio.run(ble.watch(args.timeout, _print_ble_found)):
        print("  (none found)")
    return 0


def cmd_wifi(args: argparse.Namespace) -> int:
    from bushdump import wifi

    if args.target:
        if "-" in args.target or ":" in args.target:
            # Literal BLE address — works before registration.
            address, label = args.target, args.target
        else:
            # Camera name — requires config.
            cfg = config.load_config()
            cam = cfg.cameras.get(args.target)
            if cam is None:
                configured = ", ".join(cfg.cameras) or "(none)"
                print(
                    f"Unknown camera {args.target!r}. Configured: {configured}",
                    file=sys.stderr,
                )
                return 1
            if not cam.ble_address:
                print(
                    f"Camera {args.target!r} has no BLE address configured.",
                    file=sys.stderr,
                )
                return 1
            address, label = cam.ble_address, f"{args.target} ({cam.ble_address})"
        _wake_and_report(address, label)

    if not wifi.corewlan_available():
        print("WiFi scan unavailable — Location permission off?", file=sys.stderr)
        return 1
    # When we just woke a camera, give macOS longer to re-scan and surface the
    # new AP — its background scan can take ~15s to spot a network that just
    # came up.
    timeout = args.timeout if args.timeout is not None else (20.0 if args.target else 8.0)
    print(f"Watching for WiFi networks for {timeout:.0f}s...")
    if not wifi.watch_ssids(timeout, _print_wifi_found):
        print("  (none found)")
    return 0


def _wake_and_report(address: str, label: str) -> None:
    """Wake the camera by address, printing the camera's ack on success."""
    from bushdump import ble

    _out(f"Waking {label} over BLE to bring its WiFi up...")
    try:
        reply = asyncio.run(ble.wake_wifi(address))
    except Exception as e:
        _out(f"  (BLE wake failed: {e})")
        return
    if reply is None:
        _out("  (camera didn't ack — the wake may not have taken effect)")
        return
    try:
        text = reply.decode("utf-8").strip()
    except UnicodeDecodeError:
        text = reply.hex()
    _out(f"  camera ack: {text!r}")


def _pick_ble_device(timeout: float) -> tuple[str, str | None] | None:
    from bushdump import ble

    while True:
        print(f"\nWatching for BLE devices for {timeout:.0f}s...")
        devices = _sorted_devices(asyncio.run(ble.watch(timeout, _print_ble_found)))
        if devices:
            print("\nDevices found:")
            for i, (addr, name) in enumerate(devices):
                print(f"  [{i}] {name or '(unnamed)'}   {addr}")
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
                print(f"  [{i}] {ssid}")
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
    print(f"Saved '{name}'. Try it with:  ./bd sync {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bushdump",
        description="Dump photos/videos off trail cameras over their WiFi APs.",
        usage="%(prog)s [--version] <command> ...",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(
        dest="command", title="commands", metavar="  ./bd <command> -h for per-command options"
    )

    p_cameras = sub.add_parser("cameras", help="list configured cameras")
    p_cameras.set_defaults(func=cmd_cameras)

    p_ble = sub.add_parser("ble", help="scan for nearby BLE devices (read-only)")
    p_ble.add_argument("--timeout", type=float, default=10.0, help="BLE watch seconds")
    p_ble.set_defaults(func=cmd_ble)

    p_wifi = sub.add_parser(
        "wifi",
        help="scan for nearby WiFi networks (optionally BLE-wake a camera first)",
    )
    p_wifi.add_argument(
        "target",
        nargs="?",
        help="camera name (from config) or BLE address to wake before scanning",
    )
    p_wifi.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="WiFi watch seconds (default: 20 if waking a camera, 8 otherwise)",
    )
    p_wifi.set_defaults(func=cmd_wifi)

    p_stats = sub.add_parser("stats", help="show battery, SD usage, and file counts for a camera")
    p_stats.add_argument("name", help="camera name (from `bd cameras`)")
    p_stats.set_defaults(func=cmd_stats)

    p_ls = sub.add_parser("ls", help="list files on the camera (* = would be downloaded)")
    p_ls.add_argument("name", help="camera name (from `bd cameras`)")
    p_ls.set_defaults(func=cmd_ls)

    p_keepalive = sub.add_parser("keepalive", help="keep the camera's WiFi alive (Ctrl+C to stop)")
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
        help="register a new camera (guided; pick from live BLE+WiFi lists)",
    )
    p_register.add_argument("--timeout", type=float, default=10.0, help="BLE watch seconds")
    p_register.add_argument("--wifi-timeout", type=float, default=8.0, help="WiFi watch seconds")
    p_register.set_defaults(func=cmd_register)

    p_sync = sub.add_parser("sync", help="download new files from nearby cameras")
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
    p_sync.set_defaults(func=cmd_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
