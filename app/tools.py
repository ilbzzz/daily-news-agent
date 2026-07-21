"""Tools and helpers for email validation, sanitization, and dispatch with retry logic."""

import asyncio
import json
import os
import re
from typing import Any, Dict
import aiohttp

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
      ValueError: If email contains header injection characters or fails regex
      validation.
  """
  if not raw_email or not isinstance(raw_email, str):
    raise ValueError("Email must be a non-empty string.")

  # Block header injection characters (CRLF) to prevent email header manipulation
  if "\r" in raw_email or "\n" in raw_email:
    raise ValueError("Invalid email: Header injection characters detected.")

  # Strip leading/trailing whitespace and validate against RFC 5322 regex
  cleaned = raw_email.strip()
  if not EMAIL_REGEX.match(cleaned):
    raise ValueError(f"Invalid email format: '{raw_email}'.")

  # Return normalized lowercase email
  return cleaned.lower()


async def _send_grid_request(
    session: aiohttp.ClientSession, recipient_email: str, topic: str, html_content: str
) -> Dict[str, Any]:
  """Internal HTTP request execution for SendGrid mail send."""
  api_key = os.environ.get("SENDGRID_API_KEY", SENDGRID_API_KEY)
  if api_key and api_key.startswith("${"):
    api_key = ""

  sender_email = os.environ.get("SENDGRID_SENDER_EMAIL", SENDGRID_SENDER_EMAIL)
  if sender_email and sender_email.startswith("${"):
    sender_email = ""

  dry_run_env = os.environ.get("DRY_RUN_MODE")
  if dry_run_env and dry_run_env.startswith("${"):
    dry_run_env = None

  is_dry_run = (
      (dry_run_env or str(DRY_RUN_MODE)).lower() == "true"
  )

  # Check for API key configuration in production mode
  if not api_key:
    if is_dry_run:
      # Log email dispatch details in dry-run mode without sending external HTTP requests
      print(f"[DRY_RUN] Email to {recipient_email} regarding '{topic}' logged.")
      return {"status": "dry_run_success"}
    raise RuntimeError(
        "Missing required environment variable 'SENDGRID_API_KEY' in production"
        " mode."
    )

  # Construct SendGrid v3 Mail Send API payload
  payload = {
      "personalizations": [{"to": [{"email": recipient_email}]}],
      "from": {"email": sender_email, "name": "Daily News Agent"},
      "subject": f"Daily Top Digest: {topic}",
      "content": [{"type": "text/html", "value": html_content}],
  }

  headers = {
      "Authorization": f"Bearer {api_key}",
      "Content-Type": "application/json",
  }

  try:
    async with session.post(
        "https://api.sendgrid.com/v3/mail/send",
        json=payload,
        headers=headers,
    ) as response:
      if response.status in (200, 201, 202):
        return {"status": "success"}

      # Decode and log detailed SendGrid JSON error response body
      error_body = await response.text()
      print(f"[SENDGRID_ERROR] HTTP {response.status}: {error_body}")
      
      # Handle fatal authentication errors (401/403)
      if response.status in (401, 403):
        raise RuntimeError(
            f"Fatal SendGrid Auth Error (HTTP {response.status}): {error_body}"
        )
      # Handle rate limiting (429)
      elif response.status == 429:
        retry_header = response.headers.get("Retry-After", "2")
        raise aiohttp.ClientResponseError(
            request_info=response.request_info,
            history=response.history,
            status=response.status,
            message=f"Rate limited. Retry-After: {retry_header}",
            headers=response.headers,
        )
      else:
        response.raise_for_status()
  except aiohttp.ClientResponseError as e:
    raise e


async def send_news_email(
    recipient_email: str, topic: str, html_content: str, max_retries: int = 3
) -> Dict[str, Any]:
  """Sends news digest email via SendGrid REST API with retries for HTTP 429/5xx.

  Args:
      recipient_email: Validated recipient email address.
      topic: Topic of the news digest.
      html_content: Rendered HTML body content.
      max_retries: Maximum number of retry attempts for transient errors.

  Returns:
      Dict indicating dispatch status.

  Raises:
      RuntimeError: For authentication failures (401/403) or missing API key in
      production mode.
      aiohttp.ClientResponseError: If max retries are exhausted.
  """
  attempt = 0
  async with aiohttp.ClientSession() as session:
    while True:
      try:
        return await _send_grid_request(
            session=session,
            recipient_email=recipient_email,
            topic=topic,
            html_content=html_content,
        )
      except Exception as e:
        attempt += 1
        
        # Check if error is retriable (HTTP 429 or 5xx) and limit has not been reached
        is_retriable = False
        retry_delay = (2**attempt) * 2
        
        if isinstance(e, aiohttp.ClientResponseError):
          if e.status == 429:
            is_retriable = True
            # Try to extract custom retry delay from headers
            retry_header = e.headers.get("Retry-After") if e.headers else None
            try:
              if retry_header:
                retry_delay = int(retry_header)
            except ValueError:
              pass
            print(f"[RATE_LIMIT] SendGrid rate limited. Waiting {retry_delay}s...")
          elif 500 <= e.status < 600:
            is_retriable = True
            
        elif isinstance(e, aiohttp.ClientError):
          is_retriable = True

        if is_retriable and attempt <= max_retries:
          print(
              f"[RETRY] Attempt {attempt}/{max_retries} failed with retriable error: {e}. Retrying in {retry_delay}s..."
          )
          await asyncio.sleep(retry_delay)
          continue
        
        # Re-raise exception if non-retriable or max attempts exhausted
        raise e
