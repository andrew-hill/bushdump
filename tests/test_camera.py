from bushdump.camera import CameraFile, parse_file_page, parse_info2, parse_info3


def test_camerafile_from_json_parses_fields():
    obj = {"id": "42", "type": "1", "date": "2026-05-10 13:00:01", "size": "204800"}
    f = CameraFile.from_json(obj)
    assert f.id == 42
    assert f.type == 1
    assert f.date == "2026-05-10 13:00:01"
    assert f.size == 204800
    assert isinstance(f.id, int)


def test_camerafile_kind_and_name():
    jpg = CameraFile(id=1, type=1, date="2026-05-10 13:00:01", size=100)
    assert jpg.kind == "JPG"
    assert jpg.name == "20260510T130001_00000001.jpg"
    mp4 = CameraFile(id=2, type=2, date="2026-05-10 13:00:02", size=200)
    assert mp4.kind == "MP4"
    assert mp4.name == "20260510T130002_00000002.mp4"


def test_parse_file_page_data_envelope():
    data = {"code": 0, "data": [{"id": 1, "type": 1, "date": "2026-05-10 13:00:01", "size": 100}]}
    result = parse_file_page(data)
    assert len(result) == 1
    assert result[0].id == 1


def test_parse_file_page_skips_malformed():
    assert parse_file_page({"code": 0, "data": [{"id": 1}]}) == []  # missing fields
    assert parse_file_page({"code": 0, "data": []}) == []
    assert parse_file_page(None) == []


def test_parse_info2_nominal():
    data = {"code": 0, "data": {"battery": 85, "temperature": 22, "ext_power": True}}
    battery, temp, ext = parse_info2(data)
    assert battery == 85
    assert temp == 22
    assert ext is True


def test_parse_info2_missing_fields():
    battery, temp, ext = parse_info2({"code": 0, "data": {}})
    assert battery == 0
    assert temp == 0
    assert ext is False


def test_parse_info2_bad_shape():
    assert parse_info2(None) == (0, 0, False)
    assert parse_info2("garbage") == (0, 0, False)


def test_parse_info3_nominal():
    data = {"code": 0, "data": {"total": 32000, "used": 8192, "photo": 120, "video": 5}}
    total, used, photos, videos = parse_info3(data)
    assert total == 32000
    assert used == 8192
    assert photos == 120
    assert videos == 5


def test_parse_info3_missing_fields():
    assert parse_info3({"code": 0, "data": {}}) == (0, 0, 0, 0)
    assert parse_info3(None) == (0, 0, 0, 0)
