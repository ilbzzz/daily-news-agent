"""Tools and helpers for email validation, sanitization, and dispatch with retry logic."""

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict

try:
    from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
except ImportError:
    retry = None

from app.config import DRY_RUN_MODE, SENDGRID_API_KEY, SENDGRID_SENDER_EMAIL

# RFC 5322 regex pattern for email validation
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def validate_and_sanitize_email(raw_email: str) -> str:
    """Validates and sanitizes email address against RFC 5322 regex and header injection.

    Args:
        raw_email: Raw email string input.

    Returns:
        Stripped, lowercase valid email string.

    Raises:
        ValueError: If email contains header injection characters or fails regex validation.
    """
    if not raw_email or not isinstance(raw_email, str):
        raise ValueError("Email must be a non-empty string.")

    # Block header injection characters
    if "\r" in raw_email or "\n" in raw_email:
        raise ValueError("Invalid email: Header injection characters detected.")

    cleaned = raw_email.strip()
    if not EMAIL_REGEX.match(cleaned):
        raise ValueError(f"Invalid email format: '{raw_email}'.")

    return cleaned.lower()


def _is_retriable_http_error(exception: Exception) -> bool:
    """Returns True if the exception is an HTTP error that should be retried (429 or 5xx)."""
    if isinstance(exception, urllib.error.HTTPError):
        return exception.code == 429 or (500 <= exception.code < 600)
    return False


def _send_grid_request(recipient_email: str, topic: str, html_content: str) -> Dict[str, Any]:
    """Internal HTTP request execution for SendGrid mail send."""
    api_key = os.environ.get("SENDGRID_API_KEY", SENDGRID_API_KEY)
    sender_email = os.environ.get("SENDGRID_SENDER_EMAIL", SENDGRID_SENDER_EMAIL)
    is_dry_run = os.environ.get("DRY_RUN_MODE", str(DRY_RUN_MODE)).lower() == "true"

    if not api_key:
        if is_dry_run:
            print(f"[DRY_RUN] Email to {recipient_email} regarding '{topic}' logged.")
            return {"status": "dry_run_success"}
        raise RuntimeError("Missing required environment variable 'SENDGRID_API_KEY' in production mode.")

    payload = {
        "personalizations": [{"to": [{"email": recipient_email}]}],
        "from": {"email": sender_email, "name": "Daily News Agent"},
        "subject": f"Daily Top Digest: {topic}",
        "content": [{"type": "text/html", "value": html_content}],
    }

    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as response:
            return {"status": "success"}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        print(f"[SENDGRID_ERROR] HTTP {e.code}: {error_body}")
        if e.code in (401, 403):
            raise RuntimeError(f"Fatal SendGrid Auth Error (HTTP {e.code}): {error_body}")
        elif e.code == 429:
            retry_header = e.headers.get("Retry-After", "2")
            try:
                retry_after = int(retry_header)
            except ValueError:
                retry_after = 2
            print(f"[RATE_LIMIT] SendGrid rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
        raise e


def send_news_email(recipient_email: str, topic: str, html_content: str, max_retries: int = 3) -> Dict[str, Any]:
    """Sends news digest email via SendGrid REST API with retries for HTTP 429/5xx.

    Args:
        recipient_email: Validated recipient email address.
        topic: Topic of the news digest.
        html_content: Rendered HTML body content.
        max_retries: Maximum number of retry attempts for transient errors.

    Returns:
        Dict indicating dispatch status.

    Raises:
        RuntimeError: For authentication failures (401/403) or missing API key in production mode.
        urllib.error.HTTPError: If max retries are exhausted.
    """
    attempt = 0
    while True:
        try:
            return _send_grid_request(recipient_email, topic, html_content)
        except urllib.error.HTTPError as e:
            attempt += 1
            if _is_retriable_http_error(e) and attempt < max_retries:
                backoff = 2 ** attempt
                print(f"[RETRY] Attempt {attempt}/{max_retries} failed with HTTP {e.code}. Retrying in {backoff}s...")
                time.sleep(backoff)
                continue
            raise e
