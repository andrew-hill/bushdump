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
- [ ] `Already on '<ssid>' — skipping wake+join` should **not** appear on Tahoe.
      If it does, `networksetup -getairportnetwork` is not redacted after all,
      the short-circuit works on 26 too, and the "join first, then probe" item
      below is half solved. Worth one `bd stats <name>` while already on the AP
      to check deliberately.
- [ ] While joined, run `arp -n 192.168.8.1` and compare the gateway MAC to the
      MAC in the SSID — the identity check that would let the short-circuit work
      under redaction. See the `_wake_join` item under "Code".

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

macOS 26 redacts WiFi SSIDs from any process lacking Apple's restricted
`com.apple.developer.networking.wifi-info` entitlement (needs a provisioning
profile from a paid developer account; ad-hoc signing it gets the process
SIGKILLed by AMFI). Location authorization is necessary but **not** sufficient —
confirmed with `authorizedAlways` plus a live location fix, SSIDs still null.
`sudo` does not bypass it, and `system_profiler` is redacted too. Not an
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

### Code

- [ ] `register` — offer saved networks as a pick-list (camera-likely first),
      plus join-and-diff to detect a brand-new camera. Live SSID listing can't
      work under redaction. Where the BLE module programs System ID, derive the
      SSID from GATT instead (see `docs/camera-api.md`). The pick-list source is
      `networksetup -listpreferredwirelessnetworks <iface>`, which macOS 26 does
      not redact; `wifi.saved_ssids()` wrapped it but was deleted as dead code,
      so rebuild it here when this lands.
- [ ] `_wake_join` always wakes+joins now. The old "already reachable, skip
      wake+join" short-circuit probed `camera_host`, which every camera answers
      on (`192.168.8.1:8080` for all of them), so it could take camera A for
      camera B and file A's photos under B. Joining by SSID is what actually
      identifies a camera. If the extra BLE wake grates on site, reorder rather
      than restore: join first (identity), then probe, and skip the wake when
      the camera already answers.

      **Failed-join cost measured** (off-site, 2026-08-24, stale BLE address so
      the camera is unreachable): `bd sync <name>` takes **208s** before it
      gives up — 3×20s BLE scans + 3×15s AP-boot checks + 40s final wait + 45s
      join. Every BLE attempt returned the conclusive "device not found within
      20s", and `_wake_and_report` swallows that into a bare `False`, so the
      full ladder runs for a camera BLE never saw.

      **Early bail done 2026-08-24** — `_wake_and_report` now returns a
      `WakeOutcome`, and `_wake_join` gives up when BLE completed a scan without
      seeing the camera *and* its AP is definitely absent, from the second
      attempt on. An out-of-range `bd sync <name>` went 208s → 61s, measured.
      Still to do here: **join first, then probe**, so a command run while
      already on the right camera's AP skips the wake entirely. That is the
      other half of this item and is untouched.

      **The redundant wake+join is not disruptive** (on site, 2026-09-06):
      `bd stats east` run in a second terminal, while a sync was downloading
      over east's AP in the first, neither dropped the sync nor stalled it —
      one file of 858 dipped below 1 MB/s in that run, against 1.9 MB/s
      average. So `networksetup -setairportnetwork` against the SSID you are
      already on looks like a no-op rather than a re-association, and this item
      is a time saving only. Nothing here has to be fixed before a concurrent
      command is safe.

      An identity check that needs no rejoin, untested: SSIDs carry the WiFi
      MAC (`CAM8Z8_<mac>`), so `arp -n 192.168.8.1` after joining should
      name the device with no entitlement involved. Worth one check on site
      that the gateway MAC really does match the SSID's.
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
