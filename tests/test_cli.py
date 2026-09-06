from unittest.mock import MagicMock, patch

import httpx

from bushdump import cli
from bushdump.camera import CameraFile, CameraStats


def _healthy_stats() -> CameraStats:
    return CameraStats(
        battery=80,
        temperature=22,
        ext_power=False,
        sd_total_kb=128 * 1024 * 1024,
        sd_used_kb=10 * 1024 * 1024,
        photo_count=0,
        video_count=0,
    )


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
    client.stats.return_value = _healthy_stats()
    client.parsed_time_info.return_value = None
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    mock_cam.expect_ext_power = False

    args = MagicMock()
    args.manual_wifi = False
    args.keep_awake = False

    with (
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
        patch("bushdump.config.save_state"),
    ):
        result = cli._sync_one(mock_cam, {}, args)

    assert result.downloaded == 1
    assert not result.stopped_early
    err = capsys.readouterr().err
    assert "validation failed" in err
    assert sidecar.name in err


def test_sync_retry_rerequests_sidecar_files(tmp_path, capsys):
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
    client.stats.return_value = _healthy_stats()
    client.parsed_time_info.return_value = None

    def _download(f, dest_dir, *, retry=False):
        sidecar.unlink(missing_ok=True)
        return dest

    client.download.side_effect = _download
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    mock_cam.expect_ext_power = False

    args = MagicMock()
    args.manual_wifi = False
    args.keep_awake = False
    args.retry = True

    # Watermark is ahead of the file date so it won't appear in the normal todo
    state = {"frontgate": {"Photo": "2026-05-10 14:00:00"}}

    with (
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
        patch("bushdump.config.save_state"),
    ):
        result = cli._sync_one(mock_cam, state, args)

    assert result.downloaded == 1
    out = capsys.readouterr().out
    assert "retry" in out


def test_command_aliases_preserve_arguments():
    parser = cli.build_parser()

    sync_args = parser.parse_args(["s", "frontgate", "--manual-wifi"])
    assert sync_args.name == "frontgate"
    assert sync_args.manual_wifi is True

    keepalive_args = parser.parse_args(["ka", "frontgate", "--interval", "3"])
    assert keepalive_args.name == "frontgate"
    assert keepalive_args.interval == 3


# --- wake/verify decision logic ---------------------------------------------
#
# The camera's BLE wake is unreliable in one direction only: an "OK" ack means
# it really is awake, but silence proves nothing — it often wakes anyway. On
# macOS 26 we can't read SSIDs, so AP presence is three-state and "unknown"
# must never be treated as "absent".


def _action(outcome, presence, attempt=1, max_attempts=3):
    return cli._next_wake_action(outcome, presence, attempt, max_attempts)


def test_wake_action_proceeds_on_ack():
    """An ack means the wake was accepted, so stop re-waking. It does NOT mean the
    AP is visible — detection lagged the ack by ~25s on an E6PMB — so proceeding
    means waiting for the AP, not joining this instant."""
    assert _action(cli.WakeOutcome.ACKED, None) == "proceed"


def test_wake_action_proceeds_when_ap_seen_despite_no_ack():
    """Observed on an E8 2.0 Pro: wake sent, no ack, AP up regardless."""
    assert _action(cli.WakeOutcome.SENT, True) == "proceed"


def test_wake_action_rewakes_when_ap_definitely_absent():
    assert _action(cli.WakeOutcome.SENT, False) == "rewake"


def test_wake_action_rewakes_when_presence_unknown():
    """`Resource busy` from a rate-limited scan is not evidence of absence."""
    assert _action(cli.WakeOutcome.SENT, None) == "rewake"


def test_wake_action_proceeds_anyway_after_last_attempt():
    """Out of wakes, try the join regardless — its error is our other oracle."""
    assert _action(cli.WakeOutcome.SENT, False, attempt=3) == "proceed"


# The bail needs both negatives, and needs them twice.


def test_wake_action_bails_when_ble_and_ap_both_say_absent():
    assert _action(cli.WakeOutcome.NOT_FOUND, False, attempt=2) == "bail"


def test_wake_action_never_bails_on_the_first_attempt():
    """One transient must not strand a camera that is really there."""
    assert _action(cli.WakeOutcome.NOT_FOUND, False, attempt=1) == "rewake"


