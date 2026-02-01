"""Timezone utilities for the prediction bot.

All datetime operations use a single configurable timezone (from TZ env var).
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo(os.getenv("TZ", "Europe/Warsaw"))


def now() -> datetime:
    """Get current time in the configured application timezone."""
    return datetime.now(APP_TZ)


def parse_deadline(date_str: str) -> datetime:
    """Parse a naive datetime string and attach the application timezone.

    Expected format: "YYYY-MM-DD HH:MM"
    Returns timezone-aware datetime in APP_TZ.
    """
    naive = datetime.strptime(date_str.strip(), "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=APP_TZ)


def format_for_discord(dt: datetime, format_code: str = "F") -> str:
    """Format datetime as Discord timestamp (auto-converts to user's local time).

    Args:
        dt: Timezone-aware datetime object
        format_code: Discord format code - F/f/D/d/t/T/R

    Returns:
        Discord timestamp string like "<t:{unix}:F>"
    """
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    unix_timestamp = int(dt.timestamp())
    return f"<t:{unix_timestamp}:{format_code}>"


def parse_iso(iso_str: str) -> datetime:
    """Parse ISO format string, ensuring timezone awareness.

    Backward compatible: naive strings are treated as APP_TZ.
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TZ)
    return dt
