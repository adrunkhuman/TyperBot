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


def format_for_display(dt: datetime) -> str:
    """Format datetime for user display, stripping timezone info.

    Shows time in APP_TZ without the +HH:00 offset.
    """
    return dt.strftime("%A, %B %d at %H:%M")


def parse_iso(iso_str: str) -> datetime:
    """Parse ISO format string, ensuring timezone awareness.

    Backward compatible: naive strings are treated as APP_TZ.
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TZ)
    return dt
