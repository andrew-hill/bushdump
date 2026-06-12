# TODO

- [ ] Add time check + sync (check method in github.com/fede2cr/camtrap-control/)
      Consider doing automatically upon any connection if time is out-of-sync,
      or via user-prompt to confirm, subject to a signal that the client is
      genuinely in-sync (e.g. via NTP).
- [ ] Add more graceful error handling to avoid large trace outputs from reasonably
      expected issues.
- [ ] Add `bd sync --retry` to re-download files that already have a `.error.txt` sidecar.
- [ ] Bring MP4 validation in line with JPEG validation.
- [ ] Improve JPEG pixel-corruption detection: `corrupt-scan-zeros.jpg` and
      `corrupt-scan-flip.jpg` in `tests/fixtures/corrupt-jpegs/` currently pass
      validation because libjpeg's error concealment fills in corrupted MCUs with
      plausible-looking repeated rows. Would need to detect large runs of identical
      MCU rows or similar heuristic to catch partial-download / bit-flip corruption.

## Next-visit camera smoke tests

Things to confirm on hardware next time each camera is in range.
Update `docs/camera-models.md` with findings afterwards.

### GardePro E6PMB

- [ ] Confirm video download — need videos on the SD card; check `bd ls` for
      any `type 2` files, or trigger a recording
- [ ] Confirm `bd sync` final count is correct and no traceback (was broken by
      an abrupt power-off race; fixed in code — verify next visit)
- [ ] Try `bd clock east --sync` and record whether `/cmd/setGmtClock` updates
      `/cmd/info/4` correctly.
- [ ] review output of `bd settings` (raw log on file: `bd-settings-east.log`;
      `/cmd/getParaSetting` shows valid values for each field)

### GardePro E8 2.0 Pro

- [ ] Confirm video download — check `bd ls` for type 2 files
- [ ] Confirm `bd stats` shows a non-zero battery percentage on battery-only
      power (no external solar) — verifies the `voltage`/`battery` fallback
- [ ] Try `bd clock norw --sync` and record whether `/cmd/setGmtClock` updates
      `/cmd/info/4` correctly.
- [ ] review output of `bd settings` (raw log on file: `bd-settings-norw.log`;
      `/cmd/getParaSetting` shows valid values for each field)
