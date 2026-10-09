from unittest.mock import patch

from bushdump import helper
from bushdump.cli import _format_candidate_row, _is_camera_ble, _mark
from bushdump.wifi import (
    REDACTED_HINT,
    Identity,
    Observed,
    _scan_names,
    _seen_from_scan,
    diagnose_scan,
    identify,
    is_likely_camera_ssid,
    join,
    join_succeeded,
    parse_current_ssid,
    parse_profile_id,
    parse_wifi_interface,
    profile_to_record,
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


def test_wait_for_ssid_stops_as_soon_as_the_caller_says_so():
    polls = iter([None, None, None, None])
    stops = iter([False, True])
    with (
        patch("bushdump.wifi.time", _FakeClock()),
        patch("bushdump.wifi.ssid_present", side_effect=lambda _ssid: next(polls)) as present,
    ):
        wait_for_ssid("CAM8Z8_AABBCC112233", timeout=40.0, stop=lambda: next(stops))
    assert present.call_count == 1


def test_join_stops_retrying_when_the_caller_says_so():
    # networksetup "fails" forever here; without the stop this would raise.
    failed = type("R", (), {"returncode": 0, "stdout": "Could not find network X.", "stderr": ""})
    stops = iter([False, True])
    with (
        patch("bushdump.wifi.time", _FakeClock()),
        patch("bushdump.wifi.subprocess.run", return_value=failed) as run,
    ):
        joined = join("CAM8Z8_AABBCC112233", "pw", interface="en0", stop=lambda: next(stops))
    assert joined is False
    assert run.call_count == 1


# --- identifying the network we are joined to ---
#
# Three sources, best first: `networksetup` (names the network on macOS 15
# only), the helper app (names it on 26+ once it has Location permission), and
# the ProfileID recorded by `bushdump identify` (names nothing, but is constant
# per saved network).

CAM_SSID = "CAM8Z8_A4C10896B3B0"
PROFILE = "9c72eea6cce0c16a6a4db4671ea35018696b4796e7506869b730afe6664b5581"
OTHER_PROFILE = "1af4c2b6cc2ecebf284ef4c8108834fcd0daa4caa1f74a486f9b1c8b545f5222"

SCUTIL_JOINED = f"""\
<dictionary> {{
  BSSID : <data> 0x020000000000
  CHANNEL : 36
  ProfileID : {PROFILE}
  SSID : <data> 0x00
  SSID_STR : 
}}
"""


def _helper(ssid: str | None) -> helper.Reply:
    return helper.Reply(authorized=True, ssid=ssid)


def test_parse_profile_id_reads_the_joined_networks_token():
    assert parse_profile_id(SCUTIL_JOINED) == PROFILE


def test_parse_profile_id_none_when_not_joined():
    assert parse_profile_id("<dictionary> {\n  Power Status : TRUE\n}\n") is None
    assert parse_profile_id("  No such key\n") is None


def test_identify_matches_when_networksetup_names_the_camera():
    assert identify(CAM_SSID, None, Observed(networksetup_ssid=CAM_SSID)) is Identity.MATCH


def test_identify_does_not_trust_a_networksetup_name_that_differs():
    # macOS 26 hands back a placeholder there, and 27 nothing at all, so a name
    # that isn't the camera's says nothing about which network this is.
    seen = Observed(networksetup_ssid="<redacted>")
    assert identify(CAM_SSID, None, seen) is Identity.UNKNOWN


def test_identify_matches_when_the_helper_names_the_camera():
    assert identify(CAM_SSID, None, Observed(helper=_helper(CAM_SSID))) is Identity.MATCH


def test_identify_mismatch_when_the_helper_names_another_network():
    assert identify(CAM_SSID, None, Observed(helper=_helper("G"))) is Identity.MISMATCH


def test_identify_mismatch_when_the_helper_says_we_are_on_no_network():
    assert identify(CAM_SSID, None, Observed(helper=_helper(None))) is Identity.MISMATCH


def test_identify_helper_outranks_a_recorded_profile():
    seen = Observed(helper=_helper("G"), profile_id=PROFILE)
    assert identify(CAM_SSID, PROFILE, seen) is Identity.MISMATCH


def test_identify_falls_back_to_the_recorded_profile_without_a_helper():
    assert identify(CAM_SSID, PROFILE, Observed(profile_id=PROFILE)) is Identity.MATCH
    assert identify(CAM_SSID, PROFILE, Observed(profile_id=OTHER_PROFILE)) is Identity.MISMATCH


def test_identify_unknown_without_a_helper_or_a_recorded_profile():
    assert identify(CAM_SSID, None, Observed(profile_id=PROFILE)) is Identity.UNKNOWN


def test_identify_unknown_when_the_recorded_profile_cannot_be_compared():
    # Not on WiFi at all reads as "no ProfileID", which is not proof of anything.
    assert identify(CAM_SSID, PROFILE, Observed()) is Identity.UNKNOWN


# --- listing: names come from the helper once macOS redacts them ---


def test_scan_names_keeps_names_macos_gave_us_directly():
    assert _scan_names(["G"], seen=3, ask_helper=lambda: ["other"]) == ["G"]


def test_scan_names_asks_the_helper_when_networks_were_seen_but_not_named():
    assert _scan_names([], seen=3, ask_helper=lambda: ["G", "CAM8Z8_AABBCC112233"]) == [
        "G",
        "CAM8Z8_AABBCC112233",
    ]


def test_scan_names_empty_when_the_helper_cannot_tell_either():
    assert _scan_names([], seen=3, ask_helper=lambda: None) == []


def test_scan_names_does_not_bother_the_helper_when_nothing_was_seen():
    def never() -> list[str]:
        raise AssertionError("helper asked about an empty scan")

    assert _scan_names([], seen=0, ask_helper=never) == []
    assert _scan_names([], seen=None, ask_helper=never) == []


# --- recording a ProfileID automatically: only on the helper's say-so ---


def test_profile_to_record_when_the_helper_confirms_the_camera():
    seen = Observed(helper=_helper(CAM_SSID), profile_id=PROFILE)
    assert profile_to_record(CAM_SSID, None, seen) == PROFILE


def test_profile_to_record_replaces_a_stale_one():
    seen = Observed(helper=_helper(CAM_SSID), profile_id=PROFILE)
    assert profile_to_record(CAM_SSID, OTHER_PROFILE, seen) == PROFILE


def test_profile_to_record_nothing_when_already_recorded():
    seen = Observed(helper=_helper(CAM_SSID), profile_id=PROFILE)
    assert profile_to_record(CAM_SSID, PROFILE, seen) is None


def test_profile_to_record_nothing_when_the_helper_names_another_network():
    seen = Observed(helper=_helper("G"), profile_id=PROFILE)
    assert profile_to_record(CAM_SSID, None, seen) is None


def test_profile_to_record_nothing_without_the_helper():
    # networksetup naming the camera is true on macOS 15, but the point of the
    # automatic path is that it cannot be wrong, so only the helper counts.
    seen = Observed(networksetup_ssid=CAM_SSID, profile_id=PROFILE)
    assert profile_to_record(CAM_SSID, None, seen) is None


def test_profile_to_record_nothing_when_macos_offers_no_profile_id():
    assert profile_to_record(CAM_SSID, None, Observed(helper=_helper(CAM_SSID))) is None
