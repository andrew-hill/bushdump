from bushdump.camera import CameraFile


def test_camerafile_from_json_parses_fields():
    obj = {"n": "IMG_0001.jpg", "dt": "1700000000", "s": "204800", "fid": "42"}
    f = CameraFile.from_json(obj)
    assert f.name == "IMG_0001.jpg"
    assert f.timestamp == 1700000000
    assert f.size == 204800
    assert f.fid == "42"
    assert isinstance(f.timestamp, int)
