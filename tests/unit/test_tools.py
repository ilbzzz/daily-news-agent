"""Unit tests for validation, timezone normalization, and SendGrid email tools."""

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp

from app.timezone_utils import normalize_timezone
from app.tools import send_news_email, validate_and_sanitize_email


class TestTools(unittest.IsolatedAsyncioTestCase):
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

  async def test_send_news_email_dry_run(self):
    """Tests send_news_email behavior in DRY_RUN_MODE."""
    with patch.dict(
        os.environ, {"DRY_RUN_MODE": "true", "SENDGRID_API_KEY": ""}
    ):
      result = await send_news_email("user@example.com", "AI", "<h1>Digest</h1>")
      self.assertEqual(result["status"], "dry_run_success")

  async def test_send_news_email_missing_api_key_prod(self):
    """Tests that missing API key in production mode raises RuntimeError."""
    with patch.dict(
        os.environ, {"DRY_RUN_MODE": "false", "SENDGRID_API_KEY": ""}
    ):
      with self.assertRaises(RuntimeError):
        await send_news_email("user@example.com", "AI", "<h1>Digest</h1>")

  async def test_send_news_email_auth_error_401(self):
    """Tests that SendGrid 401 Unauthorized raises RuntimeError."""
    mock_response = MagicMock()
    mock_response.status = 401
    mock_response.text = AsyncMock(return_value="Invalid API key")

    mock_post = MagicMock()
    mock_post.__aenter__.return_value = mock_response

    with (
        patch.dict(
            os.environ,
            {"DRY_RUN_MODE": "false", "SENDGRID_API_KEY": "SG.invalid_key"},
        ),
        patch("aiohttp.ClientSession.post", return_value=mock_post),
    ):
      with self.assertRaises(RuntimeError):
        await send_news_email("user@example.com", "AI", "<h1>Digest</h1>")

  async def test_send_news_email_retry_on_429(self):
    """Tests that send_news_email retries on HTTP 429 rate limits."""
    mock_response_429 = MagicMock()
    mock_response_429.status = 429
    mock_response_429.headers = {"Retry-After": "1"}
    mock_response_429.text = AsyncMock(return_value="Rate limit exceeded")

    mock_post_429 = MagicMock()
    mock_post_429.__aenter__.return_value = mock_response_429

    mock_response_202 = MagicMock()
    mock_response_202.status = 202
    mock_response_202.text = AsyncMock(return_value="{}")

    mock_post_202 = MagicMock()
    mock_post_202.__aenter__.return_value = mock_response_202

    with (
        patch.dict(
            os.environ,
            {"DRY_RUN_MODE": "false", "SENDGRID_API_KEY": "SG.valid_key"},
        ),
        patch(
            "aiohttp.ClientSession.post",
            side_effect=[mock_post_429, mock_post_202],
        ) as mock_post_call,
        patch("asyncio.sleep") as mock_sleep,
    ):
      result = await send_news_email("user@example.com", "AI", "<h1>Digest</h1>")
      self.assertEqual(result["status"], "success")
      self.assertEqual(mock_post_call.call_count, 2)
      mock_sleep.assert_called_once_with(1)


if __name__ == "__main__":
  unittest.main()
