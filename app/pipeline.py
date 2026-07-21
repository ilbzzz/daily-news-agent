"""Daily pipeline execution logic including stale lock cleanup, atomic user claim, ADK state pre-population, HTML rendering, and email dispatch."""

import asyncio
from datetime import datetime, time, timedelta, timezone
import re
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import markdown
import nh3

from app.researcher import NewsResearcherAgent
from app.timezone_utils import get_user_local_cutoff_date
from app.tools import send_news_email


def cleanup_stale_locks(
    db: Any, now_dt: Optional[datetime] = None
) -> List[str]:
  """Finds users stuck in 'processing' status for > 30 minutes (limit 50) and resets them using batch write."""
  # Ensure explicit UTC datetime
  if now_dt is None:
    now_dt = datetime.now(timezone.utc)
  elif now_dt.tzinfo is None:
    now_dt = now_dt.replace(tzinfo=timezone.utc)

  # Calculate 30-minute stale cutoff timestamp
  stale_cutoff = now_dt - timedelta(minutes=30)
  cleaned_user_ids = []

  if db is not None:
    # Query up to 50 documents stuck in 'processing' status older than stale_cutoff
    stale_docs = (
        db.collection("users")
        .where("status", "==", "processing")
        .where("updated_at", "<=", stale_cutoff)
        .limit(50)
        .get()
    )
    # Perform atomic batch update resetting status to 'idle'
    if stale_docs:
      batch = db.batch()
      for doc in stale_docs:
        batch.update(doc.reference, {"status": "idle", "updated_at": now_dt})
        cleaned_user_ids.append(doc.id)
      # Commit atomic batch write
      batch.commit()

  return cleaned_user_ids


def claim_user_transaction(
    transaction: Any, doc_ref: Any, now_dt: datetime
) -> bool:
  """Atomic Firestore transaction to claim an idle user for processing."""
  # Retrieve doc snapshot inside transactional lock
  snapshot = doc_ref.get(transaction=transaction)
  if not snapshot.exists or snapshot.get("status") != "idle":
    return False

  # Update status to 'processing' to claim exclusive lock
  transaction.update(doc_ref, {"status": "processing", "updated_at": now_dt})
  return True


