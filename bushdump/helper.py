"""BushDumpWiFiHelper: the small app bundle that reads WiFi network names for us.

macOS 26 and later hide the SSID we are joined to, and every scanned network's
name, from a process without Location permission — and a command-line process
cannot hold that permission, because macOS attributes it to the terminal. An
app bundle can. So the source in `helper/` is built into
`~/Applications/BushDumpWiFiHelper.app`, granted Location once, and asked through
`open`; launching the binary directly would make it the terminal's child again
and get nothing.

Everything degrades to None when the app is missing or unauthorised. Callers
treat that as "can't tell" and fall back, never as an answer.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "BushDumpWiFiHelper"
APP_PATH = Path.home() / "Applications" / f"{APP_NAME}.app"
SOURCE_DIR = Path(__file__).resolve().parent.parent / "helper"

INSTALL_HINT = "`bushdump helper install`"


class HelperError(RuntimeError):
    """Building or installing the helper failed; the message says which step."""


@dataclass(frozen=True, slots=True)
class Reply:
    """One answer from the app. `authorized` False means the names are withheld."""

    authorized: bool
    ssid: str | None = None
    bssid: str | None = None
    networks: tuple[str, ...] = ()


def parse_reply(text: str) -> Reply | None:
    """Parse the JSON the app writes; None for anything that isn't a reply."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("authorized"), bool):
        return None
    return Reply(
        authorized=data["authorized"],
        ssid=data.get("ssid") or None,
        bssid=data.get("bssid") or None,
        networks=tuple(n for n in data.get("networks") or () if isinstance(n, str)),
    )


def installed() -> bool:
    return (APP_PATH / "Contents" / "MacOS" / APP_NAME).exists()


def run(command: str, timeout: float = 30.0) -> Reply | None:
    """Ask the app to `authorize`, read the `current` network, or `scan`.

    None when it isn't installed, didn't answer in time, or answered nonsense.
    """
    if not installed():
        return None
    fd, out = tempfile.mkstemp(prefix="bushdump-wifi-", suffix=".json")
    os.close(fd)
    try:
        subprocess.run(
            ["open", "-W", "-g", "-n", str(APP_PATH), "--args", command, out],
            capture_output=True,
            timeout=timeout,
        )
        return parse_reply(Path(out).read_text())
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(out)


def current() -> Reply | None:
    """The network we are joined to, if the app is installed *and* authorised."""
    reply = run("current", timeout=10.0)
    return reply if reply is not None and reply.authorized else None


def scan() -> list[str] | None:
    """Names of the networks in range; None when the app can't tell us."""
    reply = run("scan", timeout=30.0)
    return list(reply.networks) if reply is not None and reply.authorized else None


# --- building and installing ------------------------------------------------


def parse_signing_identity(stdout: str) -> str | None:
    """First code-signing identity in `security find-identity -v -p codesigning` output."""
    match = re.search(r"^\s*\d+\)\s+([0-9A-F]{40})\s", stdout, re.MULTILINE)
    return match.group(1) if match else None


def signing_identity() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-identity", "-v", "-p", "codesigning"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return parse_signing_identity(result.stdout)


def _run_step(step: str, argv: list[str]) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        raise HelperError(f"{step}: {e}") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise HelperError(f"{step} failed: {detail}")


def install() -> str | None:
    """Build, sign and install the app. Returns the signing identity, None if ad-hoc.

    The identity matters beyond trust: macOS ties the Location grant to the
    app's signature. Signed with a real identity, a rebuild keeps the grant.
    Signed ad-hoc the signature *is* the binary's hash, so every rebuild is a
    new app as far as Location Services is concerned and must be re-authorised.
    """
    source = SOURCE_DIR / "main.swift"
    if not source.exists():
        raise HelperError(
            f"helper source not found at {SOURCE_DIR} — bushdump needs to be installed "
            "from a checkout (`uv tool install --editable`)"
        )
    if shutil.which("swiftc") is None:
        raise HelperError("swiftc not found — install Xcode or its command line tools")

    binary = APP_PATH / "Contents" / "MacOS" / APP_NAME
    binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_DIR / "Info.plist", APP_PATH / "Contents" / "Info.plist")
    _run_step("compile", ["swiftc", "-O", "-o", str(binary), str(source)])
    identity = signing_identity()
    _run_step("sign", ["codesign", "--force", "--sign", identity or "-", str(APP_PATH)])
    return identity
