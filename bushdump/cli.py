"""Command-line entry point for BushDump.

Heavy deps (bleak, httpx) are imported lazily inside command handlers so that
`--help`, `init`, and the test suite don't require them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from bushdump import __version__, config, sync

MEDIA_TYPES = ("Photo", "Video")


def cmd_init(args: argparse.Namespace) -> int:
    if config.write_config_template():
        print(f"Wrote config template to {config.CONFIG_PATH}")
        print("Edit it, or run `bushdump add` to register a camera interactively.")
    else:
        print(f"Config already exists at {config.CONFIG_PATH} — leaving it untouched.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    if not cfg.cameras:
        print("No cameras configured. Run `bushdump add` (or edit the config).")
        return 0
    for name, cam in cfg.cameras.items():
        print(name)
        print(f"    ssid:   {cam.ssid or '(unset)'}")
        print(f"    ble:    {cam.ble_address or '(unset)'}")
        print(f"    output: {cam.output_dir}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from bushdump import ble

    cfg = config.load_config()
    if not cfg.cameras:
        print("No cameras configured. Run `bushdump add` first.", file=sys.stderr)
        return 1

    if args.name:
        if args.name not in cfg.cameras:
            print(
                f"Unknown camera {args.name!r}. Configured: {', '.join(cfg.cameras)}",
                file=sys.stderr,
            )
            return 1
        cameras = [cfg.cameras[args.name]]
    else:
        print("Scanning for nearby cameras...")
        present = {addr for addr, _ in asyncio.run(ble.discover(timeout=args.scan_timeout))}
        cameras = sync.cameras_present(cfg.cameras.values(), present)
        if not cameras:
            print(f"None of your cameras are nearby. Configured: {', '.join(cfg.cameras)}")
            return 1
        print(f"Found nearby: {', '.join(c.name for c in cameras)}")

    state = config.load_state()
    total = 0
    for cam in cameras:
        total += _sync_one(cam, state, args)
        config.save_state(state)

    print(f"\nDone — {total} new file(s).")
    print("(Still on the camera's WiFi — rejoin your normal network when you're done.)")
    return 0


def _sync_one(cam: config.Camera, state: dict, args: argparse.Namespace) -> int:
    from bushdump import ble, wifi
    from bushdump.camera import CameraClient

    print(f"\n=== {cam.name} ===")

    if not args.manual_wifi:
        if cam.ble_address:
            print("Waking camera WiFi over BLE...")
            asyncio.run(ble.wake_wifi(cam.ble_address))
        else:
            print("No BLE address configured — skipping wake (turn WiFi on yourself).")

    if args.manual_wifi:
        input(f"Join WiFi '{cam.ssid}' (password: {cam.password}), then press Enter...")
    else:
        print(f"Joining WiFi '{cam.ssid}'...")
        wifi.join(cam.ssid, cam.password)

    downloaded_count = 0
    with CameraClient(cam.camera_host) as client:
        print("Waiting for camera to respond...")
        if not client.wait_until_ready():
            print(f"  {cam.name}: camera did not respond over HTTP — skipping.", file=sys.stderr)
            return 0
        client.enter_storage_mode()

        cam_state = state.setdefault(cam.name, {})
        for media in MEDIA_TYPES:
            watermark = cam_state.get(media)
            available = list(client.iter_files(media))
            todo = sync.files_to_download(available, watermark)
            print(f"{media}: {len(todo)} new of {len(available)}")
            fetched = []
            for f in todo:
                client.download(f, cam.output_dir)
                fetched.append(f)
                downloaded_count += 1
                print(f"  ↓ {f.name}")
            new_watermark = sync.next_watermark(fetched, watermark)
            if new_watermark is not None:
                cam_state[media] = new_watermark

        if not args.keep_awake:
            client.power_off()

    return downloaded_count


def _print_ble_found(address: str, name: str | None) -> None:
    print(f"  • {name or '(unnamed)'}   {address}")


def _print_wifi_found(ssid: str) -> None:
    print(f"  • {ssid}")


def _sorted_devices(devices: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Named devices first, then alphabetical — easier to spot the camera."""
    return sorted(devices, key=lambda d: (d[1] is None, (d[1] or "").lower()))


def cmd_discover(args: argparse.Namespace) -> int:
    from bushdump import ble

    print(f"Watching for BLE devices for {args.timeout:.0f}s...")
    if not asyncio.run(ble.watch(args.timeout, _print_ble_found)):
        print("  (none found)")
    return 0


def cmd_wifi(args: argparse.Namespace) -> int:
    from bushdump import ble, wifi

    if args.ble_address:
        print(f"Waking {args.ble_address} over BLE to bring its WiFi up...")
        try:
            asyncio.run(ble.wake_wifi(args.ble_address))
        except Exception as e:
            print(f"  (BLE wake failed: {e})")

    if not wifi.corewlan_available():
        print("WiFi scan unavailable — Location permission off?", file=sys.stderr)
        return 1
    print(f"Watching for WiFi networks for {args.timeout:.0f}s...")
    if not wifi.watch_ssids(args.timeout, _print_wifi_found):
        print("  (none found)")
    return 0


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


def cmd_add(args: argparse.Namespace) -> int:
    from bushdump import ble, wifi
    from bushdump.camera import CameraClient

    device = _pick_ble_device(args.timeout)
    if device is None:
        print("Cancelled.")
        return 1
    address, adv_name = device

    print(f"\nConnecting to {adv_name or address} over BLE to switch its WiFi on...")
    try:
        asyncio.run(ble.wake_wifi(address))
    except Exception as e:
        print(f"  (BLE wake failed: {e} — the camera's WiFi may already be on)")

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
        client.enter_storage_mode()
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
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="write a config template")
    p_init.set_defaults(func=cmd_init)

    p_list = sub.add_parser("list", help="list configured cameras")
    p_list.set_defaults(func=cmd_list)

    p_discover = sub.add_parser("discover", help="list nearby BLE devices")
    p_discover.add_argument("--timeout", type=float, default=10.0, help="BLE watch seconds")
    p_discover.set_defaults(func=cmd_discover)

    p_wifi = sub.add_parser("wifi", help="list WiFi networks (optionally wake a camera first)")
    p_wifi.add_argument("ble_address", nargs="?", help="BLE address to wake before scanning")
    p_wifi.add_argument("--timeout", type=float, default=8.0, help="WiFi watch seconds")
    p_wifi.set_defaults(func=cmd_wifi)

    p_add = sub.add_parser("add", help="register a camera (guided; pick from live lists)")
    p_add.add_argument("--timeout", type=float, default=10.0, help="BLE watch seconds")
    p_add.add_argument("--wifi-timeout", type=float, default=8.0, help="WiFi watch seconds")
    p_add.set_defaults(func=cmd_add)

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
