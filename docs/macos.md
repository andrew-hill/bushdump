# macOS behaviour bushdump depends on

What macOS does with WiFi and Bluetooth that the code has to work around:
what is hidden from a command-line process, what still answers truthfully, and
how scans, joins and saved networks behave. Each entry says which version it
was measured on. The camera's own protocol is in
[`camera-api.md`](camera-api.md); nothing here is about the camera.

Add to this file when a macOS behaviour is measured, and correct an entry in
place when a newer version changes it.

## Who is told a network's name

| | macOS 15 | macOS 26 | macOS 27 |
|---|---|---|---|
| `networksetup -getairportnetwork` | names the network | redacted | says "not associated" while joined |
| SSID and BSSID from CoreWLAN in a CLI process | readable with Location | redacted | redacted |
| Names in a CoreWLAN scan from a CLI process | readable with Location | redacted | redacted |
| `arp -an` | not measured | not measured | empty, even after pinging the router |
| MACs in `ndp` and `ifconfig` | not measured | not measured | every one reads `02:00:00:00:00:00` |

Since macOS 26 a network's name goes only to a process that itself holds
Location permission. A command-line process cannot hold it: macOS attributes
the permission to the terminal, so `authorizedAlways` plus a live location fix
in a Python process still reads null. `sudo` does not get past it,
`system_profiler`, `ipconfig getsummary`, `ioreg` and `scutil` are redacted
too, and turning off "Limit IP Address Tracking" or "Private Wi-Fi Address"
changes nothing. It follows the OS version, not the hardware.

An app bundle with Location permission is told the SSID, the BSSID and the
names in a scan (27.0.1). That is `BushDumpWiFiHelper`. The conditions:

- **It must be launched through `open`.** Run directly, the binary is the
  terminal's child again and gets a null SSID and BSSID. It still reports
  itself as authorised in that case, so the authorisation status alone does
  not prove names are readable.
- **Sign it with a keychain code-signing identity.** Signed that way it kept
  its Location grant across a rebuild and a move to another folder. An ad-hoc
  build lost the grant on rebuild, because an ad-hoc signature is the binary's
  hash.
- **No entitlement is needed.** Apple's restricted
  `com.apple.developer.networking.wifi-info` is not required, and ad-hoc
  signing a binary that claims it gets the process killed by AMFI.

The helper has not been run on macOS 26.

## What a CLI process can still read

On macOS 27, without the helper:

- IP address, router, DHCP server, channel, RSSI and security type.
- The Mac's own WiFi MAC, from CoreWLAN `hardwareAddress()` and
  `networksetup -listallhardwareports`.
- Saved networks, by name, from `networksetup -listpreferredwirelessnetworks`
  (no admin needed; measured on 26).
- The "Could not find network X" error from `networksetup -setairportnetwork`.
- WiFi power state.
- Whether one named network is in range (see "Scanning").
- The `ProfileID` of the network it is joined to (next section).
- Everything Bluetooth.

## `ProfileID`

`scutil` shows a `ProfileID` under `State:/Network/Interface/<iface>/AirPort`:
a token for the saved network the Mac is joined to. It names nothing, so it is
only useful once it has been recorded against a known network.

- **Stable per network.** Identical across four reads and three rejoins of one
  network, including after the Mac's own MAC changed, and different on a
  second network. It is not a plain SHA-256 of the SSID.
- **Networks joined through `networksetup` get one.** Two camera networks
  joined that way with a password each had their own, and the two differed
  (27.0.1).
- **It outlives the association.** After an AP was switched off, `ProfileID`
  still read as that network more than a minute later, while the helper
  reported no SSID within seconds (27.0.1). A `ProfileID` match therefore
  means "this was the last network", not "this network is up", and has to be
  backed by something that proves the link is live.
- **Not measured:** whether it survives forgetting and re-saving the network.

## Scanning

Location Services has always been required for CoreWLAN to scan at all. That
is a separate gate from the name redaction above, and it is the one that
applies on macOS 15.

**CoreWLAN still filters by a name it will not report.**
`scanForNetworksWithName_("G")` returns hits for a real network and none for a
bogus name, on 26 and 27 alike. So "is this AP in range?" can be answered
without reading any name. The answer has three states, present, absent and
unknown, because a scan can come back `Resource busy`. Unknown must never be
read as absent.

**Unknown is the common answer.** Measured at a 3s poll interval, scanning for
a real saved network that was out of range (macOS 26):

| poll | time in call | result |
|------|--------------|--------|
| 1 | ~7s | definite `False` |
| 2–4 | ~0s | `Resource busy` (`NSPOSIXErrorDomain` 16) |
| 5 | ~7s | definite `False` |
| 6–8 | ~0s | `Resource busy` |

A real scan takes about 7s and answers; the next three or so polls are
throttled and answer nothing. Roughly two thirds of polls are unknown, so poll
through them and keep the last definite answer.

**A made-up SSID does not exercise the throttle.** A name macOS considers
implausible is answered from cache at once, with no radio scan: 99 of 99 clean
answers over 300s. Benchmark only with a name macOS will actually go looking
for.

**A negative can be a stale cache read.** macOS schedules scans itself and
serves results from its own cache, so "absent" may mean the AP is down or only
that macOS has not rescanned since it came up. After an AP was switched on, the
first two polls (6s and 9s later) returned "absent" almost instantly and the
first positive came at about 25s, having spent 13s inside the call (macOS 26).
Detection latency and the AP's real boot time cannot be separated through
these APIs. Timing an immediate `networksetup -setairportnetwork` instead
would separate them; that has not been done.

## Joining

- **`networksetup -setairportnetwork` with the password joins** (26, 27).
  Switching straight from one camera's network to another's works (27.0.1).
- **Joining a saved network with no password fails** with `Error: -3900` and
  bounces the association (27).
- **Joining the network the Mac is already on is harmless.** It returns
  success in about 6s (27.0.1), and run in a second terminal it neither
  dropped nor stalled a download in progress over that network (26).
- **macOS auto-joins a saved network as soon as it appears**, usually before
  bushdump's own join is issued (27.0.1). So after a join by SSID has been requested,
  being on the network does not show that the request is what put us there.
- **Auto-join does not switch networks while associated.** With the Mac on one
  network, `airportd` logs `Already associated, and network transition not
  permitted` for the others (27.0.1). A second saved network left running
  therefore does not pull the Mac away from the one it is on.
- **Nothing restores the previous network.** After a join the Mac stays where
  it was put.
- **A wired or tethered internet link can stay up alongside.** With a USB
  tether as the default route, an AP's own subnet is still reached over WiFi
  (27.0.1).

`log show --predicate 'process == "airportd"'` records every association,
disconnect and auto-join decision along with the process that asked for it.
SSIDs in it are redacted; the reasons are not.

## Bluetooth

- **A peripheral is identified by a per-host id, not its MAC.** CoreBluetooth
  assigns each peripheral a UUID that is specific to the Mac, so an address
  saved on one machine matches nothing on another. Whether an in-place OS
  upgrade keeps the ids has not been tested.
- **The first `connect()` to a peripheral occasionally times out**; a second
  attempt succeeds.
- **Nothing in Bluetooth is redacted** (26, 27).
