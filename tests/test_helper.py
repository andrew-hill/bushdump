from bushdump import helper

# What the BushDumpWiFiHelper app hands back is the only thing here worth testing:
# launching it needs LaunchServices and a Location grant, neither of which a
# test run has.


def test_parse_reply_reads_the_current_network():
    reply = helper.parse_reply(
        '{"authorized": true, "ssid": "CAM8Z8_A4C10896B3B0", "bssid": "a4:c1:08:96:b3:b0",'
        ' "networks": []}'
    )
    assert reply == helper.Reply(
        authorized=True, ssid="CAM8Z8_A4C10896B3B0", bssid="a4:c1:08:96:b3:b0", networks=()
    )


def test_parse_reply_authorized_but_not_on_any_network():
    reply = helper.parse_reply('{"authorized": true, "ssid": null, "bssid": null}')
    assert reply is not None
    assert reply.authorized and reply.ssid is None


def test_parse_reply_keeps_scanned_names():
    reply = helper.parse_reply('{"authorized": true, "networks": ["G", "CAM8Z8_A4C10896B3B0"]}')
    assert reply is not None
    assert reply.networks == ("G", "CAM8Z8_A4C10896B3B0")


def test_parse_reply_none_for_anything_that_is_not_a_reply():
    assert helper.parse_reply("") is None
    assert helper.parse_reply("not json") is None
    assert helper.parse_reply('["authorized"]') is None
    assert helper.parse_reply('{"ssid": "G"}') is None


IDENTITIES = """\
  1) 87A23B09A3CDA33EB823E06FB9D714F87EDA3322 "Apple Development: Someone (ABCDE12345)"
  2) 0123456789ABCDEF0123456789ABCDEF01234567 "Developer ID Application: Someone (ABCDE12345)"
     2 valid identities found
"""


def test_parse_signing_identity_takes_the_first_valid_one():
    assert helper.parse_signing_identity(IDENTITIES) == "87A23B09A3CDA33EB823E06FB9D714F87EDA3322"


def test_parse_signing_identity_none_when_the_keychain_has_none():
    assert helper.parse_signing_identity("     0 valid identities found\n") is None
