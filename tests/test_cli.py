from unittest.mock import MagicMock, patch

from bushdump import cli
from bushdump.camera import CameraFile


def test_command_aliases_resolve_to_canonical_handlers():
    parser = cli.build_parser()

    cases = [
        (["cams"], cli.cmd_cameras),
        (["reg"], cli.cmd_register),
        (["s", "frontgate"], cli.cmd_sync),
        (["w", "frontgate"], cli.cmd_wake),
        (["st", "frontgate"], cli.cmd_stats),
        (["ka", "frontgate"], cli.cmd_keepalive),
    ]

    for argv, handler in cases:
        args = parser.parse_args(argv)
        assert args.func is handler


def test_clock_parser():
    parser = cli.build_parser()

    args = parser.parse_args(["clock", "frontgate"])
    assert args.func is cli.cmd_clock
    assert args.name == "frontgate"
    assert args.sync is False

    args = parser.parse_args(["clock", "frontgate", "--sync"])
    assert args.func is cli.cmd_clock
    assert args.sync is True


def test_settings_parser():
    parser = cli.build_parser()
    args = parser.parse_args(["settings", "frontgate"])
    assert args.func is cli.cmd_settings
    assert args.name == "frontgate"


def _make_settings_client(settings: dict | None = None, ready: bool = True) -> MagicMock:
    client = MagicMock()
    client.wait_until_ready.return_value = ready
    client.get_settings.return_value = settings or {"resolution": "4K", "video_length": "30s"}
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)
    return client


def test_settings_prints_key_value(capsys):
    mock_cam = MagicMock()
    mock_cam.camera_host = "192.168.8.1:8080"
    client = _make_settings_client({"resolution": "4K", "video_length": "30s"})

    with (
        patch("bushdump.cli._resolve_camera", return_value=mock_cam),
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
    ):
        args = cli.build_parser().parse_args(["settings", "frontgate"])
        result = args.func(args)

    assert result == 0
    out = capsys.readouterr().out
    assert "resolution: 4K" in out
    assert "video_length: 30s" in out


def test_settings_unknown_camera_returns_nonzero():
    with patch("bushdump.cli._resolve_camera", return_value=None):
        args = cli.build_parser().parse_args(["settings", "bogus"])
        result = args.func(args)
    assert result == 1


def test_settings_http_not_ready_returns_nonzero():
    mock_cam = MagicMock()
    mock_cam.camera_host = "192.168.8.1:8080"
    client = _make_settings_client(ready=False)

    with (
        patch("bushdump.cli._resolve_camera", return_value=mock_cam),
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
    ):
        args = cli.build_parser().parse_args(["settings", "frontgate"])
        result = args.func(args)

    assert result == 1
    client.get_settings.assert_not_called()


def test_settings_api_error_returns_nonzero(capsys):
    mock_cam = MagicMock()
    mock_cam.camera_host = "192.168.8.1:8080"
    client = _make_settings_client()
    client.get_settings.side_effect = RuntimeError("bad response")

    with (
        patch("bushdump.cli._resolve_camera", return_value=mock_cam),
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
    ):
        args = cli.build_parser().parse_args(["settings", "frontgate"])
        result = args.func(args)

    assert result == 1
    assert "bad response" in capsys.readouterr().err


def test_stats_expected_camera_error_returns_nonzero_without_traceback(capsys):
    mock_cam = MagicMock()
    mock_cam.camera_host = "192.168.8.1:8080"
    client = MagicMock()
    client.wait_until_ready.return_value = True
    client.stats.side_effect = RuntimeError("camera dropped connection")
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    with (
        patch("bushdump.cli._resolve_camera", return_value=mock_cam),
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
    ):
        args = cli.build_parser().parse_args(["stats", "frontgate"])
        result = args.func(args)

    assert result == 1
    captured = capsys.readouterr()
    assert "Error: camera dropped connection" in captured.err
    assert "Traceback" not in captured.err


def test_ls_prints_listing_progress(capsys):
    mock_cam = MagicMock()
    mock_cam.name = "frontgate"
    mock_cam.camera_host = "192.168.8.1:8080"
    client = MagicMock()
    client.wait_until_ready.return_value = True

    def list_all_files(on_page=None):
        on_page(50)
        on_page(75)
        return [CameraFile(id=1, type=1, date="2026-05-10 13:00:01", size=2048)]

    client.list_all_files.side_effect = list_all_files
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    with (
        patch("bushdump.cli._resolve_camera", return_value=mock_cam),
        patch("bushdump.cli._wake_join"),
        patch("bushdump.config.load_state", return_value={}),
        patch("bushdump.camera.CameraClient", return_value=client),
    ):
        args = cli.build_parser().parse_args(["ls", "frontgate"])
        result = args.func(args)

    assert result == 0
    out = capsys.readouterr().out
    assert "Listing files..." in out
    assert "  ... 75 files" in out
    assert "1 files on camera" in out


def test_sync_warns_on_corrupt_download(tmp_path, capsys):
    file = CameraFile(id=1, type=1, date="2026-05-10 13:00:01", size=1024)
    dest = tmp_path / file.name
    sidecar = dest.with_name(dest.name + ".error.txt")
    dest.write_bytes(b"\x00" * 100)
    sidecar.write_text("Validation failed: invalid JPEG\n")

    mock_cam = MagicMock()
    mock_cam.name = "frontgate"
    mock_cam.camera_host = "192.168.8.1:8080"
    mock_cam.ssid = "TestCam_AP"
    mock_cam.output_dir = tmp_path

    client = MagicMock()
    client.wait_until_ready.return_value = True
    client.list_all_files.return_value = [file]
    client.download.return_value = dest
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    args = MagicMock()
    args.manual_wifi = False
    args.keep_awake = False

    with (
        patch("bushdump.wifi.current_ssid", return_value="TestCam_AP"),
        patch("bushdump.camera.CameraClient", return_value=client),
        patch("bushdump.config.save_state"),
    ):
        n, conflicts = cli._sync_one(mock_cam, {}, args)

    assert n == 1
    err = capsys.readouterr().err
    assert "validation failed" in err
    assert sidecar.name in err


def test_command_aliases_preserve_arguments():
    parser = cli.build_parser()

    sync_args = parser.parse_args(["s", "frontgate", "--manual-wifi"])
    assert sync_args.name == "frontgate"
    assert sync_args.manual_wifi is True

    keepalive_args = parser.parse_args(["ka", "frontgate", "--interval", "3"])
    assert keepalive_args.name == "frontgate"
    assert keepalive_args.interval == 3