def test_wake_action_does_not_bail_on_a_throttled_scan():
    """None is "couldn't tell", never "absent" — bailing on it would give up on a
    camera whose AP is up while macOS happened to throttle the scan."""
    assert _action(cli.WakeOutcome.NOT_FOUND, None, attempt=3) == "proceed"


def test_wake_action_does_not_bail_when_the_ap_is_up():
    """BLE missing a camera whose AP is serving means BLE had a bad moment, not
    that the camera is absent. Join it."""
    assert _action(cli.WakeOutcome.NOT_FOUND, True, attempt=2) == "proceed"


def test_wake_action_does_not_bail_on_a_generic_wake_failure():
    """FAILED covers Bluetooth off and connect/write errors — none of which tell
    us anything about the camera. Only a completed scan that saw nothing does."""
    assert _action(cli.WakeOutcome.FAILED, False, attempt=3) == "proceed"


# --- _presence_after_wake: the window is sized to what the wake achieved ---


def _window_used(outcome) -> float | None:
    seen: dict[str, float] = {}

    def fake_wait(ssid, timeout=40.0, interval=3.0):
        seen["timeout"] = timeout
        return False

    with patch("bushdump.wifi.wait_for_ssid", side_effect=fake_wait):
        cli._presence_after_wake("CAM8Z8_AABBCC112233", outcome)
    return seen.get("timeout")


def test_presence_after_ack_does_not_scan_at_all():
    assert _window_used(cli.WakeOutcome.ACKED) is None


def test_presence_after_a_received_wake_covers_the_boot_lag():
    """Measured 26.2s and 22.3s on the two models, so a shorter window is
    guaranteed to read "absent" and burn an extra wake."""
    assert _window_used(cli.WakeOutcome.SENT) >= 25.0


def test_presence_after_not_found_only_asks_if_the_ap_is_already_up():
    """Nothing was woken, so there is no boot latency to wait out."""
    assert _window_used(cli.WakeOutcome.NOT_FOUND) < _window_used(cli.WakeOutcome.SENT)


# --- _is_expected_camera_error: which failures print a line, not a traceback ---
#
# Trail cameras are flaky by nature, so the routine failures — asleep, out of
# range, AP down mid-download — must not dump a stack trace at someone standing
# in a paddock. This pins which exception types count as routine.


def test_expected_camera_error_covers_a_failed_join():
    # wifi.join raises this when networksetup cannot find the AP, which is the
    # normal outcome for a camera that is asleep or out of range.
    err = RuntimeError("Failed to join 'CAM8Z8_AABBCC112233' within 45s: Could not find network")
    assert cli._is_expected_camera_error(err)


def test_expected_camera_error_covers_missing_files():
    assert cli._is_expected_camera_error(FileNotFoundError("no such file"))


def test_expected_camera_error_covers_httpx_failures():
    import httpx

    assert cli._is_expected_camera_error(httpx.ConnectError("connection refused"))
    assert cli._is_expected_camera_error(httpx.ReadTimeout("timed out"))


def test_expected_camera_error_excludes_real_bugs():
    # A programming error should still surface with its full traceback.
    assert not cli._is_expected_camera_error(ValueError("bad value"))
    assert not cli._is_expected_camera_error(KeyError("missing"))


def test_expected_camera_error_excludes_bleak_errors():
    # BleakError is not a RuntimeError, so it deliberately does not match here.
    # Bluetooth-unavailable is caught where it can arise instead — _wake_and_report
    # and cmd_sync's scan step — which report it with their own advice.
    from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason

    err = BleakBluetoothNotAvailableError("off", BleakBluetoothNotAvailableReason.POWERED_OFF)
    assert not cli._is_expected_camera_error(err)


