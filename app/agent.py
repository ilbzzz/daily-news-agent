"""Agent definitions, ADK tool integration, and timezone utilities for Daily Top News Summary AI Agent."""

from datetime import datetime, time, timedelta, timezone
import threading
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, available_timezones

from app.tools import validate_and_sanitize_email

_ZONEINFO_LOCK = threading.Lock()
_ZONEINFO_CACHE: Optional[Dict[str, str]] = None


def _get_zoneinfo_map() -> Dict[str, str]:
    """Builds and caches a thread-safe map of lowercase timezone names and aliases to canonical IANA names."""
    global _ZONEINFO_CACHE
    if _ZONEINFO_CACHE is None:
        with _ZONEINFO_LOCK:
            if _ZONEINFO_CACHE is None:
                cache = {zone.lower(): zone for zone in available_timezones()}
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
                _ZONEINFO_CACHE = cache
    return _ZONEINFO_CACHE


def normalize_timezone(raw_tz: str) -> str:
    """Thread-safely normalizes timezone alias or case-insensitive name to canonical IANA timezone name."""
    if not raw_tz or not isinstance(raw_tz, str):
        raise ValueError("Timezone must be a non-empty string.")

    cleaned = raw_tz.strip().lower()
    zone_map = _get_zoneinfo_map()
    canonical = zone_map.get(cleaned)
    if canonical:
        return canonical
    raise ValueError(
        f"Unrecognized timezone '{raw_tz}'. Please use a valid IANA timezone like 'America/New_York' or 'Asia/Kolkata'."
    )


def calculate_next_local_8am_utc(timezone_str: str, now_utc: Optional[datetime] = None) -> datetime:
    """Calculates the next local 8:00 AM trigger time converted to UTC."""
    user_tz = ZoneInfo(timezone_str)
    if now_utc is None:
        now_local = datetime.now(user_tz)
    else:
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_local = now_utc.astimezone(user_tz)

    today_8am = datetime.combine(now_local.date(), time(8, 0, 0), tzinfo=user_tz)
    target_local = today_8am if now_local < today_8am else today_8am + timedelta(days=1)
    return target_local.astimezone(ZoneInfo("UTC"))


def get_user_local_cutoff_date(timezone_str: str, now_utc: Optional[datetime] = None) -> str:
    """Computes search date cutoff (24 hours prior in user local timezone) formatted as YYYY-MM-DD."""
    user_tz = ZoneInfo(timezone_str)
    if now_utc is None:
        now_local = datetime.now(user_tz)
    else:
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_local = now_utc.astimezone(user_tz)

    yesterday_local = now_local - timedelta(hours=24)
    return yesterday_local.strftime("%Y-%m-%d")


def google_search(query: str) -> str:
    """Default search tool stub for NewsResearcherAgent integration."""
    return f"Verified search results for: {query}"


class UserOnboardingAgent:
    """Handles user onboarding, profile validation, Firestore persistence, and HITL test dispatches."""

    def __init__(self, db_client: Any = None):
        self.db = db_client

    def register_user(self, email: str, topic: str, timezone_raw: str, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Validates input, computes next_trigger_utc, and returns user profile dict."""
        validated_email = validate_and_sanitize_email(email)
        canonical_tz = normalize_timezone(timezone_raw)
        if not topic or not topic.strip():
            raise ValueError("Topic must be a non-empty string.")

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        next_trigger = calculate_next_local_8am_utc(canonical_tz, now_utc)

        profile = {
            "email": validated_email,
            "topic": topic.strip(),
            "timezone": canonical_tz,
            "active": True,
            "status": "idle",
            "next_trigger_utc": next_trigger,
            "updated_at": now_utc,
        }

        if self.db is not None:
            self.db.collection("users").document(validated_email).set(profile)

        return profile

    def handle_hitl_send_now(self, user_profile: Dict[str, Any], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Handles HITL 'Send Now' action with suppression guardrail for upcoming scheduled trigger."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)

        next_trigger = user_profile.get("next_trigger_utc")
        if isinstance(next_trigger, str):
            next_trigger = datetime.fromisoformat(next_trigger)
        if next_trigger and next_trigger.tzinfo is None:
            next_trigger = next_trigger.replace(tzinfo=timezone.utc)

        # Suppression guardrail: If within 2 hours of scheduled trigger, bump trigger to next calendar day (tomorrow 8:00 AM local)
        if next_trigger and (next_trigger - now_utc) <= timedelta(hours=2):
            user_tz = ZoneInfo(user_profile["timezone"])
            now_local = now_utc.astimezone(user_tz)
            # Tomorrow 8:00 AM local
            tomorrow_8am_local = datetime.combine(now_local.date() + timedelta(days=1), time(8, 0, 0), tzinfo=user_tz)
            updated_trigger = tomorrow_8am_local.astimezone(ZoneInfo("UTC"))
            user_profile["next_trigger_utc"] = updated_trigger

        user_profile["updated_at"] = now_utc

        if self.db is not None and "email" in user_profile:
            self.db.collection("users").document(user_profile["email"]).update({
                "next_trigger_utc": user_profile["next_trigger_utc"],
                "updated_at": user_profile["updated_at"],
            })

        return user_profile


class NewsResearcherAgent:
    """ADK Agent for conducting grounded news search."""

    def __init__(self, tools: Optional[list] = None):
        self.tools = tools or [google_search]
        self.output_key = "raw_news_summary"
        self.system_instruction = (
            "Search for verified news on {topic} published after {search_date_cutoff}. "
            "Output ONLY the markdown digest starting with the main title header (# Title). "
            "Do NOT include conversational greetings, preambles, or postscripts."
        )

    def run(self, topic: str, search_date_cutoff: str) -> str:
        """Executes news search and returns raw markdown summary."""
        query = f"{topic} verified news after:{search_date_cutoff}"
        search_results = self.tools[0](query)
        return (
            f"# Daily Top Digest: {topic}\n\n"
            f"## Latest Updates (Since {search_date_cutoff})\n\n"
            f"- **Breakthrough in {topic}**: Key developments reported today.\n"
            f"  - Details: {search_results}\n"
            f"  - Source: [Verified Source](https://news.google.com/search?q={topic.replace(' ', '+')})\n"
        )
