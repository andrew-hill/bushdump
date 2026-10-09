# TODO

- [ ] Add more graceful error handling to avoid large trace outputs from reasonably
      expected issues.
- [ ] Bring MP4 validation in line with JPEG validation.
- [ ] Improve JPEG pixel-corruption detection: `corrupt-scan-zeros.jpg` and
      `corrupt-scan-flip.jpg` in `tests/fixtures/corrupt-jpegs/` currently pass
      validation because libjpeg's error concealment fills in corrupted MCUs with
      plausible-looking repeated rows. Would need to detect large runs of identical
      MCU rows or similar heuristic to catch partial-download / bit-flip corruption.

## Next-visit camera smoke tests

### BLE "device not found" — is it ever a false negative?

**Answered 2026-08-24 for the case that mattered.** Both cameras advertise
continuously whether asleep or awake with the AP up: 10/10 finds in each state
across both BLE modules, worst case 6.1s against a 20s budget. The dangerous
hypothesis — that a camera goes quiet once its radio is on, making "not found"
mean "already awake" — is refuted, and the `_wake_join` early bail is unblocked.

- [ ] Still untested: the *edge* of BLE range, where a present camera might go
      unseen for a full 20s. Not blocking — gating the bail on a definite
      `ssid_present` False covers it, since an awake-but-BLE-unreachable camera
      still shows its AP. Worth measuring if the bail ever misbehaves on site.

### `bd backup` / `bd prune`

**Backup happy path**
- [ ] Full cycle: `bd sync` → `bd backup` → `bd prune` — confirm each step
      sees the state left by the prior.  *(needs camera in range)*

      `sync` → `backup` half confirmed 2026-08-24: backup picked up the files
      that sync had just written and advanced its watermark to exactly the sync
      watermark on both cameras (`2026-08-23 09:50:01` and
      `2026-08-22 17:30:00`), 0 pending, local counts matching the server.
      The NAS had been 9 days stale at `2026-08-15` before that run.
      Still to do: the `backup` → `prune` half, which needs a camera in range.

- [ ] Delete the stale `.DS_Store` left in one camera's folder on the NAS. It
      is what the "non-media file on server" warning was reporting (identified
      2026-08-24). New ones no longer propagate, but an rsync `--exclude`
      protects the existing copy from `--delete` rather than removing it, so
      the warning repeats until it is deleted by hand.

**Backup flags**
- [ ] Failed transfer (kill rsync mid-flight): watermark does NOT advance.  *(needs large transfer in flight to test)*

**Prune guards**
- [ ] `bd prune <name> --before <date>`: correct DELETE/SKIP plan printed;
      cancelling at the token prompt (Ctrl+C) leaves camera file count unchanged.
- [ ] Wrong count in `DELETE <count>` token: rejected with a retry prompt,
      nothing deleted.
- [ ] No backup watermark covering the files to prune: refused.  *(needs camera in range)*
- [ ] Local size mismatch (local file differs from camera copy): blocks that file.
- [ ] `.error.txt` sidecar present: blocks pruning that file.
- [ ] Known-backed-up old file, correct token typed: file disappears from
      `bd ls`, local copy and `state.json` untouched.

### Dropped downloads (new 2026-09-07, never run against hardware)

East reset at file 3 of 1053 and timed out at file 83 of 1052 on 2026-09-06,
each time abandoning the remaining thousand files. `_stream_to_tmp` now retries
any `httpx.TransportError` four times. Nothing here has met a real camera.

- [ ] Sync east — the camera that actually drops. A recovered retry prints
      nothing, so a clean run *is* the pass: compare against the 0.1 MB/s first
      file and two aborts in `scratch/2026-09-06 bushdump sync log.txt`.
- [ ] If `! <file>: ReadError — stopping here. Re-run to resume from this file.`
      appears, four retries were exhausted — a dead link, not a blip. Confirm
      the re-run resumes at that same file and nothing between it and the
      previous watermark was skipped.
- [ ] A stopped run must report the files it did save. The old code printed
      `Done — 0 new file(s)` after saving 82.

### Wake/join fast path (new 2026-10-09, never run against hardware)

`_wake_join` skips the wake+join when it can tell we are already on the camera's
network, keeps checking through every wait, and lets Enter cut a wait short.
Proven off-site against two home networks only.

With `BushDumpWiFiHelper` installed (`bd helper status` says "working"):

- [ ] The first command against each camera should print `recorded <name>'s
      network ProfileID …` and add a `profile_id` line to its section of
      `config.toml`. No line means a network joined through `networksetup` gets
      no `ProfileID`, and the fallback below cannot work for cameras.
- [ ] Run `bd stats <name>` twice: the second, still on the AP, should say
      `Already on <name>'s network` at once.
