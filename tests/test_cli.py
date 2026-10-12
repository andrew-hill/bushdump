import argparse
import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from bushdump import cli, config, helper, wifi
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
    args.power_off = False

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
    args.power_off = False
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

    def fake_wait(ssid, timeout=40.0, interval=3.0, stop=None):
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
    args.power_off = False
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
    args.power_off = False
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


# --- sleep ---


def _sleep(argv, *, identity=wifi.Identity.MATCH, current=None, answers=True):
    """Run `sleep`; return (exit code, client mock)."""
    client = MagicMock()
    client.wait_until_ready.return_value = answers
    client.__enter__ = lambda s: client
    client.__exit__ = MagicMock(return_value=False)
    args = cli.build_parser().parse_args(["sleep", *argv])
    with (
        patch("bushdump.cli._resolve_camera", return_value=_cam()),
        patch("bushdump.cli._camera_identity", return_value=identity),
        patch("bushdump.cli._current_camera", return_value=current),
        patch("bushdump.config.load_config"),
        patch("bushdump.camera.CameraClient", return_value=client),
    ):
        return args.func(args), client


def test_sleep_powers_off_the_named_camera_we_are_on():
    code, client = _sleep(["gate"])
    assert code == 0
    client.power_off.assert_called_once()


def test_sleep_refuses_when_on_another_network():
    code, client = _sleep(["gate"], identity=wifi.Identity.MISMATCH)
    assert code == 1
    client.power_off.assert_not_called()


def test_sleep_without_a_name_takes_the_camera_we_are_on():
    code, client = _sleep([], current=_cam())
    assert code == 0
    client.power_off.assert_called_once()


def test_sleep_without_a_name_needs_a_recognised_network():
    code, client = _sleep([])
    assert code == 1
    client.power_off.assert_not_called()


def test_sleep_is_a_no_op_when_no_camera_answers(capsys):
    code, client = _sleep(["gate"], answers=False)
    assert code == 0
    client.power_off.assert_not_called()
    assert "already off" in capsys.readouterr().out


# --- _wake_join short-circuit ---


def _ap_camera() -> MagicMock:
    cam = MagicMock()
    cam.name = "frontgate"
    cam.ssid = "CAM8Z8_AABBCCDDEEFF"
    cam.password = "1234567890"
    cam.ble_address = "D1AE237D-FF38-DF02-2E8D-A1A9813A0CD6"
    cam.profile_id = None
    return cam


def test_wake_join_skips_wake_when_already_on_that_ssid(capsys):
    """An SSID match identifies the camera: SSIDs carry the device's WiFi MAC."""
    cam = _ap_camera()
    with (
        patch("bushdump.wifi.observe", return_value=wifi.Observed(networksetup_ssid=cam.ssid)),
        patch("bushdump.cli._camera_answers", return_value=True),
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
        patch("bushdump.wifi.observe", return_value=wifi.Observed(networksetup_ssid="<redacted>")),
        patch("bushdump.helper.current", return_value=None),
        patch("bushdump.wifi.join") as join,
        patch("bushdump.wifi.wait_for_ssid", return_value=True),
        patch("bushdump.cli._wake_and_report", return_value=cli.WakeOutcome.ACKED),
    ):
        cli._wake_join(cam)

    join.assert_called_once()


def test_wake_join_proceeds_when_on_a_different_ssid():
    cam = _ap_camera()
    with (
        patch(
            "bushdump.wifi.observe",
            return_value=wifi.Observed(networksetup_ssid="CAM8Z8_112233445566"),
        ),
        patch("bushdump.helper.current", return_value=None),
        patch("bushdump.wifi.join") as join,
        patch("bushdump.wifi.wait_for_ssid", return_value=True),
        patch("bushdump.cli._wake_and_report", return_value=cli.WakeOutcome.ACKED),
    ):
        cli._wake_join(cam)

    join.assert_called_once()


# --- skipping the wake+join when we are already on the camera's network -----
#
# SSIDs are unreadable on macOS 26, so "already there" is decided by the
# gateway MAC instead (`wifi.identify`). These pin down what each verdict is
# allowed to skip.


def _cam(ble_address: str | None = "AAAA-BBBB") -> config.Camera:
    return config.Camera(
        name="gate",
        ssid="CAM8Z8_A4C10896B3B0",
        password="pw",
        camera_host="192.168.8.1:8080",
        output_dir=Path("/nonexistent"),
        ble_address=ble_address,
    )


