from bushdump.wifi import parse_wifi_interface, rank_ssids

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