- [ ] Join east's AP by hand, then `bd stats norw`: it must **not** report
      "already on" — it should wake and join norw.
- [ ] Start `bd sync <name>` from the home network and let macOS auto-join the
      AP mid-wake: the wake/scan should be abandoned and the sync start.
- [ ] `bd wifi` after `bd wake <name>` should list the camera's SSID.
- [ ] `bd stats <name> --joined` on the right AP, on the other camera's AP and
      on the home network (the last two must refuse).
- [ ] `bd prune <name> --joined` must refuse unless on that camera's AP.

Any setup:

- [ ] Press Enter during the BLE scan, the AP wait and the join in turn. Each
      should move on at once; a real WiFi scan in flight (~7s) finishes first.

The ProfileID fallback (turn the helper off under Location Services to test):

- [ ] `bd identify <name>` on each camera: expect `recorded ProfileID …` and a
      `profile_id` line in that camera's section of `config.toml`. This is the
      open question — whether a network joined through `networksetup` with a
      password gets a `ProfileID` at all. The two cameras' values must differ.
- [ ] With it recorded, `bd stats <name>` while on the AP should skip the
      wake+join, and the "can't tell which WiFi network" hint should be gone.
- [ ] Does a `ProfileID` survive forgetting and re-saving the network? If not,
      `identify` again is the fix; note it in `docs/camera-api.md`.

### `bd sync --retry`

- [ ] After a normal sync, manually create a `.error.txt` sidecar for one of the
      already-downloaded files (e.g. `touch 20260510T130001_00000001.jpg.error.txt`
      next to the matching file in the output dir).
