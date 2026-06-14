"""Camera health checks — pure logic, no I/O.

All thresholds are module-level constants so they're easy to adjust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from bushdump.camera import CameraStats, TimeInfo

# Clock drift thresholds (seconds)
_CLOCK_WARN_SECS = 5
_CLOCK_ALERT_SECS = 60

# SD card thresholds
_SD_WARN_PCT = 75
_SD_ALERT_PCT = 90
_SD_WARN_FREE_GB = 10.0
_SD_ALERT_FREE_GB = 2.0

# Battery thresholds (percent)
_BATTERY_WARN_PCT = 30
_BATTERY_ALERT_PCT = 15

# Temperature thresholds (°C)
_TEMP_LOW_WARN = 0
_TEMP_LOW_ALERT = -10
_TEMP_HIGH_WARN = 50
_TEMP_HIGH_ALERT = 60


@dataclass(frozen=True, slots=True)
class Warning:
    level: Literal["warn", "alert"]
    code: str
    message: str


def check_clock(camera_clock_utc: datetime, now_utc: datetime) -> Warning | None:
    drift = (camera_clock_utc - now_utc).total_seconds()
    abs_drift = abs(drift)
    if abs_drift < _CLOCK_WARN_SECS:
        return None
    direction = "ahead" if drift > 0 else "behind"
    msg = f"Camera clock is {abs_drift:.0f}s {direction} of laptop"
    level: Literal["warn", "alert"] = "alert" if abs_drift >= _CLOCK_ALERT_SECS else "warn"
    return Warning(level=level, code="clock_drift", message=msg)


def check_sd(used_kb: int, total_kb: int) -> Warning | None:
    if total_kb <= 0:
        return None
    pct = used_kb / total_kb * 100
    free_gb = (total_kb - used_kb) / (1024 * 1024)
    if pct >= _SD_ALERT_PCT or free_gb < _SD_ALERT_FREE_GB:
        return Warning(
            level="alert",
            code="sd_full",
            message=f"SD card critically full: {pct:.0f}% used, {free_gb:.1f} GB free",
        )
    if pct >= _SD_WARN_PCT or free_gb < _SD_WARN_FREE_GB:
        return Warning(
            level="warn",
            code="sd_full",
            message=f"SD card {pct:.0f}% used, {free_gb:.1f} GB free",
        )
    return None


def check_battery(percent: int) -> Warning | None:
    if percent <= 0:
        return None  # 0 % is also reported when on ext power with some firmware
    if percent <= _BATTERY_ALERT_PCT:
        return Warning(
            level="alert", code="battery_low", message=f"Battery critically low: {percent}%"
        )
    if percent <= _BATTERY_WARN_PCT:
        return Warning(level="warn", code="battery_low", message=f"Battery low: {percent}%")
    return None


def check_ext_power(ext_power: bool, expected: bool) -> Warning | None:
    if not expected:
        return None
    if not ext_power:
        return Warning(
            level="warn",
            code="ext_power_absent",
            message="External power expected but not detected",
        )
    return None


def check_temperature(celsius: int) -> Warning | None:
    if celsius <= _TEMP_LOW_ALERT or celsius >= _TEMP_HIGH_ALERT:
        return Warning(
            level="alert",
            code="temperature",
            message=f"Camera temperature extreme: {celsius}°C",
        )
    if celsius < _TEMP_LOW_WARN or celsius > _TEMP_HIGH_WARN:
        return Warning(
            level="warn",
            code="temperature",
            message=f"Camera temperature out of range: {celsius}°C",
        )
    return None


def evaluate(
    stats: CameraStats,
    time_info: TimeInfo | None,
    *,
    expect_ext_power: bool,
    now_utc: datetime | None = None,
) -> list[Warning]:
    """Return all active warnings for a camera. Silent when everything is healthy."""
    if now_utc is None:
        now_utc = datetime.now(UTC)
    warnings: list[Warning] = []
    if time_info is not None:
        w = check_clock(time_info.clock_utc, now_utc)
        if w:
            warnings.append(w)
    w = check_sd(stats.sd_used_kb, stats.sd_total_kb)
    if w:
        warnings.append(w)
    w = check_battery(stats.battery)
    if w:
        warnings.append(w)
    w = check_ext_power(stats.ext_power, expect_ext_power)
    if w:
        warnings.append(w)
    w = check_temperature(stats.temperature)
    if w:
        warnings.append(w)
    return warnings