def _wake_join_with(identities, *, enter=False, wake=None, answers=True, **kwargs):
    """Run _wake_join with every radio faked; return the (wake, wait, join) mocks."""
    wake_mock = MagicMock(side_effect=wake or (lambda *a, **k: cli.WakeOutcome.SENT))
    with (
        patch("bushdump.cli._camera_identity", side_effect=identities),
        patch("bushdump.cli._camera_answers", return_value=answers),
        patch("bushdump.cli._enter_pressed", return_value=enter),
        patch("bushdump.cli._wake_and_report", wake_mock),
        patch("bushdump.helper.current", return_value=None),
        patch("bushdump.wifi.observe", return_value=wifi.Observed()),
        patch("bushdump.wifi.wait_for_ssid", return_value=True) as wait,
        patch("bushdump.wifi.join", return_value=True) as join,
    ):
        cli._wake_join(_cam(), **kwargs)
    return wake_mock, wait, join


def _calls_stop(*_args, stop, **_kwargs):
    stop()
    return cli.WakeOutcome.ABANDONED


def test_wake_join_does_nothing_when_the_gateway_is_already_this_camera():
    wake, _wait, join = _wake_join_with([wifi.Identity.MATCH])
    wake.assert_not_called()
    join.assert_not_called()


def test_a_match_with_no_camera_answering_gets_the_full_wake_and_join(capsys):
    """The network outlived the AP, so the sources that matched are not asked again."""
    wake, wait, join = _wake_join_with([wifi.Identity.MATCH] * 9, answers=False)
    assert wake.call_count == 1
    assert wait.called
    assert join.call_count == 1
    assert "not answering" in capsys.readouterr().out


def test_wake_join_stops_once_macos_auto_joins_mid_wake():
    wake, wait, join = _wake_join_with(
        [wifi.Identity.UNKNOWN, wifi.Identity.MATCH], wake=_calls_stop
    )
    assert wake.call_count == 1
    wait.assert_not_called()
    join.assert_not_called()


def test_enter_during_the_wake_goes_straight_to_the_join():
    wake, wait, join = _wake_join_with([wifi.Identity.UNKNOWN] * 9, enter=True, wake=_calls_stop)
    assert wake.call_count == 1
    wait.assert_not_called()
    assert join.call_count == 1


def test_joined_flag_skips_the_wake_when_the_gateway_matches():
    wake, _wait, join = _wake_join_with([wifi.Identity.MATCH], joined=True)
    wake.assert_not_called()
    join.assert_not_called()


def test_joined_flag_refuses_a_network_known_to_be_another_camera():
    with pytest.raises(cli.CameraUnreachable):
        _wake_join_with([wifi.Identity.MISMATCH], joined=True)


def test_joined_flag_takes_your_word_when_it_cannot_tell():
    wake, _wait, join = _wake_join_with([wifi.Identity.UNKNOWN], joined=True)
    wake.assert_not_called()
    join.assert_not_called()


def test_joined_flag_will_not_take_your_word_for_a_prune():
    with pytest.raises(cli.CameraUnreachable):
        _wake_join_with([wifi.Identity.UNKNOWN], joined=True, strict=True)


def test_one_enter_skips_one_wait_only():
    with (
        patch("bushdump.cli._camera_identity", return_value=wifi.Identity.UNKNOWN),
        patch("bushdump.cli._enter_pressed", side_effect=[True, False]),
    ):
        watch = cli._WaitWatch(_cam())
        assert watch() is True
        assert watch.take_skip() is True
        assert watch() is False


def test_prune_passes_joined_as_strict():
    args = cli.build_parser().parse_args(["prune", "gate", "--joined"])
    with (
        patch("bushdump.cli._resolve_camera", return_value=_cam()),
        patch(
            "bushdump.config.load_backups", return_value={"gate": {"Photo": "2026-01-01 00:00:00"}}
        ),
        patch("bushdump.cli._wake_join", side_effect=cli.CameraUnreachable("stop here")) as wj,
    ):
        assert args.func(args) == 1
    wj.assert_called_once_with(_cam(), joined=True, strict=True)


def test_sync_joined_needs_a_camera_name(capsys):
    args = cli.build_parser().parse_args(["sync", "--joined"])
    assert args.func(args) == 1
    assert "--joined" in capsys.readouterr().err


