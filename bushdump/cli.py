"""Command-line entry point for BushDump."""

from __future__ import annotations

import argparse

from bushdump import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bushdump",
        description="Dump photos/videos off a trail camera over its WiFi AP.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    # TODO: wire up BLE wake -> connect -> sync.
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
