from types import SimpleNamespace

from bushdump.camera import CameraFile
from bushdump.sync import cameras_present, files_to_download


def _file(id: int) -> CameraFile:
    # date encodes id as seconds so ordering is unambiguous in tests
    return CameraFile(id=id, type=1, date=f"2026-05-10 13:00:{id:02d}", size=100)


def test_first_run_downloads_everything_oldest_first():
    files = [_file(3), _file(1), _file(2)]
    result = files_to_download(files, watermark=None)
    assert [f.id for f in result] == [1, 2, 3]


def test_only_files_newer_than_watermark():
    files = [_file(1), _file(2), _file(3)]
    result = files_to_download(files, watermark=_file(2).date)
    assert [f.id for f in result] == [2, 3]


def test_watermark_second_is_rechecked_for_interrupted_runs():
    files = [
        CameraFile(id=1, type=1, date="2026-05-10 13:00:02", size=100),
        CameraFile(id=2, type=1, date="2026-05-10 13:00:02", size=100),
        CameraFile(id=3, type=1, date="2026-05-10 13:00:03", size=100),
    ]
    result = files_to_download(files, watermark="2026-05-10 13:00:02")
    assert [f.id for f in result] == [1, 2, 3]


def test_legacy_int_watermark_treated_as_first_run():
    files = [_file(1), _file(2), _file(3)]
    result = files_to_download(files, watermark=2)  # type: ignore[arg-type]
    assert [f.id for f in result] == [1, 2, 3]


def _cam(name, ble):
    return SimpleNamespace(name=name, ble_address=ble)


def test_cameras_present_matches_case_insensitively():
    cams = [_cam("front", "AAA-111"), _cam("back", "BBB-222")]
    result = cameras_present(cams, {"aaa-111"})
    assert [c.name for c in result] == ["front"]


def test_cameras_present_skips_cameras_without_address():
    cams = [_cam("noble", None), _cam("yes", "CCC")]
    result = cameras_present(cams, {"ccc", "ddd"})
    assert [c.name for c in result] == ["yes"]
