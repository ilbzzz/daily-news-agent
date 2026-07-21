"""Unit tests for validation, timezone normalization, and SendGrid email tools."""

import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from app.agent import normalize_timezone
from app.tools import send_news_email, validate_and_sanitize_email


class TestTools(unittest.TestCase):
  """Test suite for tools and validation helpers."""

  def test_validate_and_sanitize_email_valid(self):
    """Tests that valid emails are properly sanitized and lowercased."""
    self.assertEqual(
        validate_and_sanitize_email(" User.Name+tag@Example.COM "),
        "user.name+tag@example.com",
    )
    self.assertEqual(
        validate_and_sanitize_email("alice@domain.co.uk"), "alice@domain.co.uk"
    )

  def test_validate_and_sanitize_email_header_injection(self):
    """Tests that header injection characters (CRLF) raise ValueError."""
    with self.assertRaises(ValueError):
      validate_and_sanitize_email(
          "user@example.com\r\nBcc: attacker@example.com"
      )

    with self.assertRaises(ValueError):
      validate_and_sanitize_email("user@example.com\nSubject: Hacked")

  def test_validate_and_sanitize_email_invalid_format(self):
    """Tests that invalid email formats raise ValueError."""
    with self.assertRaises(ValueError):
      validate_and_sanitize_email("not-an-email")

    with self.assertRaises(ValueError):
      validate_and_sanitize_email("@missing-user.com")

  def test_normalize_timezone_valid_and_aliases(self):
    """Tests timezone normalization for canonical names and common aliases."""
    self.assertEqual(normalize_timezone("est"), "America/New_York")
    self.assertEqual(normalize_timezone("PST"), "America/Los_Angeles")
    self.assertEqual(normalize_timezone("ist"), "Asia/Kolkata")
    self.assertEqual(normalize_timezone("America/Chicago"), "America/Chicago")

  def test_normalize_timezone_invalid(self):
    """Tests that invalid timezone strings raise ValueError."""
    with self.assertRaises(ValueError):
      normalize_timezone("Invalid/Timezone_Name")

  def test_send_news_email_dry_run(self):
    """Tests send_news_email behavior in DRY_RUN_MODE."""
    with patch.dict(
        os.environ, {"DRY_RUN_MODE": "true", "SENDGRID_API_KEY": ""}
    ):
      result = send_news_email("user@example.com", "AI", "<h1>Digest</h1>")
      self.assertEqual(result["status"], "dry_run_success")

  def test_send_news_email_missing_api_key_prod(self):
    """Tests that missing API key in production mode raises RuntimeError."""
    with patch.dict(
        os.environ, {"DRY_RUN_MODE": "false", "SENDGRID_API_KEY": ""}
    ):
      with self.assertRaises(RuntimeError):
        send_news_email("user@example.com", "AI", "<h1>Digest</h1>")

  def test_send_news_email_auth_error_401(self):
    """Tests that SendGrid 401 Unauthorized raises RuntimeError with decoded body."""
    error_body = json.dumps(
        {"errors": [{"message": "Invalid API key"}]}
    ).encode("utf-8")
    mock_error = urllib.error.HTTPError(
        url="https://api.sendgrid.com/v3/mail/send",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(error_body),
    )

    with (
        patch.dict(
            os.environ,
            {"DRY_RUN_MODE": "false", "SENDGRID_API_KEY": "SG.invalid_key"},
        ),
        patch("urllib.request.urlopen", side_effect=mock_error),
    ):
      with self.assertRaises(RuntimeError):
        send_news_email("user@example.com", "AI", "<h1>Digest</h1>")

  def test_send_news_email_retry_on_429(self):
    """Tests that send_news_email retries on HTTP 429 rate limits."""
    error_body = b"Rate limit exceeded"
    mock_error = urllib.error.HTTPError(
        url="https://api.sendgrid.com/v3/mail/send",
        code=429,
        msg="Too Many Requests",
        hdrs={"Retry-After": "1"},
        fp=io.BytesIO(error_body),
    )

    with (
        patch.dict(
            os.environ,
            {"DRY_RUN_MODE": "false", "SENDGRID_API_KEY": "SG.valid_key"},
        ),
        patch(
            "urllib.request.urlopen", side_effect=[mock_error, MagicMock()]
        ) as mock_urlopen,
        patch("time.sleep"),
    ):
      result = send_news_email("user@example.com", "AI", "<h1>Digest</h1>")
      self.assertEqual(result["status"], "success")
      self.assertEqual(mock_urlopen.call_count, 2)


if __name__ == "__main__":
  unittest.main()