# --- recognising a camera's network, and saying how to speed things up ------

PROFILE = "9c72eea6cce0c16a6a4db4671ea35018696b4796e7506869b730afe6664b5581"


def _slow_path_output(capsys, *, profile_id, helper_reply) -> str:
    cam = dataclasses.replace(_cam(), profile_id=profile_id)
    with (
        patch("bushdump.cli._camera_identity", return_value=wifi.Identity.UNKNOWN),
        patch("bushdump.cli._enter_pressed", return_value=False),
        patch("bushdump.cli._wake_and_report", return_value=cli.WakeOutcome.ACKED),
        patch("bushdump.helper.current", return_value=helper_reply),
        patch("bushdump.wifi.observe", return_value=wifi.Observed()),
        patch("bushdump.wifi.wait_for_ssid", return_value=True),
        patch("bushdump.wifi.join", return_value=True),
    ):
        cli._wake_join(cam)
    return capsys.readouterr().out


def test_slow_path_says_how_to_speed_up_when_nothing_can_recognise_the_network(capsys):
    out = _slow_path_output(capsys, profile_id=None, helper_reply=None)
    assert "bushdump helper install" in out
    assert "bushdump identify gate" in out


def test_slow_path_is_quiet_once_a_profile_id_is_recorded(capsys):
    out = _slow_path_output(capsys, profile_id=PROFILE, helper_reply=None)
    assert "bushdump helper install" not in out


def test_slow_path_is_quiet_when_the_helper_is_working(capsys):
    reply = helper.Reply(authorized=True, ssid="G")
    out = _slow_path_output(capsys, profile_id=None, helper_reply=reply)
    assert "bushdump helper install" not in out


def test_prove_joins_by_ssid_even_when_the_network_is_already_recognised():
    wake, _wait, join = _wake_join_with([wifi.Identity.MATCH] * 9, prove=True)
    assert wake.call_count == 1
    assert join.call_count == 1


def _identify(observed, *, joined=True, profile=PROFILE):
    """Run `identify gate`; return (exit code, set_camera_key mock, wake_join mock)."""
    args = cli.build_parser().parse_args(["identify", "gate"])
    with (
        patch("bushdump.cli._resolve_camera", return_value=_cam()),
        patch("bushdump.wifi.observe", return_value=observed),
        patch("bushdump.cli._wake_join", return_value=joined) as wake_join,
        patch("bushdump.wifi.current_profile_id", return_value=profile),
        patch("bushdump.cli.time.sleep"),
        patch("bushdump.config.set_camera_key") as set_key,
    ):
        return args.func(args), set_key, wake_join


def test_identify_records_without_joining_when_the_helper_names_the_camera():
    on_camera = wifi.Observed(helper=helper.Reply(authorized=True, ssid=_cam().ssid))
    code, set_key, wake_join = _identify(on_camera)
    assert code == 0
    wake_join.assert_not_called()
    set_key.assert_called_once_with("gate", "profile_id", PROFILE)


def test_identify_joins_by_ssid_when_nothing_names_the_network():
    code, set_key, wake_join = _identify(wifi.Observed(profile_id=PROFILE))
    assert code == 0
    wake_join.assert_called_once_with(_cam(), prove=True)
    set_key.assert_called_once_with("gate", "profile_id", PROFILE)


def test_identify_records_nothing_unless_it_joined_by_ssid(capsys):
    code, set_key, _ = _identify(wifi.Observed(), joined=False)
    assert code == 1
    set_key.assert_not_called()
    assert "nothing recorded" in capsys.readouterr().err.lower()


def test_identify_records_nothing_when_macos_offers_no_profile_id(capsys):
    code, set_key, _ = _identify(wifi.Observed(), profile=None)
    assert code == 1
    set_key.assert_not_called()
    assert "profileid" in capsys.readouterr().err.lower()


def test_every_command_is_listed_in_the_top_level_help():
    # The grouped list in --help is written by hand, so a new subcommand is
    # invisible there until someone remembers to add it.
    parser = cli.build_parser()
    listed = parser.format_help()
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    commands = {a.dest for a in subparsers._choices_actions}
    missing = sorted(c for c in commands if f"\n    {c}" not in listed)
    assert not missing, f"not in --help: {missing}"


