import pytest

from bushdump.camera import CameraFile
from bushdump.prune import LocalFile, classify_for_prune, parse_cutoff, scan_local_dir


def _cam_file(
    id: int, date: str = "2026-04-15 10:00:00", size: int = 1000, type: int = 1
) -> CameraFile:
    return CameraFile(id=id, date=date, size=size, type=type)


def _local_file(name: str, size: int = 1000, has_error: bool = False) -> LocalFile:
    return LocalFile(name=name, size=size, has_error_sidecar=has_error)


CUTOFF = "2026-05-01 00:00:00"
WATERMARK = "2026-04-30 23:59:59"


# --- parse_cutoff ---


def test_parse_cutoff_date_only():
    assert parse_cutoff("2026-05-01") == "2026-05-01 00:00:00"


def test_parse_cutoff_full_datetime():
    assert parse_cutoff("2026-05-01 12:30:00") == "2026-05-01 12:30:00"


def test_parse_cutoff_strips_whitespace():
    assert parse_cutoff("  2026-05-01  ") == "2026-05-01 00:00:00"


def test_parse_cutoff_rejects_slash_format():
    with pytest.raises(ValueError):
        parse_cutoff("2026/05/01")


def test_parse_cutoff_rejects_garbage():
    with pytest.raises(ValueError):
        parse_cutoff("garbage")


def test_parse_cutoff_boundary_is_not_older():
    # A file at exactly the cutoff is NOT deleted (f.date >= cutoff → skip)
    cutoff = parse_cutoff("2026-05-01")
    f = _cam_file(1, date="2026-05-01 00:00:00")
    local = {f.name: _local_file(f.name)}
    verdicts = classify_for_prune([f], local=local, backup_watermark=WATERMARK, cutoff_date=cutoff)
    assert verdicts[0].reason == "newer than cutoff"


# --- classify_for_prune ---


def test_classify_deletable():
    f = _cam_file(1)
    local = {f.name: _local_file(f.name, size=1000)}
    verdicts = classify_for_prune([f], local=local, backup_watermark=WATERMARK, cutoff_date=CUTOFF)
    assert len(verdicts) == 1
    assert verdicts[0].deletable is True
    assert verdicts[0].reason == ""


def test_classify_newer_than_cutoff():
    f = _cam_file(1, date="2026-06-01 10:00:00")
    verdicts = classify_for_prune([f], local={}, backup_watermark=WATERMARK, cutoff_date=CUTOFF)
    assert verdicts[0].deletable is False
    assert verdicts[0].reason == "newer than cutoff"


def test_classify_not_backed_up_no_watermark():
    f = _cam_file(1)
    verdicts = classify_for_prune([f], local={}, backup_watermark=None, cutoff_date=CUTOFF)
    assert verdicts[0].reason == "not confirmed backed up"


def test_classify_not_backed_up_after_watermark():
    f = _cam_file(1, date="2026-04-29 10:00:00")
    verdicts = classify_for_prune(
        [f], local={}, backup_watermark="2026-04-28 00:00:00", cutoff_date=CUTOFF
    )
    assert verdicts[0].reason == "not confirmed backed up"


def test_classify_not_downloaded():
    f = _cam_file(1)
    verdicts = classify_for_prune([f], local={}, backup_watermark=WATERMARK, cutoff_date=CUTOFF)
    assert verdicts[0].reason == "not downloaded locally"


def test_classify_size_mismatch():
    f = _cam_file(1, size=1000)
    local = {f.name: _local_file(f.name, size=999)}
    verdicts = classify_for_prune([f], local=local, backup_watermark=WATERMARK, cutoff_date=CUTOFF)
    assert verdicts[0].reason == "size mismatch — re-download"


def test_classify_error_sidecar():
    f = _cam_file(1)
    local = {f.name: _local_file(f.name, size=1000, has_error=True)}
    verdicts = classify_for_prune([f], local=local, backup_watermark=WATERMARK, cutoff_date=CUTOFF)
    assert verdicts[0].reason == "validation failed (.error.txt)"


def test_classify_gate_order_cutoff_beats_no_watermark():
    # File is newer than cutoff AND not downloaded — first gate (cutoff) wins
    f = _cam_file(1, date="2026-06-01 10:00:00")
    verdicts = classify_for_prune([f], local={}, backup_watermark=None, cutoff_date=CUTOFF)
    assert verdicts[0].reason == "newer than cutoff"


def test_classify_beyond_watermark_beats_not_downloaded():
    # watermark gate fires before "not downloaded"
    f = _cam_file(1, date="2026-04-29 10:00:00")
    verdicts = classify_for_prune(
        [f], local={}, backup_watermark="2026-04-28 00:00:00", cutoff_date=CUTOFF
    )
    assert verdicts[0].reason == "not confirmed backed up"


def test_classify_multiple_files():
    f_old = _cam_file(1, date="2026-04-10 10:00:00")
    f_new = _cam_file(2, date="2026-06-01 10:00:00")
    local = {f_old.name: _local_file(f_old.name)}
    verdicts = classify_for_prune(
        [f_old, f_new], local=local, backup_watermark=WATERMARK, cutoff_date=CUTOFF
    )
    assert verdicts[0].deletable is True
    assert verdicts[1].reason == "newer than cutoff"


# --- scan_local_dir ---


def test_scan_local_dir_canonical_only(tmp_path):
    (tmp_path / "20260510T130001_00000001.jpg").write_bytes(b"x" * 500)
    (tmp_path / "20260510T130002_00000002.mp4").write_bytes(b"y" * 1000)
    # Artifacts that should be excluded as keys
    (tmp_path / "20260510T130001_00000001.jpg.error.txt").write_text("err")
    (tmp_path / "20260510T130003_00000003.jpg.part").write_bytes(b"z" * 100)
    (tmp_path / ".DS_Store").write_bytes(b"\x00")

    result = scan_local_dir(tmp_path)

    assert set(result.keys()) == {
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.mp4",
    }
    assert result["20260510T130001_00000001.jpg"].size == 500
    assert result["20260510T130001_00000001.jpg"].has_error_sidecar is True
    assert result["20260510T130002_00000002.mp4"].has_error_sidecar is False


def test_scan_local_dir_missing(tmp_path):
    assert scan_local_dir(tmp_path / "absent") == {}
