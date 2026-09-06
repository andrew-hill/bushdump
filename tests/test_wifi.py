from unittest.mock import patch

from bushdump.cli import _format_candidate_row, _is_camera_ble, _mark
from bushdump.wifi import (
    REDACTED_HINT,
    _seen_from_scan,
    diagnose_scan,
    is_likely_camera_ssid,
    join_succeeded,
    parse_current_ssid,
    parse_wifi_interface,
    rank_ssids,
    wait_for_ssid,
)

SAMPLE = """\
Hardware Port: Ethernet
Device: en1
Ethernet Address: aa:bb:cc:dd:ee:ff

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: 11:22:33:44:55:66

Hardware Port: Bluetooth PAN
Device: en2
Ethernet Address: 77:88:99:aa:bb:cc
"""


def test_parse_wifi_interface_finds_wifi_device():
    assert parse_wifi_interface(SAMPLE) == "en0"


def test_parse_wifi_interface_none_when_absent():
    assert parse_wifi_interface("Hardware Port: Ethernet\nDevice: en1\n") is None


def test_rank_ssids_dedupes_and_surfaces_cameras_first():
    ssids = ["HomeNet", "Trail Cam Pro 5678", "Cafe", "HomeNet", "Trail Cam Pro 1234"]
    ranked = rank_ssids(ssids)
    # Trail cams first (alpha within group), then the rest (alpha), deduped.
    assert ranked == ["Trail Cam Pro 1234", "Trail Cam Pro 5678", "Cafe", "HomeNet"]


def test_rank_ssids_cam8z8_surfaces_first():
    ssids = ["HomeNet", "CAM8Z8_AABBCC445566", "Cafe", "CAM8Z8_AABBCC112233"]
    ranked = rank_ssids(ssids)
    assert ranked[:2] == ["CAM8Z8_AABBCC112233", "CAM8Z8_AABBCC445566"]


# --- is_likely_camera_ssid ---


def test_is_likely_camera_ssid_linkiing():
    assert is_likely_camera_ssid("CAM8Z8_AABBCC112233")


def test_is_likely_camera_ssid_legacy():
    assert is_likely_camera_ssid("Trail Cam Pro ABCD")


def test_is_likely_camera_ssid_negative():
    assert not is_likely_camera_ssid("MyHomeNetwork")
    assert not is_likely_camera_ssid("NETGEAR42")


# --- _is_camera_ble ---


def test_is_camera_ble_linkiing_name():
    assert _is_camera_ble("CAM8Z8_backyard_G_E6PMB")


def test_is_camera_ble_brand_names():
    assert _is_camera_ble("GardeProFront")
    assert _is_camera_ble("dsoon_cam")
    assert _is_camera_ble("campark_trail")


def test_is_camera_ble_negative():
    assert not _is_camera_ble("AirPods")
    assert not _is_camera_ble("Bose QuietComfort 45")


def test_is_camera_ble_none():
    assert not _is_camera_ble(None)


def test_mark_is_plain_symbol():
    assert _mark(True) == "◆"
    assert _mark(False) == "◇"


def test_candidate_row_highlights_full_row_on_tty():
    row = "  ◆  CAM8Z8_backyard   abc"
    assert _format_candidate_row(row, True, tty=True) == f"\033[1;33m{row}\033[0m"


def test_candidate_row_stays_plain_when_not_tty_or_not_candidate():
    row = "  ◆  CAM8Z8_backyard   abc"
    assert _format_candidate_row(row, True, tty=False) == row
    assert _format_candidate_row(row, False, tty=True) == row


# --- macOS 26 SSID redaction ------------------------------------------------
#
# Tahoe hides SSIDs from processes without Apple's `wifi-info` entitlement, so a
# healthy scan returns networks with no names. These cover telling that apart
# from the framework being absent and from nothing being in range.


def test_diagnose_scan_ok_when_names_came_back():
    assert diagnose_scan(framework=True, seen=12, named=12) is None


def test_diagnose_scan_reports_missing_framework():
    problem = diagnose_scan(framework=False, seen=0, named=0)
    assert problem is not None
    assert "CoreWLAN" in problem


def test_diagnose_scan_reports_redaction_when_networks_seen_but_unnamed():
    problem = diagnose_scan(framework=True, seen=46, named=0)
    assert problem == REDACTED_HINT


def test_diagnose_scan_reports_empty_air_when_nothing_seen():
    problem = diagnose_scan(framework=True, seen=0, named=0)
    assert problem is not None
    assert problem != REDACTED_HINT


def test_diagnose_scan_prefers_framework_problem_over_redaction():
    """A missing framework reports zero networks too — don't call that redaction."""
    problem = diagnose_scan(framework=False, seen=0, named=0)
    assert problem != REDACTED_HINT


def test_diagnose_scan_distinguishes_wifi_off_from_empty_air():
    """`seen=None` means the scan never ran (WiFi off, no hardware, scan threw).

    Reporting that as "nothing in range" is the one answer that's definitely
    wrong — the whole point of this function is not conflating the cases.
    """
    problem = diagnose_scan(framework=True, seen=None, named=0)
    empty_air = diagnose_scan(framework=True, seen=0, named=0)
    assert problem is not None
    assert problem != empty_air
    assert problem != REDACTED_HINT


