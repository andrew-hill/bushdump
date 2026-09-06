from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from bushdump import camera as camera_mod
from bushdump.camera import (
    CameraClient,
    CameraFile,
    format_gmt_clock,
    parse_file_page,
    parse_info2,
    parse_info3,
)


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


def test_parse_info2_voltage_fallback():
    # E8 2.0 Pro firmware reports "voltage" instead of "battery"; ext_power is an int
    data = {
        "code": 0,
        "data": {"voltage": 100, "vol_value": 4182, "temperature": 21, "ext_power": 2},
    }
    battery, temp, ext = parse_info2(data)
    assert battery == 100
    assert temp == 21
    assert ext is True


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


# --- format_gmt_clock ---


def test_format_gmt_clock_rounds_up():
    when = datetime(2026, 7, 29, 10, 15, 32, 600_000, tzinfo=UTC)
    assert format_gmt_clock(when) == "2026-07-29 10:15:33"


def test_format_gmt_clock_rounds_down():
    when = datetime(2026, 7, 29, 10, 15, 32, 400_000, tzinfo=UTC)
    assert format_gmt_clock(when) == "2026-07-29 10:15:32"


def test_format_gmt_clock_converts_to_utc():
    # UTC+10 aware datetime → converted to UTC before formatting.
    tz = timezone(timedelta(hours=10))
    when = datetime(2026, 7, 29, 20, 15, 32, tzinfo=tz)
    assert format_gmt_clock(when) == "2026-07-29 10:15:32"


# --- CameraClient.delete ---


def _make_http_client(response_body: object) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = response_body
    resp.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.get.return_value = resp
    return mock_http


def test_delete_calls_correct_url():
    f = CameraFile(id=42, date="2026-04-15 10:00:00", size=1000, type=1)
    mock_http = _make_http_client({"code": 0})
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        client.delete(f)
    mock_http.get.assert_called_once_with("/cmd/delete/42/JPG")


def test_delete_mp4_uses_correct_kind():
    f = CameraFile(id=7, date="2026-04-15 10:00:00", size=500, type=2)
    mock_http = _make_http_client({"code": 0})
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        client.delete(f)
    mock_http.get.assert_called_once_with("/cmd/delete/7/MP4")


def test_delete_raises_on_non_zero_code():
    f = CameraFile(id=1, date="2026-04-15 10:00:00", size=1000, type=1)
    mock_http = _make_http_client({"code": 1, "msg": "error"})
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        with pytest.raises(RuntimeError, match="Delete failed"):
            client.delete(f)


def test_delete_raises_on_bad_shape():
    f = CameraFile(id=1, date="2026-04-15 10:00:00", size=1000, type=1)
    mock_http = _make_http_client("unexpected string")
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        with pytest.raises(RuntimeError, match="Delete failed"):
            client.delete(f)


# --- CameraClient._stream_to_tmp retries ---


def _make_stream_client(outcomes: list[object]) -> MagicMock:
    """A stubbed httpx client whose `stream()` replays `outcomes` in order.

    An exception instance is raised on that attempt; anything else is treated as
    the body bytes to yield. Also answers `/cmd/standby/reset` as ready, so the
    between-attempt `wait_until_ready` returns on its first poll without sleeping.
    """
    calls = iter(outcomes)

    def _stream(_method: str, _url: str) -> MagicMock:
        outcome = next(calls)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if isinstance(outcome, Exception):
            resp.iter_bytes.side_effect = outcome
        else:
            resp.iter_bytes.return_value = [outcome]
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=resp)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    ready = MagicMock()
    ready.status_code = 200
    ready.json.return_value = {"code": 0}
    mock_http = MagicMock()
    mock_http.stream.side_effect = _stream
    mock_http.get.return_value = ready
    return mock_http


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadError("connection reset by peer"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("incomplete chunk"),
        httpx.ConnectError("no route"),
    ],
)
def test_stream_to_tmp_retries_transient_transport_errors(tmp_path, exc):
    f = CameraFile(id=1, date="2026-08-23 10:00:00", size=4, type=1)
    mock_http = _make_stream_client([exc, b"good"])
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        client._stream_to_tmp(f, tmp_path / "x.jpg.part")
    assert (tmp_path / "x.jpg.part").read_bytes() == b"good"
    assert mock_http.stream.call_count == 2


def test_stream_to_tmp_discards_partial_bytes_between_attempts(tmp_path):
    """A truncated first attempt must not be prepended to the retry."""
    f = CameraFile(id=1, date="2026-08-23 10:00:00", size=4, type=1)
    mock_http = _make_stream_client([httpx.ReadError("reset"), b"good"])
    tmp = tmp_path / "x.jpg.part"
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        client._stream_to_tmp(f, tmp)
    assert tmp.read_bytes() == b"good"


def test_stream_to_tmp_gives_up_after_repeated_failures(tmp_path):
    f = CameraFile(id=1, date="2026-08-23 10:00:00", size=4, type=1)
    resets = [httpx.ReadError("reset")] * 10
    mock_http = _make_stream_client(resets)
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        with pytest.raises(httpx.ReadError):
            client._stream_to_tmp(f, tmp_path / "x.jpg.part")
    assert mock_http.stream.call_count == camera_mod.STREAM_ATTEMPTS


def test_stream_to_tmp_does_not_retry_non_transport_errors(tmp_path):
    """A bug in our own code should surface at once, not be retried four times."""
    f = CameraFile(id=1, date="2026-08-23 10:00:00", size=4, type=1)
    mock_http = _make_stream_client([ValueError("boom"), b"good"])
    with patch("httpx.Client", return_value=mock_http):
        client = CameraClient()
        with pytest.raises(ValueError):
            client._stream_to_tmp(f, tmp_path / "x.jpg.part")
    assert mock_http.stream.call_count == 1