def render_markdown_to_html(raw_markdown: str) -> str:
  """Converts Markdown text to sanitized HTML wrapped in a responsive email template."""
  # Fallback text if markdown output is empty
  if not raw_markdown or not raw_markdown.strip():
    raw_markdown = "# Daily Top Digest\n\nNo Major News Today."

  # Convert Markdown to HTML using markdown module
  html_body = markdown.markdown(raw_markdown, extensions=["extra"])

  # Sanitize HTML using nh3 to strip untrusted elements
  clean_html = nh3.clean(html_body)

  # Embed sanitized HTML into a responsive table-based email template with inline CSS
  email_template = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Daily Top Digest</title>
  <style>
    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333333; background-color: #f4f4f9; margin: 0; padding: 20px; }}
    .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; padding: 25px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
    h1 {{ color: #1a73e8; font-size: 24px; border-bottom: 2px solid #e8eaed; padding-bottom: 10px; }}
    h2 {{ color: #202124; font-size: 18px; margin-top: 20px; }}
    a {{ color: #1a73e8; text-decoration: none; }}
    ul {{ padding-left: 20px; }}
    li {{ margin-bottom: 10px; }}
    .footer {{ margin-top: 30px; font-size: 12px; color: #70757a; text-align: center; border-top: 1px solid #e8eaed; padding-top: 15px; }}
  </style>
</head>
<body>
  <div class="container">
    {clean_html}
    <div class="footer">
      <p>Delivered by Daily Top News Summary AI Agent</p>
    </div>
  </div>
</body>
</html>"""
  return email_template


def advance_user_next_trigger_local(
    timezone_str: str,
    current_trigger_utc: Optional[datetime],
    now_utc: Optional[datetime] = None,
) -> datetime:
  """Advances next_trigger_utc to tomorrow's 8:00 AM local time, preventing backlog spam catch-up loops."""
  user_tz = ZoneInfo(timezone_str)
  if now_utc is None:
    now_utc = datetime.now(timezone.utc)
  elif now_utc.tzinfo is None:
    now_utc = now_utc.replace(tzinfo=timezone.utc)

  # Convert current execution timestamp to user local timezone
  now_local = now_utc.astimezone(user_tz)

  if current_trigger_utc:
    if current_trigger_utc.tzinfo is None:
      current_trigger_utc = current_trigger_utc.replace(tzinfo=timezone.utc)
    curr_local = current_trigger_utc.astimezone(user_tz)
    
    # Protect against stale triggers from past outages: Always advance to tomorrow relative to now_local
    base_date = max(curr_local.date(), now_local.date())
    target_date = base_date + timedelta(days=1)
  else:
    # Default to tomorrow if current trigger was absent
    target_date = now_local.date() + timedelta(days=1)

  # Construct 8:00 AM local time on target date and convert to UTC
  next_local_8am = datetime.combine(target_date, time(8, 0, 0), tzinfo=user_tz)
  return next_local_8am.astimezone(ZoneInfo("UTC"))


async def _process_single_user(
    db: Any,
    doc: Any,
    researcher: NewsResearcherAgent,
    now_utc: datetime,
) -> Optional[str]:
  """Concurrently processes news summary and email dispatch for a single user."""
  doc_ref = doc.reference
  user_data = doc.to_dict()
  user_email = user_data.get("email", doc.id)
  user_topic = user_data.get("topic", "General News")
  user_tz = user_data.get("timezone", "UTC")
  current_trigger = user_data.get("next_trigger_utc")

  # Atomic claim transaction lock
  claimed = db.run_transaction(
      lambda tx: claim_user_transaction(tx, doc_ref, now_utc)
  )
  if not claimed:
    return None

  success = False
  try:
    cutoff_date = get_user_local_cutoff_date(user_tz, now_utc)
    raw_summary = researcher.run(
        topic=user_topic,
        search_date_cutoff=cutoff_date,
    )
    if not raw_summary or not raw_summary.strip():
      raw_summary = f"# Daily Top Digest: {user_topic}\n\nNo Major News Today."

    html_content = render_markdown_to_html(raw_summary)

    # Dispatch email asynchronously
    await send_news_email(
        recipient_email=user_email,
        topic=user_topic,
        html_content=html_content,
    )

    # Advance trigger and release lock
    next_trigger = advance_user_next_trigger_local(
        user_tz, current_trigger, now_utc
    )
    doc_ref.update({
        "status": "idle",
        "next_trigger_utc": next_trigger,
        "updated_at": now_utc,
    })
    success = True
    return user_email
  except Exception as e:
    print(f"[PIPELINE_FAILURE] Error processing user {user_email}: {e}")
    raise e
  finally:
    if not success:
      try:
        doc_ref.update({"status": "idle", "updated_at": now_utc})
      except Exception as cleanup_err:
        print(
            f"[CLEANUP_ERROR] Failed to reset status for {user_email}:"
            f" {cleanup_err}"
        )


async def run_daily_pipeline(
    db: Any = None, now_utc: Optional[datetime] = None
) -> Dict[str, Any]:
  """Executes daily pipeline concurrently for all due users."""
  # Ensure UTC timezone awareness on timestamp
  if now_utc is None:
    now_utc = datetime.now(timezone.utc)
  elif now_utc.tzinfo is None:
    now_utc = now_utc.replace(tzinfo=timezone.utc)

  stale_cleaned = []
  if db is not None:
    try:
      # Step 1: Bounded stale lock cleanup
      stale_cleaned = cleanup_stale_locks(db, now_utc)
    except Exception as e:
      print(f"[FIRESTORE_WARNING] Stale lock cleanup skipped: {e}")
      db = None

  # Return summary early if DB client is unattached or database is uninitialized
  if db is None:
    return {
        "status": "completed",
        "stale_cleaned_count": len(stale_cleaned),
        "processed_count": 0,
        "failed_count": 0,
        "note": "Firestore database unavailable or uninitialized.",
    }

  due_docs = []
  try:
    # Step 2: Query due idle users (active == True AND status == "idle" AND next_trigger_utc <= now_utc) capped with .limit(50)
    due_query = (
        db.collection("users")
        .where("active", "==", True)
        .where("status", "==", "idle")
        .where("next_trigger_utc", "<=", now_utc)
        .limit(50)
    )
    due_docs = due_query.get()
  except Exception as e:
    print(f"[FIRESTORE_WARNING] Due user query skipped: {e}")
    due_docs = []

  # Instantiate NewsResearcherAgent instance
  researcher = NewsResearcherAgent()

  # Create tasks for all due users to run concurrently
  tasks = [
      _process_single_user(db, doc, researcher, now_utc)
      for doc in due_docs
  ]

  # Run all tasks concurrently
  results = await asyncio.gather(*tasks, return_exceptions=True)

  processed_users = []
  failed_users = []

  for doc, res in zip(due_docs, results):
    user_email = doc.to_dict().get("email", doc.id)
    if isinstance(res, Exception):
      failed_users.append(user_email)
    elif res is not None:
      processed_users.append(res)

  # Return operational summary dictionary
  return {
      "status": "completed",
      "stale_cleaned_count": len(stale_cleaned),
      "processed_count": len(processed_users),
      "failed_count": len(failed_users),
      "processed_users": processed_users,
      "failed_users": failed_users,
  }