# --- the ProfileID fallback primes itself whenever the helper confirms ------


def _remember(observed, *, recorded=None):
    cam = dataclasses.replace(_cam(), profile_id=recorded)
    with (
        patch("bushdump.wifi.observe", return_value=observed),
        patch("bushdump.config.set_camera_key") as set_key,
    ):
        cli._remember_profile(cam)
    return cam, set_key


def test_remember_profile_records_what_the_helper_confirmed(capsys):
    on_camera = wifi.Observed(
        helper=helper.Reply(authorized=True, ssid=_cam().ssid), profile_id=PROFILE
    )
    cam, set_key = _remember(on_camera)
    set_key.assert_called_once_with("gate", "profile_id", PROFILE)
    assert cam.profile_id == PROFILE
    assert "ProfileID" in capsys.readouterr().out


def test_remember_profile_does_nothing_without_the_helpers_confirmation():
    _cam_after, set_key = _remember(wifi.Observed(profile_id=PROFILE))
    set_key.assert_not_called()


def test_remember_profile_never_breaks_the_command_it_rides_on():
    on_camera = wifi.Observed(
        helper=helper.Reply(authorized=True, ssid=_cam().ssid), profile_id=PROFILE
    )
    cam = _cam()
    with (
        patch("bushdump.wifi.observe", return_value=on_camera),
        patch("bushdump.config.set_camera_key", side_effect=ValueError("no such section")),
    ):
        cli._remember_profile(cam)
    assert cam.profile_id is None


def test_wake_join_remembers_the_profile_on_both_ways_onto_the_network():
    with patch("bushdump.cli._remember_profile") as remember:
        _wake_join_with([wifi.Identity.MATCH])
        _wake_join_with([wifi.Identity.UNKNOWN] * 9)
    assert remember.call_count == 2


# --- sync with no name: start with the camera whose network we are on -------


def _two_cameras() -> dict[str, config.Camera]:
    east = dataclasses.replace(_cam("EAST-ADDR"), name="east", ssid="CAM8Z8_AAAAAAAAAAAA")
    norw = dataclasses.replace(_cam("NORW-ADDR"), name="norw", ssid="CAM8Z8_BBBBBBBBBBBB")
    return {"east": east, "norw": norw}


def test_current_camera_is_the_one_whose_network_we_are_on():
    cams = _two_cameras()
    on_norw = wifi.Observed(helper=helper.Reply(authorized=True, ssid=cams["norw"].ssid))
    with patch("bushdump.wifi.observe", return_value=on_norw) as observe:
        assert cli._current_camera(cams.values()) is cams["norw"]
    observe.assert_called_once()


def test_current_camera_none_on_some_other_network():
    on_home = wifi.Observed(helper=helper.Reply(authorized=True, ssid="G"))
    with patch("bushdump.wifi.observe", return_value=on_home):
        assert cli._current_camera(_two_cameras().values()) is None


def _sync_order(current, found_addresses):
    """Drain _cameras_in_sync_order; return (names in order, events in order)."""
    cams = _two_cameras()
    events: list[str] = []

    async def discover(timeout):
        events.append("ble-scan")
        return [(addr, None) for addr in found_addresses]

    names = []
    with (
        patch("bushdump.cli._current_camera", return_value=cams.get(current)),
        patch("bushdump.ble.discover", discover),
    ):
        for cam in cli._cameras_in_sync_order(cams, scan_timeout=1.0):
            names.append(cam.name)
            events.append(cam.name)
    return names, events


def test_sync_starts_with_the_current_camera_before_any_ble_scan():
    names, events = _sync_order("norw", ["EAST-ADDR", "NORW-ADDR"])
    assert names == ["norw", "east"]
    assert events == ["norw", "ble-scan", "east"]


def test_sync_still_syncs_the_current_camera_when_ble_finds_nothing():
    names, _ = _sync_order("norw", [])
    assert names == ["norw"]


def test_sync_scans_first_when_we_are_on_no_cameras_network():
    names, events = _sync_order(None, ["EAST-ADDR", "NORW-ADDR"])
    assert names == ["east", "norw"]
    assert events[0] == "ble-scan"


def test_sync_has_nothing_to_do_when_no_camera_is_current_or_nearby():
    with pytest.raises(cli.NoCamerasToSync):
        _sync_order(None, [])
