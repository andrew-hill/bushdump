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

### `bd backup` / `bd prune`

**Backup happy path**
- [ ] Full cycle: `bd sync` → `bd backup` → `bd prune` — confirm each step
      sees the state left by the prior.  *(needs camera in range)*

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
- [ ] Modest drift (5s ≤ |drift| < `clock_auto_sync_secs`, default 900): `bd sync <name>`
      auto-syncs silently with a one-line info message, no prompt.
- [ ] Large drift (≥ threshold): warn + prompt appears; decline it (or let it time out)
      and confirm the end-of-sync re-warn fires before power-off.
- [ ] `bd clock <name> --sync`: the set lands within ~1s of laptop time (not your
      answer-delay behind), confirming the stale-capture + round-not-floor fixes.

Things to confirm on hardware next time each camera is in range.
Update `docs/camera-models.md` with findings afterwards.

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
back `Resource busy` — and unknown must never be read as absent. In practice
that's rare: hammering scans in a tight loop produced ~50% busy, but real
polling at 3s intervals across every on-site run produced none at all.

Also unredacted: `networksetup -listpreferredwirelessnetworks` (saved networks,
no admin needed), the "Could not find network X" error from
`-setairportnetwork`, WiFi power state, and BLE.

### Code

- [ ] `register` — offer saved networks as a pick-list (camera-likely first),
      plus join-and-diff to detect a brand-new camera. Live SSID listing can't
      work under redaction. Where the BLE module programs System ID, derive the
      SSID from GATT instead (see `docs/camera-api.md`).
- [ ] `_wake_join` always wakes+joins now. The old "already reachable, skip
      wake+join" short-circuit probed `camera_host`, which every camera answers
      on (`192.168.8.1:8080` for all of them), so it could take camera A for
      camera B and file A's photos under B. Joining by SSID is what actually
      identifies a camera. If the extra BLE wake grates on site, reorder rather
      than restore: join first (identity), then probe, and skip the wake when
      the camera already answers. Measure a *failed* join (camera asleep) first
      — that's the cost that decides whether it's worth it.
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
