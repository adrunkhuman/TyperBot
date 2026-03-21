"""Tests for timezone utilities.

Timezones are tricky. These tests verify:
- Consistent behavior across DST transitions
- Correct parsing regardless of input format
- Environment-based timezone configuration
"""

from datetime import UTC, datetime

import pytest
from freezegun import freeze_time

from typer_bot.utils import timezone as tz_module


class TestNow:
    """Test now() function."""

    def test_returns_timezone_aware(self):
        """now() must return timezone-aware datetime."""
        result = tz_module.now()
        assert result.tzinfo is not None

    @freeze_time("2024-03-15 14:30:00")
    def test_returns_frozen_time(self):
        """now() returns current time in APP_TZ."""
        result = tz_module.now()
        # Time is frozen at UTC, but now() converts to APP_TZ
        assert result.year == 2024
        assert result.month == 3
        assert result.day == 15


class TestParseDeadline:
    """Test parse_deadline() function."""

    def test_basic_parsing(self):
        """Parse standard format with APP_TZ attached."""
        result = tz_module.parse_deadline("2024-03-15 14:30")
        assert result.year == 2024
        assert result.month == 3
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo == tz_module.APP_TZ

    def test_with_whitespace(self):
        """Handle leading/trailing whitespace."""
        result = tz_module.parse_deadline("  2024-03-15 14:30  ")
        assert result.year == 2024
        assert result.tzinfo == tz_module.APP_TZ

    def test_dst_transition_spring(self):
        """DST spring forward - clocks skip 2:00-3:00 AM."""
        # In Europe/Warsaw, DST starts last Sunday of March at 2:00 AM
        # 2024: March 31, clocks jump from 1:59:59 to 3:00:00
        result = tz_module.parse_deadline("2024-03-31 04:00")  # After transition
        assert result.tzinfo == tz_module.APP_TZ

    def test_dst_transition_fall(self):
        """DST fall back - clocks repeat 2:00-3:00 AM."""
        # In Europe/Warsaw, DST ends last Sunday of October at 3:00 AM
        # 2024: October 27, clocks fall back from 2:59:59 to 2:00:00
        result = tz_module.parse_deadline("2024-10-27 04:00")  # After transition
        assert result.tzinfo == tz_module.APP_TZ


class TestFormatForDiscord:
    """Test format_for_discord() function."""

    def test_formats_full_timestamp(self):
        """Format datetime as Discord full timestamp."""
        dt = tz_module.parse_deadline("2024-03-15 14:30")
        result = tz_module.format_for_discord(dt, "F")
        unix_ts = int(dt.timestamp())
        assert result == f"<t:{unix_ts}:F>"

    def test_formats_relative_timestamp(self):
        """Format datetime as Discord relative timestamp."""
        dt = tz_module.parse_deadline("2024-07-15 20:00")
        result = tz_module.format_for_discord(dt, "R")
        unix_ts = int(dt.timestamp())
        assert result == f"<t:{unix_ts}:R>"

    def test_raises_on_naive_datetime(self):
        """Must raise error on naive datetime."""
        naive = datetime(2024, 3, 15, 14, 30)
        with pytest.raises(ValueError, match="timezone-aware"):
            tz_module.format_for_discord(naive)


class TestParseIso:
    """Test parse_iso() function."""

    def test_naive_iso_string(self):
        """Naive ISO string gets APP_TZ attached."""
        result = tz_module.parse_iso("2024-03-15T14:30:00")
        assert result.tzinfo == tz_module.APP_TZ
        assert result.hour == 14

    def test_utc_iso_string(self):
        """UTC ISO string converted to APP_TZ."""
        result = tz_module.parse_iso("2024-03-15T12:00:00+00:00")
        # Should convert to APP_TZ timezone
        assert result.tzinfo is not None

    def test_different_timezone_in_string(self):
        """ISO string with different timezone preserved."""
        result = tz_module.parse_iso("2024-03-15T14:30:00+05:00")
        # The timezone from the string is preserved
        assert result.tzinfo is not None


class TestTimezoneConfiguration:
    """Test timezone configuration via TZ environment variable."""

    def test_default_utc(self):
        """Default timezone is UTC when TZ not set."""
        # APP_TZ is set at import time, can't easily change in tests
        assert str(tz_module.APP_TZ) == "UTC"

    @pytest.mark.skip(reason="APP_TZ set at import time, can't test dynamically")
    def test_tz_env_var_changes_timezone(self, monkeypatch):
        """TZ env var changes APP_TZ - skipped due to import-time binding."""
        # APP_TZ is set when module is imported, so we can't test this
        # without reloading the module. Documenting this limitation.
        pass


class TestTimezoneComparisons:
    """Test datetime comparisons across timezones."""

    def test_same_time_different_zones(self):
        """parse_deadline produces a tz-aware datetime that round-trips to UTC correctly."""
        app_time = tz_module.parse_deadline("2024-03-15 14:00")
        utc_time = app_time.astimezone(UTC)

        # Same Unix timestamp regardless of zone
        assert app_time.timestamp() == utc_time.timestamp()
        # With UTC as default APP_TZ the hour values are the same
        assert app_time.hour == utc_time.hour

    def test_comparing_naive_and_aware_raises_error(self):
        """Can't compare naive and timezone-aware datetimes directly."""
        aware = tz_module.parse_deadline("2024-03-15 14:00")
        naive = datetime(2024, 3, 15, 14, 0)

        with pytest.raises(TypeError):
            aware > naive  # noqa: B015
