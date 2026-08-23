from bushdump.cli import _format_candidate_row, _is_camera_ble, _mark
from bushdump.wifi import (
    REDACTED_HINT,
    diagnose_scan,
    is_likely_camera_ssid,
    parse_wifi_interface,
    rank_ssids,
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
