"""Probe a camera's HTTP API while joined to its WiFi AP.

Default is the Linkiing/Telink GardePro platform (BushDump-supported):
  - gateway `http://192.168.8.1:8080`
  - hits `/cmd/info/1..5`, `/cmd/getSetting`, `/cmd/getParaSetting`,
    `/cmd/standby/reset`, `/list/detail/forward/0/10`

`--legacy` switches to the original GardePro/Dsoon OEM:
  - gateway `http://192.168.1.8:80`
  - calls `/SetMode?Storage` first, then `/Storage?GetDirFileInfo` and
    `/Storage?GetFilePage=0&type=Photo`

Output is plain text — redirect to a file if you need to paste it back from
an offline session: `uv run python tools/probe-http.py > probe.out 2>&1`.

Usage:
  uv run python tools/probe-http.py
  uv run python tools/probe-http.py --legacy
  uv run python tools/probe-http.py --host 192.168.8.1 --port 8080
"""

from __future__ import annotations

import argparse
import sys

import httpx

LINKIING = {
    "host": "192.168.8.1",
    "port": 8080,
    "endpoints": [
        "/cmd/info/1",
        "/cmd/info/2",
        "/cmd/info/3",
        "/cmd/info/4",
        "/cmd/info/5",
        "/cmd/getSetting",
        "/cmd/getParaSetting",
        "/cmd/standby/reset",
        "/list/detail/forward/0/10",
    ],
}

LEGACY = {
    "host": "192.168.1.8",
    "port": 80,
    "endpoints": [
        "/SetMode?Storage",
        "/Storage?GetDirFileInfo",
        "/Storage?GetFilePage=0&type=Photo",
        "/Storage?GetFilePage=0&type=Video",
    ],
}

MAX_BODY = 4000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--legacy", action="store_true", help="use the GardePro/Dsoon OEM gateway")
    parser.add_argument("--host", help="override gateway host (default depends on platform)")
    parser.add_argument("--port", type=int, help="override gateway port")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    platform = LEGACY if args.legacy else LINKIING
    host = args.host or platform["host"]
    port = args.port or platform["port"]
    endpoints = platform["endpoints"]

    base = f"http://{host}:{port}"
    print(f"# Probing {base} ({'legacy' if args.legacy else 'Linkiing'})\n")

    with httpx.Client(timeout=args.timeout) as client:
        for path in endpoints:
            url = base + path
            print(f"## GET {path}")
            try:
                r = client.get(url)
            except Exception as e:
                print(f"  ERROR: {e}\n")
                continue
            print(f"  status: {r.status_code}")
            print(f"  content-type: {r.headers.get('content-type', '')}")
            body = r.text
            if len(body) > MAX_BODY:
                print(f"  body ({len(body)} bytes, truncated to {MAX_BODY}):")
                print(body[:MAX_BODY])
                print("  ... [truncated]")
            else:
                print(f"  body ({len(body)} bytes):")
                print(body)
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