# --- _seen_from_scan: the three-state `seen` diagnose_scan depends on ---
#
# diagnose_scan has always handled `seen is None`, but nothing ever produced it:
# a switched-off radio still returns a live CWInterface, so an empty scan came
# back as 0 and `bushdump wifi` answered "No WiFi networks in range." with WiFi
# off. These pin the mapping that feeds it.


def test_seen_from_scan_radio_off_is_unknown_not_empty():
    assert _seen_from_scan(powered=False, scan_failed=False, cached=0) is None


def test_seen_from_scan_radio_off_ignores_a_stale_cache():
    # Results may linger from before the radio went down; we still never scanned.
    assert _seen_from_scan(powered=False, scan_failed=False, cached=12) is None


def test_seen_from_scan_empty_air_is_zero_not_unknown():
    assert _seen_from_scan(powered=True, scan_failed=False, cached=0) == 0


def test_seen_from_scan_failed_scan_with_nothing_cached_is_unknown():
    assert _seen_from_scan(powered=True, scan_failed=True, cached=0) is None


def test_seen_from_scan_failed_scan_still_counts_a_populated_cache():
    # A throttled active scan is fine as long as the OS cache has something —
    # that cache is what makes redaction detectable at all.
    assert _seen_from_scan(powered=True, scan_failed=True, cached=46) == 46


def test_seen_from_scan_feeds_diagnose_scan_the_wifi_off_message():
    seen = _seen_from_scan(powered=False, scan_failed=False, cached=0)
    problem = diagnose_scan(framework=True, seen=seen, named=0)
    assert problem is not None
    assert "switched off" in problem
    assert "in range" not in problem


# --- join_succeeded: networksetup reports failure on stdout and exits 0 ---


def test_join_succeeded_on_clean_silent_exit():
    assert join_succeeded(0, "")


def test_join_succeeded_false_when_network_missing_despite_exit_zero():
    # The exact shape seen when the camera AP is down — exit 0, error on stdout.
    assert not join_succeeded(0, "Could not find network CAM8Z8_AABBCC112233.\n")


def test_join_succeeded_false_on_nonzero_exit():
    assert not join_succeeded(1, "")


def test_join_succeeded_ignores_trailing_whitespace():
    assert join_succeeded(0, "   \n")


# --- wait_for_ssid: unknowns are the common case, not the verdict ---
#
# Measured on macOS 26 at the real 3s interval: a genuine scan takes ~7s and
# answers, then the next ~3 polls come back `Resource busy`. So a window that
# reaches a definite answer will almost always contain unknowns too. Treating
# any unknown as the verdict threw those answers away.


class _FakeClock:
    """Stands in for the `time` module so polling loops run instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _wait(samples: list[bool | None], interval: float = 3.0) -> bool | None:
    """Run wait_for_ssid over exactly `samples` polls, with the clock faked out."""
    with (
        patch("bushdump.wifi.time", _FakeClock()),
        patch("bushdump.wifi.ssid_present", side_effect=samples),
    ):
        return wait_for_ssid(
            "CAM8Z8_AABBCC112233",
            timeout=interval * (len(samples) - 1),
            interval=interval,
        )


def test_wait_for_ssid_trusts_a_definite_absence_amid_throttled_scans():
    # The real observed shape: answer, three throttled, answer, three throttled.
    samples = [False, None, None, None, False, None, None, None, False]
    assert _wait(samples) is False


def test_wait_for_ssid_reports_unknown_only_when_nothing_ever_answered():
    assert _wait([None] * 12) is None


def test_wait_for_ssid_returns_true_as_soon_as_the_ap_appears():
    # Must not keep polling once it knows — the wake is done.
    assert _wait([None, None, True]) is True


def test_wait_for_ssid_absence_survives_a_trailing_run_of_unknowns():
    assert _wait([None, False, None, None, None, None]) is False


# --- parse_current_ssid ---


def test_parse_current_ssid_reads_the_name():
    assert parse_current_ssid("Current Wi-Fi Network: HomeNet\n") == "HomeNet"


def test_parse_current_ssid_none_when_not_associated():
    assert parse_current_ssid("You are not associated with an AirPort network.\n") is None


def test_parse_current_ssid_keeps_spaces_in_the_name():
    assert parse_current_ssid("Current Wi-Fi Network: The Frog Net\n") == "The Frog Net"


def test_parse_current_ssid_redaction_does_not_match_a_real_ssid():
    """Under macOS 26 the name comes back as a placeholder, not the SSID.

    Callers compare against a configured SSID, so a placeholder simply fails to
    match and they fall through to the full wake+join. Nothing has to detect
    redaction for that to be safe.
    """
    redacted = parse_current_ssid("Current Wi-Fi Network: <redacted>\n")
    assert redacted != "CAM8Z8_AABBCCDDEEFF"
