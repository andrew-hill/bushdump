# TODO

## Next-visit camera smoke tests

Things to confirm on hardware next time each camera is in range.
Update `docs/camera-models.md` with findings afterwards.

### GardePro E6PMB (frontgate)

- [ ] Confirm file download works end-to-end (`bd sync frontgate` to completion
      and verify files land on disk) — the doc still says "not yet exercised"
- [ ] Confirm video download — need videos on the SD card; check `bd ls
      frontgate` for any `type 2` files, or trigger a recording
- [ ] Confirm clean power-off — let a sync finish normally and verify the
      camera's WiFi drops (currently inferred, not observed)
- [ ] Run `bd stats frontgate` and confirm battery/SD output is sensible

### GardePro E8 2 (norw)

- [ ] Investigate wake reliability — try `tools/wake.py` several times in
      a row, note whether it always needs multiple attempts or was a one-off;
      try `tools/wake.py --probe-all` to see if another characteristic responds
      more reliably
- [ ] Run `tools/probe-http.py` while on its AP — confirms `/cmd/info/1..5`,
      `/cmd/getSetting`, and the full HTTP shape match E6PMB
- [ ] Run `bd stats norw` — quick sanity check of battery and SD
- [ ] Confirm clean power-off — let `bd sync norw` finish without interruption
      and verify WiFi drops
- [ ] Confirm video download — check `bd ls norw` for type 2 files
- [ ] Try `bd register` for norw from scratch (once wake is reliable) to
      confirm the guided flow works end-to-end for this model
