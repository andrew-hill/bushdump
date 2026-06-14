"""Tests for bushdump.health — pure logic, no I/O."""

from datetime import UTC, datetime, timedelta

from bushdump.camera import CameraStats, TimeInfo, parse_info4
from bushdump.health import (
    check_battery,
    check_clock,
    check_ext_power,
    check_sd,
    check_temperature,
    evaluate,
)


def _stats(**overrides) -> CameraStats:
    defaults = dict(
        battery=80,
        temperature=22,
        ext_power=False,
        sd_total_kb=128 * 1024 * 1024,  # 128 GB
        sd_used_kb=10 * 1024 * 1024,  # 10 GB
        photo_count=100,
        video_count=10,
    )
    defaults.update(overrides)
    return CameraStats(**defaults)


def _time_info(offset_secs: float = 0.0, now: datetime | None = None) -> TimeInfo:
    """Build a TimeInfo whose clock_utc is `offset_secs` ahead of `now` (default: actual now)."""
    base = now or datetime.now(UTC)
    return TimeInfo(clock_utc=base + timedelta(seconds=offset_secs), tz_minutes=None)


# --- check_clock ---


def test_clock_within_window():
    now = datetime.now(UTC)
    assert check_clock(now + timedelta(seconds=3), now) is None
    assert check_clock(now - timedelta(seconds=3), now) is None


def test_clock_warn():
    now = datetime.now(UTC)
    w = check_clock(now + timedelta(seconds=6), now)
    assert w is not None
    assert w.level == "warn"
    assert "ahead" in w.message


def test_clock_warn_behind():
    now = datetime.now(UTC)
    w = check_clock(now - timedelta(seconds=6), now)
    assert w is not None
    assert w.level == "warn"
    assert "behind" in w.message


def test_clock_alert():
    now = datetime.now(UTC)
    w = check_clock(now - timedelta(seconds=70), now)
    assert w is not None
    assert w.level == "alert"


# --- check_sd ---


def _kb(gb: float) -> int:
    return int(gb * 1024 * 1024)


def test_sd_healthy():
    assert check_sd(_kb(50), _kb(128)) is None  # ~39%, 78 GB free


def test_sd_warn_pct():
    # 80% used, 25.6 GB free — over pct threshold
    assert check_sd(_kb(102.4), _kb(128)) is not None
    w = check_sd(_kb(102.4), _kb(128))
    assert w.level == "warn"


def test_sd_warn_free_space():
    # 50% used on a small card, but only 8 GB free
    w = check_sd(_kb(8), _kb(16))
    assert w is not None
    assert w.level == "warn"


def test_sd_alert_pct():
    # 95% used
    w = check_sd(_kb(121.6), _kb(128))
    assert w is not None
    assert w.level == "alert"


def test_sd_alert_free_space():
    # 50% used but only 1 GB free — alert level
    w = check_sd(_kb(15), _kb(16))
    assert w is not None
    assert w.level == "alert"


def test_sd_zero_total():
    assert check_sd(0, 0) is None


# --- check_battery ---


def test_battery_ok():
    assert check_battery(80) is None
    assert check_battery(50) is None
    assert check_battery(31) is None


def test_battery_warn():
    w = check_battery(25)
    assert w is not None
    assert w.level == "warn"


def test_battery_alert():
    w = check_battery(10)
    assert w is not None
    assert w.level == "alert"


def test_battery_zero_ignored():
    # 0% reported on ext power by some firmware; don't warn
    assert check_battery(0) is None


# --- check_ext_power ---


def test_ext_power_not_expected():
    assert check_ext_power(False, False) is None
    assert check_ext_power(True, False) is None


def test_ext_power_expected_present():
    assert check_ext_power(True, True) is None


def test_ext_power_expected_absent():
    w = check_ext_power(False, True)
    assert w is not None
    assert w.level == "warn"


# --- check_temperature ---


def test_temperature_ok():
    assert check_temperature(22) is None
    assert check_temperature(0) is None
    assert check_temperature(50) is None


def test_temperature_warn_high():
    w = check_temperature(55)
    assert w is not None
    assert w.level == "warn"


def test_temperature_alert_high():
    w = check_temperature(65)
    assert w is not None
    assert w.level == "alert"


def test_temperature_warn_low():
    w = check_temperature(-5)
    assert w is not None
    assert w.level == "warn"


def test_temperature_alert_low():
    w = check_temperature(-15)
    assert w is not None
    assert w.level == "alert"


# --- evaluate (orchestrator) ---


def test_evaluate_healthy_is_silent():
    s = _stats()
    ti = _time_info(offset_secs=1)
    now = datetime.now(UTC)
    warnings = evaluate(s, ti, expect_ext_power=False, now_utc=now)
    assert warnings == []


def test_evaluate_multiple_issues():
    now = datetime.now(UTC)
    s = _stats(battery=10, sd_used_kb=_kb(120), sd_total_kb=_kb(128))
    ti = _time_info(offset_secs=200, now=now)
    warnings = evaluate(s, ti, expect_ext_power=False, now_utc=now)
    codes = {w.code for w in warnings}
    assert "clock_drift" in codes
    assert "sd_full" in codes
    assert "battery_low" in codes


def test_evaluate_no_time_info():
    s = _stats()
    warnings = evaluate(s, None, expect_ext_power=False)
    assert all(w.code != "clock_drift" for w in warnings)


# --- parse_info4 ---


def test_parse_info4_with_tz_key():
    data = {"code": 0, "data": {"clock": "2026-06-14 10:30:00", "tz": 600}}
    ti = parse_info4(data)
    assert ti is not None
    assert ti.tz_minutes == 600
    # UTC = local − 600 min
    assert ti.clock_utc == datetime(2026, 6, 14, 0, 30, 0, tzinfo=UTC)


def test_parse_info4_with_timezone_key():
    data = {"code": 0, "data": {"clock": "2026-06-14 10:30:00", "timezone": -300}}
    ti = parse_info4(data)
    assert ti is not None
    assert ti.tz_minutes == -300
    assert ti.clock_utc == datetime(2026, 6, 14, 15, 30, 0, tzinfo=UTC)


def test_parse_info4_no_tz():
    data = {"code": 0, "data": {"clock": "2026-06-14 10:30:00"}}
    ti = parse_info4(data)
    assert ti is not None
    assert ti.tz_minutes is None
    # Assume UTC
    assert ti.clock_utc == datetime(2026, 6, 14, 10, 30, 0, tzinfo=UTC)


def test_parse_info4_garbage():
    assert parse_info4(None) is None
    assert parse_info4({}) is None
    assert parse_info4({"data": {"clock": "not a date"}}) is None
    assert parse_info4("bad") is None
