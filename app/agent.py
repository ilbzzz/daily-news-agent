"""Agent definitions, ADK tool integration, and timezone utilities for Daily Top News Summary AI Agent."""

import asyncio
from datetime import datetime, time, timedelta, timezone
import os
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

try:
  from google.cloud import firestore
except ImportError:
  firestore = None

# ADK SDK imports
from google.adk import agents
from google.adk import apps
from google.adk import models
from google.adk import runners
from google.genai import types

from app import pipeline
from app import researcher
from app import timezone_utils
from app import tools


def get_firestore_client() -> Optional[Any]:
  """Initializes and returns a firestore.Client using environment variables."""
  if firestore is None:
    return None
  try:
    project_id = (
        os.environ.get("GCP_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or "xz-ai-agents"
    )
    return firestore.Client(project=project_id)
  except Exception:
    return None


class UserOnboardingAgent:
  """Handles user onboarding, profile validation, Firestore persistence, and HITL test dispatches."""

  def __init__(self, db_client: Any = None):
    self.db = db_client

  def register_user(
      self,
      email: str,
      topic: str,
      timezone_raw: str,
      now_utc: Optional[datetime] = None,
  ) -> Dict[str, Any]:
    """Validates input, computes next_trigger_utc, and returns user profile dict."""
    # Sanitize email and normalize timezone inputs
    validated_email = tools.validate_and_sanitize_email(email)
    canonical_tz = timezone_utils.normalize_timezone(timezone_raw)
    if not topic or not topic.strip():
      raise ValueError("Topic must be a non-empty string.")

    # Ensure now_utc has explicit UTC timezone info
    if now_utc is None:
      now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
      now_utc = now_utc.replace(tzinfo=timezone.utc)

    # Compute initial local 8:00 AM UTC trigger
    next_trigger = timezone_utils.calculate_next_local_8am_utc(canonical_tz, now_utc)

    # Construct user profile document schema
    profile = {
        "email": validated_email,
        "topic": topic.strip(),
        "timezone": canonical_tz,
        "active": True,
        "status": "idle",
        "next_trigger_utc": next_trigger,
        "updated_at": now_utc,
    }

    # Persist user profile to Firestore document collection if db client exists
    if self.db is not None:
      self.db.collection("users").document(validated_email).set(profile)

    return profile

  def handle_hitl_send_now(
      self, user_profile: Dict[str, Any], now_utc: Optional[datetime] = None
  ) -> Dict[str, Any]:
    """Handles HITL 'Send Now' action with suppression guardrail for upcoming scheduled trigger."""
    # Standardize now_utc timezone
    if now_utc is None:
      now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
      now_utc = now_utc.replace(tzinfo=timezone.utc)

    # Parse and ensure datetime timezone awareness for next_trigger_utc
    next_trigger = user_profile.get("next_trigger_utc")
    if isinstance(next_trigger, str):
      next_trigger = datetime.fromisoformat(next_trigger)
    if next_trigger and next_trigger.tzinfo is None:
      next_trigger = next_trigger.replace(tzinfo=timezone.utc)

    # Suppression guardrail: If within 2 hours of scheduled trigger, bump trigger to tomorrow 8:00 AM local
    if next_trigger and (next_trigger - now_utc) <= timedelta(hours=2):
      user_tz = ZoneInfo(user_profile["timezone"])
      now_local = now_utc.astimezone(user_tz)

      # Calculate tomorrow 8:00 AM in user local timezone
      tomorrow_8am_local = datetime.combine(
          now_local.date() + timedelta(days=1), time(8, 0, 0), tzinfo=user_tz
      )
      # Convert updated trigger to UTC and store back to profile
      updated_trigger = tomorrow_8am_local.astimezone(ZoneInfo("UTC"))
      user_profile["next_trigger_utc"] = updated_trigger

    # Update profile timestamp
    user_profile["updated_at"] = now_utc

    # Update Firestore record if DB client present
    if self.db is not None and "email" in user_profile:
      self.db.collection("users").document(user_profile["email"]).update({
          "next_trigger_utc": user_profile["next_trigger_utc"],
          "updated_at": user_profile["updated_at"],
      })

    return user_profile


# ---------------------------------------------------------------------------
# ADK Tools Definitions
# ---------------------------------------------------------------------------

def register_user(email: str, topic: str, timezone: str) -> dict:
  """Registers a user for the daily news digest.

  Args:
    email: The user's email address.
    topic: The news topic the user is interested in (e.g., 'Artificial Intelligence').
    timezone: The user's timezone (e.g., 'America/New_York' or 'Asia/Kolkata').

  Returns:
    A dictionary with the registration status and user profile details.
  """
  db = get_firestore_client()
  onboarder = UserOnboardingAgent(db_client=db)
  try:
    profile = onboarder.register_user(email=email, topic=topic, timezone_raw=timezone)
    # Convert datetime objects to string for JSON serialization
    if "next_trigger_utc" in profile and isinstance(profile["next_trigger_utc"], datetime):
      profile["next_trigger_utc"] = profile["next_trigger_utc"].isoformat()
    if "updated_at" in profile and isinstance(profile["updated_at"], datetime):
      profile["updated_at"] = profile["updated_at"].isoformat()
    return {"status": "success", "profile": profile}
  except Exception as e:
    return {"status": "error", "message": str(e)}


def unsubscribe_user(email: str) -> dict:
  """Unsubscribes a user from the daily news digest.

  Args:
    email: The user's registered email address.

  Returns:
    A dictionary indicating the unsubscribe status.
  """
  db = get_firestore_client()
  if db is None:
    return {"status": "error", "message": "Database unavailable"}
  try:
    doc_ref = db.collection("users").document(email.strip().lower())
    doc = doc_ref.get()
    if not doc.exists:
      return {"status": "error", "message": f"User with email '{email}' not found."}
    
    doc_ref.update({
        "active": False,
        "updated_at": datetime.now(timezone.utc)
    })
    return {"status": "success", "message": f"Successfully unsubscribed {email}."}
  except Exception as e:
    return {"status": "error", "message": str(e)}


async def trigger_digest_now(email: str) -> dict:
  """Triggers an immediate news digest email for the specified user.

  Args:
    email: The user's registered email address.

  Returns:
    A dictionary indicating the pipeline trigger status.
  """
  db = get_firestore_client()
  if db is None:
    return {"status": "error", "message": "Database unavailable"}
  try:
    doc_ref = db.collection("users").document(email.strip().lower())
    doc = doc_ref.get()
    if not doc.exists:
      return {"status": "error", "message": f"User with email '{email}' not found."}
    
    user_data = doc.to_dict()
    if not user_data.get("active", False):
      return {"status": "error", "message": f"User {email} is inactive. Cannot trigger digest."}

    user_topic = user_data.get("topic", "General News")
    user_tz = user_data.get("timezone", "UTC")
    now_utc = datetime.now(timezone.utc)
    
    now_utc = datetime.now(timezone.utc)
    
    claimed = db.run_transaction(
        lambda tx: pipeline.claim_user_transaction(tx, doc_ref, now_utc)
    )
    if not claimed:
      return {"status": "error", "message": "User is currently being processed. Lock acquired by another task."}

    try:
      cutoff_date = timezone_utils.get_user_local_cutoff_date(user_tz, now_utc)
      researcher_agent = researcher.NewsResearcherAgent()
      raw_summary = researcher_agent.run(topic=user_topic, search_date_cutoff=cutoff_date)
      if not raw_summary or not raw_summary.strip():
        raw_summary = f"# Daily Top Digest: {user_topic}\n\nNo Major News Today."

      html_content = pipeline.render_markdown_to_html(raw_summary)

      await tools.send_news_email(recipient_email=email, topic=user_topic, html_content=html_content)

      # Handle hitl send bump if needed
      onboarder = UserOnboardingAgent(db_client=db)
      onboarder.handle_hitl_send_now(user_data, now_utc)
      
      return {"status": "success", "message": f"Digest email triggered and sent to {email} successfully."}
    finally:
      # Release lock
      doc_ref.update({"status": "idle", "updated_at": now_utc})
      
  except Exception as e:
    return {"status": "error", "message": str(e)}


async def run_daily_pipeline_tool() -> dict:
  """Triggers the daily news digest pipeline for all registered users who are due.

  Returns:
    A dictionary containing the pipeline execution statistics.
  """
  db = get_firestore_client()
  if db is None:
    return {"status": "error", "message": "Database unavailable"}
  try:
    results = await pipeline.run_daily_pipeline(db=db)
    return results
  except Exception as e:
    return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# ADK Agent Definitions
# ---------------------------------------------------------------------------

INSTRUCTION_PROMPT = """You are the Daily News Agent. You help users manage their daily news email digest subscriptions.

You have tools to:
1. Register a user: `register_user(email, topic, timezone)`.
2. Unsubscribe a user: `unsubscribe_user(email)`.
3. Send a test digest immediately: `trigger_digest_now(email)`.
4. Trigger the daily news pipeline for all due users: `run_daily_pipeline_tool()`.

When a user asks to subscribe or register, extract their email, topic, and timezone.
- Email: Valid email address.
- Topic: The subject they want news about.
- Timezone: A valid IANA timezone (e.g., 'America/New_York', 'Asia/Kolkata'). If they provide an abbreviation like 'EST' or 'PST', convert it to a valid IANA timezone if possible, or ask for clarification.

If the user does not provide some parameters (like timezone or topic), ask them for the missing details politely.

If a user asks to unsubscribe, ask for their email and call the unsubscribe tool.
If a user wants to test or run the news digest right now, ask for their email and trigger it immediately.

If the system or user requests to run/trigger the daily pipeline or process due users, call the run_daily_pipeline_tool.

Always respond politely and confirm when a tool call succeeds.
"""

root_agent = agents.Agent(
    name="daily_news_agent",
    model=models.Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION_PROMPT,
    tools=[register_user, unsubscribe_user, trigger_digest_now, run_daily_pipeline_tool],
)

app = apps.App(
    root_agent=root_agent,
    name="app",
)


# ---------------------------------------------------------------------------
# Legacy Interface Class for Compatibility and Local Tests
# ---------------------------------------------------------------------------

class DailyNewsAgent:
  """Root ADK Conversational Agent exposing query() for Gemini Enterprise chat interface."""

  def __init__(self, db_client: Any = None):
    self._db = db_client

  def set_up(self):
    pass

  @property
  def db(self):
    if self._db is None:
      self._db = get_firestore_client()
    return self._db

  def query(
      self,
      prompt: Optional[str] = None,
      input: Optional[str] = None,
      message: Optional[str] = None,
      **kwargs: Any,
  ) -> str:
    """Main entrypoint for processing conversational user queries from Agent Runtime or Gemini Enterprise."""
    user_prompt = (
        prompt
        or input
        or message
        or kwargs.get("user_input")
        or kwargs.get("query")
        or ""
    )
    if isinstance(user_prompt, dict):
      user_prompt = (
          user_prompt.get("text")
          or user_prompt.get("content")
          or str(user_prompt)
      )
    if not user_prompt or not isinstance(user_prompt, str):
      user_prompt = "Hello"

    # If local test environment (DRY_RUN_MODE=true), use rule-based fallback to avoid Vertex AI API calls
    if os.environ.get("DRY_RUN_MODE", "false").lower() == "true":
      prompt_lower = user_prompt.lower()
      if "subscribe" in prompt_lower or "register" in prompt_lower:
        return (
            "To register for daily news digests, please specify your email,"
            " topic, and timezone (e.g., 'Register user@example.com for AI in"
            " America/New_York')."
        )

      if any(k in prompt_lower for k in ("news", "digest", "run", "pipeline", "today")):
        return (
            "Daily news digest pipeline triggered successfully!\n"
            "Results: {'status': 'success'}"
        )

      return (
          f"Daily News AI Agent received your request: '{user_prompt}'.\n"
          "I deliver daily 8:00 AM news digests tailored to your topic and timezone!"
      )

    # Production flow: Run ADK runner synchronously
    session_id = "default-session"
    runner = runners.InMemoryRunner(agent=root_agent, app_name="daily_news_agent")

    async def run_agent():
      events = []
      new_msg = types.Content(
          role="user",
          parts=[types.Part(text=user_prompt)]
      )
      async for event in runner.run_async(
          user_id="sync-user",
          session_id=session_id,
          new_message=new_msg,
      ):
        events.append(event)
      
      output_text = ""
      for event in events:
        if event.content:
          for part in event.content.parts:
            if hasattr(part, "text") and part.text:
              output_text += part.text
      return output_text

    try:
      return asyncio.run(run_agent())
    except Exception as e:
      return f"Error executing conversational agent: {e}"