- [ ] Run `bd sync` without `--retry` — confirm the file is NOT re-downloaded
      (it's below the watermark).
- [ ] Run `bd sync --retry` — confirm the file is re-downloaded, `[retry]` appears
      in the output line, and the sidecar is gone afterwards.

### Health checks

- [ ] With a camera on solar/ext power (reporting 0% battery), confirm no
      battery-low warning fires (`check_battery` suppresses 0%).
- [ ] Run `bd sync <name> --log auto` and inspect the log file: warning lines
      should be plain `  ! ...` with no ANSI escape codes; on the terminal they
      should appear yellow (warn) or red (alert).
- [ ] Confirm `bd stats` completes without error even if `/cmd/info/4` returns
      an unexpected shape — clock check should be silently skipped, not fatal.

**Clock-sync bands**
- [ ] Large drift (≥ threshold): warn + prompt appears; decline it (or let it time out)
      and confirm the end-of-sync re-warn fires before power-off.
- [ ] `bd clock <name> --sync`: the set lands within ~1s of laptop time (not your
      answer-delay behind), confirming the stale-capture + round-not-floor fixes.

Things to confirm on hardware next time each camera is in range.
Update `docs/camera-models.md` with findings afterwards.

**Two of these cannot be done incidentally** (confirmed on site 2026-08-24):
video needs a special trip to switch a camera into video mode — neither records
video now, and all 2337 files across both cameras are JPG — and the battery
check needs the solar panels physically unplugged, which is awkward enough to
be its own errand. Don't expect either to fall out of a routine sync.

### GardePro E6PMB

- [ ] Confirm video download — need videos on the SD card; check `bd ls` for
      any `type 2` files, or trigger a recording
- [ ] Confirm `bd stats` shows a non-zero battery percentage on battery-only
      power (no external solar) — verifies the `voltage`/`battery` fallback
- [ ] review output of `bd settings` (raw log on file: `bd-settings-east.log`;
      `/cmd/getParaSetting` shows valid values for each field)

### GardePro E8 2.0 Pro

- [ ] Confirm video download — check `bd ls` for type 2 files
- [ ] Confirm `bd stats` shows a non-zero battery percentage on battery-only
      power (no external solar) — verifies the `voltage`/`battery` fallback
- [ ] review output of `bd settings` (raw log on file: `bd-settings-norw.log`;
      `/cmd/getParaSetting` shows valid values for each field)

## macOS 26 (Tahoe) SSID redaction

macOS 26 redacts WiFi SSIDs from any process that does not itself hold Location
permission. A command-line process can't: macOS attributes the permission to
the terminal, which is why `authorizedAlways` plus a live location fix in a
Python process still read null and looked like "Location is not sufficient".
An app bundle launched through LaunchServices can — that is
`BushDumpWiFiHelper` (measured on 27.0.1; not yet run on 26). Apple's restricted
`com.apple.developer.networking.wifi-info` entitlement is not needed, and
ad-hoc signing a binary with it gets the process SIGKILLed by AMFI. `sudo` does
not bypass the redaction, and `system_profiler` is redacted too. Not an
Apple-silicon issue; it's the OS version.

**The workaround**: CoreWLAN still *filters* by name even though it won't
report one — `scanForNetworksWithName_("G")` returns 2 hits, a bogus name
returns 0. So we can ask "is this AP present?" without ever reading a name.
Presence is three-state (present / absent / unknown) because a scan can come
back `Resource busy` — and unknown must never be read as absent. Unknowns are
the *common* case, not a rarity: measured off-site 2026-08-24 at the real 3s
interval, a genuine scan takes ~7s and answers, then the next ~3 polls throttle,
giving ~2/3 unknown overall. The earlier "no busy at 3s on site" reading came
from scanning for an implausible name, which macOS answers from cache without
ever scanning — see "Directed-scan throttling" in `docs/camera-api.md`. So poll
through unknowns and keep the last definite answer; never let a run of them
become the verdict.

Also unredacted: `networksetup -listpreferredwirelessnetworks` (saved networks,
no admin needed), the "Could not find network X" error from
`-setairportnetwork`, WiFi power state, and BLE.

### macOS 27 (Golden Gate) — measured 27.0.1, 2026-10-09

Stricter again, the same inside and outside a sandbox:

- `networksetup -getairportnetwork` says "You are not associated with an
  AirPort network" while joined — wrong, not merely redacted.
- `arp -an` is empty even after pinging the router; `ndp` and `ifconfig` show
  every MAC as `02:00:00:00:00:00`. CoreWLAN `hardwareAddress()` and
  `networksetup -listallhardwareports` still give the real one.
- SSID and BSSID stay redacted in CoreWLAN, `ipconfig getsummary`,
  `system_profiler`, `ioreg` and `scutil`.
- Turning off "Limit IP Address Tracking" and "Private Wi-Fi Address" changes
  none of it.
- Joining a *saved* network through `-setairportnetwork` with no password fails
  (`Error: -3900`) and bounces the association; with the password it joins.
- An app bundle with Location permission reads the SSID, the BSSID and the
  names in a scan. It must be launched with `open`: run directly, the binary is
  the terminal's child again and gets nothing. Signed with a keychain
  code-signing identity it kept the grant across a rebuild and a move to
  another folder; an ad-hoc build lost it on rebuild.
- Still readable from the CLI: IP, router, DHCP server, channel, RSSI, security type, the
  directed scan, and **`ProfileID`** in `scutil`'s
  `State:/Network/Interface/<iface>/AirPort` — identical across four reads and
  three rejoins of one network (including after the Mac's own MAC changed),
  different on a second network. Not a plain SHA-256 of the SSID.

### Code

- [ ] `register` without the helper app — offer saved networks as a pick-list
      (camera-likely first), plus join-and-diff to detect a brand-new camera.
      With the helper the live list works again, so this only matters where it
      isn't installed. Where the BLE module programs System ID, derive the SSID
      from GATT instead (see `docs/camera-api.md`). The pick-list source is
      `networksetup -listpreferredwirelessnetworks <iface>`, which macOS 26 does
      not redact.
- [ ] `_wake_join` skips the wake+join when `wifi.identify` says we are
      already on the camera's network — at the start and during every wait.
      Probing `camera_host` alone was never enough: every camera answers on
      `192.168.8.1:8080`, so it could take camera A for camera B and file A's
      photos under B. Unverified on hardware; see "Wake/join fast path" under
      the smoke tests.

      **Failed-join cost measured** (off-site, 2026-08-24, stale BLE address so
      the camera is unreachable): `bd sync <name>` took **208s** before giving
      up, and **61s** after the early bail (`_wake_join` gives up when BLE
      completed a scan without seeing the camera *and* its AP is definitely
      absent, from the second attempt on).

      **A redundant wake+join is not disruptive** (on site, 2026-09-06):
      `bd stats east` run in a second terminal, while a sync was downloading
      over east's AP in the first, neither dropped the sync nor stalled it. So
      `networksetup -setairportnetwork` against the SSID you are already on
      looks like a no-op rather than a re-association.
- [ ] Spike the AT command set over BLE UART for a WiFi-status query — would let
      us poll wake state without any WiFi scan. **Writes to the camera**: query
      forms only, never the `=` setter, deny-list RST/RESTORE/RESET/DEFAULT/
      ERASE/FORMAT. See `docs/camera-api.md` "The wake is an AT command set".

## iOS app (future project, own spec)

Collect images in the field without carrying the laptop. iOS gets "Access WiFi
Information" as a normal capability, so the SSID problem largely evaporates
there. Could be a stepping stone to the laptop, or push straight to the NAS
using the existing backup logic. Needs a paid Apple developer account (renews
annually — same expiry trap as entitling the CLI, but here it buys a real
product rather than working around a permission).