def test_sync_stops_at_failed_file_without_advancing_watermark(tmp_path, capsys):
    """A file we could not fetch must stay above the watermark.

    The watermark is date-based and `todo` is oldest-first, so skipping a file
    means the next success advances the watermark past it and no later run ever
    asks for it again. Stopping is the only safe response.
    """
    files = [
        CameraFile(id=1, type=1, date="2026-05-10 13:00:01", size=1024),
        CameraFile(id=2, type=1, date="2026-05-10 13:10:01", size=1024),
        CameraFile(id=3, type=1, date="2026-05-10 13:20:01", size=1024),
    ]

    mock_cam = MagicMock()
    mock_cam.name = "frontgate"
    mock_cam.camera_host = "192.168.8.1:8080"
    mock_cam.ssid = "TestCam_AP"
    mock_cam.output_dir = tmp_path
    mock_cam.expect_ext_power = False

    def _download(f, dest_dir, *, retry=False):
        if f.id == 2:
            raise httpx.ReadError("connection reset by peer")
        return dest_dir / f.name

    client = MagicMock()
    client.wait_until_ready.return_value = True
    client.list_all_files.return_value = files
    client.download.side_effect = _download
    client.stats.return_value = _healthy_stats()
    client.parsed_time_info.return_value = None
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    args = MagicMock()
    args.manual_wifi = False
    args.keep_awake = False
    args.retry = False

    state: dict = {}
    with (
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
        patch("bushdump.config.save_state"),
    ):
        result = cli._sync_one(mock_cam, state, args)

    assert result.downloaded == 1
    assert result.stopped_early
    # Advanced to file 1 only — file 2 is still above it and will be re-listed.
    assert state["frontgate"]["Photo"] == "2026-05-10 13:00:01"
    # File 3 was never attempted; stopping beats reaching past the gap.
    assert client.download.call_count == 2


def test_sync_failure_message_is_one_line_not_a_traceback(tmp_path, capsys):
    file = CameraFile(id=1, type=1, date="2026-05-10 13:00:01", size=1024)

    mock_cam = MagicMock()
    mock_cam.name = "frontgate"
    mock_cam.camera_host = "192.168.8.1:8080"
    mock_cam.ssid = "TestCam_AP"
    mock_cam.output_dir = tmp_path
    mock_cam.expect_ext_power = False

    client = MagicMock()
    client.wait_until_ready.return_value = True
    client.list_all_files.return_value = [file]
    client.download.side_effect = httpx.ReadError("connection reset by peer")
    client.stats.return_value = _healthy_stats()
    client.parsed_time_info.return_value = None
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)

    args = MagicMock()
    args.manual_wifi = False
    args.keep_awake = False
    args.retry = False

    with (
        patch("bushdump.cli._wake_join"),
        patch("bushdump.camera.CameraClient", return_value=client),
        patch("bushdump.config.save_state"),
    ):
        cli._sync_one(mock_cam, {}, args)

    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert file.name in err
    assert "Re-run to resume" in err


# --- _wake_join short-circuit ---


def _ap_camera() -> MagicMock:
    cam = MagicMock()
    cam.name = "frontgate"
    cam.ssid = "CAM8Z8_AABBCCDDEEFF"
    cam.password = "1234567890"
    cam.ble_address = "D1AE237D-FF38-DF02-2E8D-A1A9813A0CD6"
    return cam


def test_wake_join_skips_wake_when_already_on_that_ssid(capsys):
    """An SSID match identifies the camera: SSIDs carry the device's WiFi MAC."""
    cam = _ap_camera()
    with (
        patch("bushdump.wifi.current_ssid", return_value=cam.ssid),
        patch("bushdump.wifi.join") as join,
        patch("bushdump.cli._wake_and_report") as wake,
    ):
        cli._wake_join(cam)

    join.assert_not_called()
    wake.assert_not_called()
    assert cli._joined_ap, "still sitting on the camera's AP — sync must still say so"
    assert "Already on" in capsys.readouterr().out


def test_wake_join_proceeds_when_ssid_is_redacted(capsys):
    """macOS 26 hands back a placeholder, which must not match and must not skip."""
    cam = _ap_camera()
    with (
        patch("bushdump.wifi.current_ssid", return_value="<redacted>"),
        patch("bushdump.wifi.join") as join,
        patch("bushdump.wifi.wait_for_ssid", return_value=True),
        patch("bushdump.cli._wake_and_report", return_value=cli.WakeOutcome.ACKED),
    ):
        cli._wake_join(cam)

    join.assert_called_once()


def test_wake_join_proceeds_when_on_a_different_ssid():
    cam = _ap_camera()
    with (
        patch("bushdump.wifi.current_ssid", return_value="CAM8Z8_112233445566"),
        patch("bushdump.wifi.join") as join,
        patch("bushdump.wifi.wait_for_ssid", return_value=True),
        patch("bushdump.cli._wake_and_report", return_value=cli.WakeOutcome.ACKED),
    ):
        cli._wake_join(cam)

    join.assert_called_once()
