from bushdump.cli import _is_camera_ble
from bushdump.wifi import is_likely_camera_ssid, parse_wifi_interface, rank_ssids

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
    ssids = ["HomeNet", "CAM8Z8_A4C13896B3B0", "Cafe", "CAM8Z8_385CFB2540D4"]
    ranked = rank_ssids(ssids)
    assert ranked[:2] == ["CAM8Z8_385CFB2540D4", "CAM8Z8_A4C13896B3B0"]


# --- is_likely_camera_ssid ---


def test_is_likely_camera_ssid_linkiing():
    assert is_likely_camera_ssid("CAM8Z8_385CFB2540D4")


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
