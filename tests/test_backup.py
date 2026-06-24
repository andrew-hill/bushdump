from bushdump.backup import (
    advance_watermark,
    date_from_name,
    media_names_of_kind,
    parse_rsync_pending,
    safe_watermark,
)

# --- date_from_name ---


def test_date_from_name_canonical_jpg():
    assert date_from_name("20260510T130001_00000001.jpg") == "2026-05-10 13:00:01"


def test_date_from_name_canonical_mp4():
    assert date_from_name("20260510T130001_00000002.mp4") == "2026-05-10 13:00:01"


def test_date_from_name_rejects_error_sidecar():
    assert date_from_name("20260510T130001_00000001.jpg.error.txt") is None


def test_date_from_name_rejects_part():
    assert date_from_name("20260510T130001_00000001.jpg.part") is None


def test_date_from_name_rejects_part2():
    assert date_from_name("20260510T130001_00000001.jpg.part2") is None


def test_date_from_name_rejects_alt_jpg():
    assert date_from_name("20260510T130001_00000001.alt.jpg") is None


def test_date_from_name_rejects_alt_mp4():
    assert date_from_name("20260510T130001_00000001.alt.mp4") is None


def test_date_from_name_rejects_collision_suffix():
    assert date_from_name("20260510T130001_00000001_2.jpg") is None
    assert date_from_name("20260510T130001_00000001_3.jpg") is None


def test_date_from_name_rejects_garbage():
    assert date_from_name("garbage") is None
    assert date_from_name("") is None
    assert date_from_name(".DS_Store") is None
    assert date_from_name("photo.jpg") is None
    assert date_from_name("20260510T130001_00000001.txt") is None


# --- parse_rsync_pending ---


def test_parse_rsync_pending_typical():
    output = "20260510T130001_00000001.jpg\n20260510T130002_00000002.mp4\nsubdir/\n.DS_Store\n\n"
    result = parse_rsync_pending(output)
    assert "20260510T130001_00000001.jpg" in result
    assert "20260510T130002_00000002.mp4" in result
    assert ".DS_Store" in result
    assert "" not in result
    # directories excluded
    for name in result:
        assert not name.endswith("/")


def test_parse_rsync_pending_empty():
    assert parse_rsync_pending("") == set()


def test_parse_rsync_pending_strips_path_prefix():
    output = "subdir/20260510T130001_00000001.jpg\n"
    result = parse_rsync_pending(output)
    assert "20260510T130001_00000001.jpg" in result
    assert "subdir/20260510T130001_00000001.jpg" not in result


# --- media_names_of_kind ---


def test_media_names_of_kind_filters_to_jpg():
    names = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.mp4",
        ".DS_Store",
        "20260510T130003_00000003.jpg.part",
        "20260510T130004_00000004.alt.jpg",
        "20260510T130005_00000005_2.jpg",
        "20260510T130006_00000006.jpg.error.txt",
    ]
    result = media_names_of_kind(names, "JPG")
    assert result == {"20260510T130001_00000001.jpg"}


def test_media_names_of_kind_filters_to_mp4():
    names = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.mp4",
        "20260510T130003_00000003.mp4.error.txt",
        "20260510T130004_00000004.alt.mp4",
    ]
    result = media_names_of_kind(names, "MP4")
    assert result == {"20260510T130002_00000002.mp4"}


def test_media_names_of_kind_cross_type_guard():
    names = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.mp4",
    ]
    jpg_result = media_names_of_kind(names, "JPG")
    mp4_result = media_names_of_kind(names, "MP4")
    assert "20260510T130002_00000002.mp4" not in jpg_result
    assert "20260510T130001_00000001.jpg" not in mp4_result


def test_media_names_of_kind_drops_ds_store():
    assert media_names_of_kind([".DS_Store"], "JPG") == set()


def test_media_names_of_kind_empty():
    assert media_names_of_kind([], "JPG") == set()


# --- safe_watermark ---


def test_safe_watermark_all_confirmed():
    local = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.jpg",
        "20260510T130003_00000003.jpg",
    ]
    assert safe_watermark(local, set()) == "2026-05-10 13:00:03"


def test_safe_watermark_blocked_caps():
    local = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.jpg",
        "20260510T130003_00000003.jpg",
    ]
    blocked = {"20260510T130002_00000002.jpg"}
    # earliest_blocked = "2026-05-10 13:00:02"; confirmed = dates < that
    assert safe_watermark(local, blocked) == "2026-05-10 13:00:01"


def test_safe_watermark_non_parseable_blocked_ignored():
    local = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.jpg",
    ]
    # Neither "garbage" nor the .error.txt sidecar parse as canonical names
    blocked = {"garbage", "20260510T130001_00000001.jpg.error.txt"}
    assert safe_watermark(local, blocked) == "2026-05-10 13:00:02"


def test_safe_watermark_sidecar_caps_even_if_rsync_copied():
    local = [
        "20260509T120000_00000001.jpg",
        "20260510T130001_00000001.jpg",
    ]
    # File was rsync-transferred but has a local .error.txt → still in blocked
    blocked = {"20260510T130001_00000001.jpg"}
    assert safe_watermark(local, blocked) == "2026-05-09 12:00:00"


def test_safe_watermark_same_second_rule():
    # Two files at the same timestamp T; one is blocked → watermark must be < T
    local = [
        "20260509T120000_00000001.jpg",
        "20260510T130001_00000001.jpg",
        "20260510T130001_00000002.jpg",
    ]
    blocked = {"20260510T130001_00000002.jpg"}
    # earliest_blocked date = "2026-05-10 13:00:01" (same as confirmed sibling)
    # confirmed = dates strictly < "2026-05-10 13:00:01"
    assert safe_watermark(local, blocked) == "2026-05-09 12:00:00"


def test_safe_watermark_oldest_blocked_returns_none():
    local = [
        "20260510T130001_00000001.jpg",
        "20260510T130002_00000002.jpg",
    ]
    blocked = {"20260510T130001_00000001.jpg"}
    # earliest_blocked = "2026-05-10 13:00:01"; no confirmed dates strictly before it
    assert safe_watermark(local, blocked) is None


def test_safe_watermark_empty_local():
    assert safe_watermark([], set()) is None


def test_safe_watermark_empty_local_with_blocked():
    assert safe_watermark([], {"20260510T130001_00000001.jpg"}) is None


# --- advance_watermark ---


def test_advance_watermark_fresh():
    result, regressed = advance_watermark("2026-05-10 13:00:01", None)
    assert result == "2026-05-10 13:00:01"
    assert regressed is False


def test_advance_watermark_advances():
    stored = "2026-05-10 13:00:01"
    computed = "2026-05-11 14:00:00"
    result, regressed = advance_watermark(computed, stored)
    assert result == computed
    assert regressed is False


def test_advance_watermark_no_retreat():
    stored = "2026-05-10 13:00:01"
    computed = "2026-05-09 12:00:00"
    result, regressed = advance_watermark(computed, stored)
    assert result == stored  # keeps stored
    assert regressed is True


def test_advance_watermark_computed_none_keeps_stored():
    stored = "2026-05-10 13:00:01"
    result, regressed = advance_watermark(None, stored)
    assert result == stored
    assert regressed is False


def test_advance_watermark_both_none():
    result, regressed = advance_watermark(None, None)
    assert result is None
    assert regressed is False
