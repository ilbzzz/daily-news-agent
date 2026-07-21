"""Timezone normalisation and target trigger calculation utilities."""

from datetime import datetime, time, timedelta, timezone
import threading
from typing import Dict, Optional
from zoneinfo import ZoneInfo, available_timezones

# Thread lock and cache variables for timezone lookup map
_ZONEINFO_LOCK = threading.Lock()
_ZONEINFO_CACHE: Optional[Dict[str, str]] = None


def _get_zoneinfo_map() -> Dict[str, str]:
  """Builds and caches a thread-safe map of lowercase timezone names and aliases to canonical IANA names."""
  global _ZONEINFO_CACHE
  # Check if cache is uninitialized before acquiring lock (Double-Checked Locking Pattern)
  if _ZONEINFO_CACHE is None:
    with _ZONEINFO_LOCK:
      if _ZONEINFO_CACHE is None:
        # Map all standard system IANA timezone strings to lowercase keys
        cache = {zone.lower(): zone for zone in available_timezones()}
        # Add common timezone abbreviation aliases mapped to canonical IANA names
        cache.update({
            "pst": "America/Los_Angeles",
            "pt": "America/Los_Angeles",
            "pacific": "America/Los_Angeles",
            "est": "America/New_York",
            "et": "America/New_York",
            "eastern": "America/New_York",
            "cst": "America/Chicago",
            "ct": "America/Chicago",
            "central": "America/Chicago",
            "mst": "America/Denver",
            "mt": "America/Denver",
            "mountain": "America/Denver",
            "ist": "Asia/Kolkata",
            "jst": "Asia/Tokyo",
            "gmt": "UTC",
            "utc": "UTC",
        })
        # Save constructed map to global cache
        _ZONEINFO_CACHE = cache
  return _ZONEINFO_CACHE


def normalize_timezone(raw_tz: str) -> str:
  """Thread-safely normalizes timezone alias or case-insensitive name to canonical IANA timezone name."""
  if not raw_tz or not isinstance(raw_tz, str):
    raise ValueError("Timezone must be a non-empty string.")

  # Clean input string and query timezone lookup map
  cleaned = raw_tz.strip().lower()
  zone_map = _get_zoneinfo_map()
  canonical = zone_map.get(cleaned)
  if canonical:
    return canonical

  # Raise error if timezone is unrecognized
  raise ValueError(
      f"Unrecognized timezone '{raw_tz}'. Please use a valid IANA timezone like"
      " 'America/New_York' or 'Asia/Kolkata'."
  )


def calculate_next_local_8am_utc(
    timezone_str: str, now_utc: Optional[datetime] = None
) -> datetime:
  """Calculates the next local 8:00 AM trigger time converted to UTC."""
  user_tz = ZoneInfo(timezone_str)

  # Compute current time in user's local timezone
  if now_utc is None:
    now_local = datetime.now(user_tz)
  else:
    if now_utc.tzinfo is None:
      now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_local = now_utc.astimezone(user_tz)

  # Calculate 8:00 AM today in user local timezone
  today_8am = datetime.combine(now_local.date(), time(8, 0, 0), tzinfo=user_tz)

  # If current local time is past 8:00 AM, target 8:00 AM tomorrow
  target_local = (
      today_8am if now_local < today_8am else today_8am + timedelta(days=1)
  )

  # Convert target local time back to UTC
  return target_local.astimezone(ZoneInfo("UTC"))


def get_user_local_cutoff_date(
    timezone_str: str, now_utc: Optional[datetime] = None
) -> str:
  """Computes search date cutoff (24 hours prior in user local timezone) formatted as YYYY-MM-DD."""
  user_tz = ZoneInfo(timezone_str)

  # Determine current local time
  if now_utc is None:
    now_local = datetime.now(user_tz)
  else:
    if now_utc.tzinfo is None:
      now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_local = now_utc.astimezone(user_tz)

  # Subtract 24 hours to find cutoff date in user local timezone
  yesterday_local = now_local - timedelta(hours=24)
  return yesterday_local.strftime("%Y-%m-%d")
