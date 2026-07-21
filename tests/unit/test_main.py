"""Unit tests for the FastAPI application in app/fast_api_app.py."""

from unittest.mock import AsyncMock, MagicMock, patch
import unittest
from fastapi.testclient import TestClient

# Patch GCP authentication and Logging before importing app to avoid network calls at import time
with (
    patch("google.auth.default", return_value=(None, "test-project")),
    patch("google.cloud.logging.Client"),
):
  from app.fast_api_app import app


class TestFastApiApp(unittest.TestCase):
  """Test suite for FastAPI route endpoints."""

  def setUp(self):
    self.client = TestClient(app)

  def test_healthz(self):
    """Tests GET /healthz endpoint returns HTTP 200."""
    response = self.client.get("/healthz")
    self.assertEqual(response.status_code, 200)

  @patch("app.fast_api_app.logger")
  def test_feedback_endpoint(self, mock_logger):
    """Tests that POST /feedback logs structured feedback and returns success."""
    payload = {
        "score": 5,
        "comments": "Great news digest!",
        "session_id": "session-123",
        "user_id": "user-456",
        "chat_history": []
    }
    response = self.client.post("/feedback", json=payload)
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json(), {"status": "success"})
    mock_logger.log_struct.assert_called_once()

  @patch("app.pipeline.run_daily_pipeline", new_callable=AsyncMock)
  def test_run_pipeline_endpoint(self, mock_run_daily_pipeline):
    """Tests that POST /run-pipeline invokes the daily pipeline runner and returns results."""
    mock_run_daily_pipeline.return_value = {
        "status": "completed",
        "stale_cleaned_count": 0,
        "processed_count": 2,
        "failed_count": 0,
        "processed_users": ["user1@example.com", "user2@example.com"],
        "failed_users": [],
    }

    response = self.client.post("/run-pipeline")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(
        response.json(),
        {
            "status": "completed",
            "stale_cleaned_count": 0,
            "processed_count": 2,
            "failed_count": 0,
            "processed_users": ["user1@example.com", "user2@example.com"],
            "failed_users": [],
        },
    )
    mock_run_daily_pipeline.assert_called_once()
