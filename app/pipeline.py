"""Daily pipeline execution logic including stale lock cleanup, atomic user claim, ADK state pre-population, HTML rendering, and email dispatch."""

from datetime import datetime, time, timedelta, timezone
import re
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

try:
    import markdown
except ImportError:
    markdown = None

try:
    import nh3
except ImportError:
    nh3 = None

from app.agent import NewsResearcherAgent, get_user_local_cutoff_date
from app.tools import send_news_email


def _fallback_markdown_to_html(raw_markdown: str) -> str:
    """Simple regex fallback converter from Markdown to HTML when markdown module is unavailable."""
    lines = raw_markdown.splitlines()
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue

        # Headers
        if stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{stripped[2:].strip()}</h1>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{stripped[3:].strip()}</h2>")
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{stripped[4:].strip()}</h3>")
        # Unordered list items
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            item_text = stripped[2:].strip()
            item_text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", item_text)
            item_text = re.sub(r"\[(.*?)\]\((.*?)\)", r'<a href="\2">\1</a>', item_text)
            html_lines.append(f"<li>{item_text}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            line_text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", stripped)
            line_text = re.sub(r"\[(.*?)\]\((.*?)\)", r'<a href="\2">\1</a>', line_text)
            html_lines.append(f"<p>{line_text}</p>")

    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)


def cleanup_stale_locks(db: Any, now_dt: Optional[datetime] = None) -> List[str]:
    """Finds users stuck in 'processing' status for > 30 minutes (limit 50) and resets them using batch write."""
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    stale_cutoff = now_dt - timedelta(minutes=30)
    cleaned_user_ids = []

    if db is not None:
        stale_docs = (
            db.collection("users")
            .where("status", "==", "processing")
            .where("updated_at", "<=", stale_cutoff)
            .limit(50)
            .get()
        )
        if stale_docs:
            batch = db.batch()
            for doc in stale_docs:
                batch.update(doc.reference, {"status": "idle", "updated_at": now_dt})
                cleaned_user_ids.append(doc.id)
            batch.commit()

    return cleaned_user_ids


def claim_user_transaction(transaction: Any, doc_ref: Any, now_dt: datetime) -> bool:
    """Atomic Firestore transaction to claim an idle user for processing."""
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists or snapshot.get("status") != "idle":
        return False
    transaction.update(doc_ref, {"status": "processing", "updated_at": now_dt})
    return True


def render_markdown_to_html(raw_markdown: str) -> str:
    """Converts Markdown text to sanitized HTML wrapped in a responsive email template."""
    if not raw_markdown or not raw_markdown.strip():
        raw_markdown = "# Daily Top Digest\n\nNo Major News Today."

    if markdown is not None:
        html_body = markdown.markdown(raw_markdown, extensions=["extra"])
    else:
        html_body = _fallback_markdown_to_html(raw_markdown)

    if nh3 is not None:
        clean_html = nh3.clean(html_body)
    else:
        clean_html = html_body

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


def advance_user_next_trigger_local(timezone_str: str, current_trigger_utc: Optional[datetime], now_utc: Optional[datetime] = None) -> datetime:
    """Advances next_trigger_utc to tomorrow's 8:00 AM local time, preventing backlog spam catch-up loops."""
    user_tz = ZoneInfo(timezone_str)
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    now_local = now_utc.astimezone(user_tz)

    if current_trigger_utc:
        if current_trigger_utc.tzinfo is None:
            current_trigger_utc = current_trigger_utc.replace(tzinfo=timezone.utc)
        curr_local = current_trigger_utc.astimezone(user_tz)
        # Protect against stale/past triggers: Always ensure target_date is tomorrow relative to now_local
        base_date = max(curr_local.date(), now_local.date())
        target_date = base_date + timedelta(days=1)
    else:
        target_date = now_local.date() + timedelta(days=1)

    next_local_8am = datetime.combine(target_date, time(8, 0, 0), tzinfo=user_tz)
    return next_local_8am.astimezone(ZoneInfo("UTC"))


def run_daily_pipeline(db: Any = None, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Executes daily pipeline: stale lock cleanup, querying due users, running researcher, HTML rendering, dispatch, and DST schedule advancement."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    # Step 1: Bounded stale lock cleanup (.limit(50))
    stale_cleaned = cleanup_stale_locks(db, now_utc)

    processed_users = []
    failed_users = []

    if db is None:
        return {
            "status": "completed",
            "stale_cleaned_count": len(stale_cleaned),
            "processed_count": 0,
            "failed_count": 0,
        }

    # Step 2: Query due idle users (.limit(50))
    due_query = (
        db.collection("users")
        .where("active", "==", True)
        .where("status", "==", "idle")
        .where("next_trigger_utc", "<=", now_utc)
        .limit(50)
    )
    due_docs = due_query.get()

    researcher = NewsResearcherAgent()

    for doc in due_docs:
        doc_ref = doc.reference
        user_data = doc.to_dict()
        user_email = user_data.get("email", doc.id)
        user_topic = user_data.get("topic", "General News")
        user_tz = user_data.get("timezone", "UTC")
        current_trigger = user_data.get("next_trigger_utc")

        # Step 3: Atomic claim lock
        transaction = db.transaction()
        claimed = claim_user_transaction(transaction, doc_ref, now_utc)
        if not claimed:
            continue

        success = False
        try:
            # Step 4: Pre-populate ADK session state
            cutoff_date = get_user_local_cutoff_date(user_tz, now_utc)
            session_state = {
                "topic": user_topic,
                "search_date_cutoff": cutoff_date,
            }

            # Step 5: Run NewsResearcherAgent
            raw_summary = researcher.run(
                topic=session_state["topic"],
                search_date_cutoff=session_state["search_date_cutoff"],
            )
            if not raw_summary or not raw_summary.strip():
                raw_summary = f"# Daily Top Digest: {user_topic}\n\nNo Major News Today."

            # Step 6: Post-run HTML conversion & sanitization
            html_content = render_markdown_to_html(raw_summary)

            # Step 7: Call send_news_email
            send_news_email(recipient_email=user_email, topic=user_topic, html_content=html_content)

            # Step 8: Advance next_trigger_utc by 1 local day and reset status to 'idle'
            next_trigger = advance_user_next_trigger_local(user_tz, current_trigger, now_utc)
            doc_ref.update({
                "status": "idle",
                "next_trigger_utc": next_trigger,
                "updated_at": now_utc,
            })
            success = True
            processed_users.append(user_email)
        except Exception as e:
            print(f"[PIPELINE_FAILURE] Error processing user {user_email}: {e}")
            failed_users.append(user_email)
        finally:
            if not success:
                # Failure recovery status release
                try:
                    doc_ref.update({"status": "idle", "updated_at": now_utc})
                except Exception as cleanup_err:
                    print(f"[CLEANUP_ERROR] Failed to reset status for {user_email}: {cleanup_err}")

    return {
        "status": "completed",
        "stale_cleaned_count": len(stale_cleaned),
        "processed_count": len(processed_users),
        "failed_count": len(failed_users),
        "processed_users": processed_users,
        "failed_users": failed_users,
    }
