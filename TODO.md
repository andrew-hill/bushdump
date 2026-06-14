# TODO

- [ ] Integrate clock sync into `bd sync` — `bd clock --sync` works standalone;
      consider auto-syncing on each connection if the clock is out-of-sync
      (subject to NTP confidence check), or at least prompting the user.
- [ ] Add more graceful error handling to avoid large trace outputs from reasonably
      expected issues.
- [ ] Bring MP4 validation in line with JPEG validation.
- [ ] Improve JPEG pixel-corruption detection: `corrupt-scan-zeros.jpg` and
      `corrupt-scan-flip.jpg` in `tests/fixtures/corrupt-jpegs/` currently pass
      validation because libjpeg's error concealment fills in corrupted MCUs with
      plausible-looking repeated rows. Would need to detect large runs of identical
      MCU rows or similar heuristic to catch partial-download / bit-flip corruption.


## Next-visit camera smoke tests

### `bd sync --retry`

- [ ] After a normal sync, manually create a `.error.txt` sidecar for one of the
      already-downloaded files (e.g. `touch 20260510T130001_00000001.jpg.error.txt`
      next to the matching file in the output dir).
- [ ] Run `bd sync` without `--retry` — confirm the file is NOT re-downloaded
      (it's below the watermark).
- [ ] Run `bd sync --retry` — confirm the file is re-downloaded, `[retry]` appears
      in the output line, and the sidecar is gone afterwards.

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
