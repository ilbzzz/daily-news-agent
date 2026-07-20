"""Unit tests for daily pipeline execution, scheduling, claim transactions, and cleanup."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.agent import UserOnboardingAgent, calculate_next_local_8am_utc, get_user_local_cutoff_date
from app.pipeline import (
    advance_user_next_trigger_local,
    claim_user_transaction,
    cleanup_stale_locks,
    render_markdown_to_html,
    run_daily_pipeline,
)


class TestPipeline(unittest.TestCase):
    """Test suite for pipeline logic, scheduling math, claim transactions, and status releases."""

    def test_calculate_next_local_8am_utc_before_8am(self):
        """Tests that target is today 8:00 AM local if current time is before 8:00 AM."""
        # 7:00 AM EDT (11:00 UTC)
        now_utc = datetime(2026, 7, 20, 11, 0, 0, tzinfo=timezone.utc)
        next_utc = calculate_next_local_8am_utc("America/New_York", now_utc)
        # Target should be 8:00 AM EDT on 2026-07-20 = 12:00 UTC
        self.assertEqual(next_utc, datetime(2026, 7, 20, 12, 0, 0, tzinfo=ZoneInfo("UTC")))

    def test_calculate_next_local_8am_utc_after_8am(self):
        """Tests that target is tomorrow 8:00 AM local if current time is after 8:00 AM."""
        # 9:00 AM EDT (13:00 UTC)
        now_utc = datetime(2026, 7, 20, 13, 0, 0, tzinfo=timezone.utc)
        next_utc = calculate_next_local_8am_utc("America/New_York", now_utc)
        # Target should be 8:00 AM EDT on 2026-07-21 = 12:00 UTC on 2026-07-21
        self.assertEqual(next_utc, datetime(2026, 7, 21, 12, 0, 0, tzinfo=ZoneInfo("UTC")))

    def test_get_user_local_cutoff_date(self):
        """Tests that date cutoff is 24 hours prior in user's local timezone formatted as YYYY-MM-DD."""
        now_utc = datetime(2026, 7, 20, 14, 0, 0, tzinfo=timezone.utc)
        cutoff = get_user_local_cutoff_date("America/New_York", now_utc)
        self.assertEqual(cutoff, "2026-07-19")

    def test_advance_user_next_trigger_local_dst_preserving(self):
        """Tests that schedule advancement preserves local 8:00 AM across calendar days."""
        now_utc = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        current_trigger = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        next_trigger = advance_user_next_trigger_local("America/New_York", current_trigger, now_utc)
        self.assertEqual(next_trigger, datetime(2026, 7, 21, 12, 0, 0, tzinfo=ZoneInfo("UTC")))

    def test_advance_user_next_trigger_local_outage_backlog_prevention(self):
        """Tests that an outage in the past does not produce past target dates."""
        now_utc = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        # Stale trigger from 3 days ago
        stale_trigger = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
        next_trigger = advance_user_next_trigger_local("America/New_York", stale_trigger, now_utc)
        # Should be tomorrow (2026-07-21 12:00 UTC) rather than 2026-07-18
        self.assertEqual(next_trigger, datetime(2026, 7, 21, 12, 0, 0, tzinfo=ZoneInfo("UTC")))

    def test_render_markdown_to_html_formatting(self):
        """Tests markdown to sanitized HTML email template wrapper."""
        raw_md = "# Daily Top Digest: AI\n\n- **Breakthrough**: New model released. [Link](https://example.com)"
        html = render_markdown_to_html(raw_md)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Daily Top Digest", html)
        self.assertIn("New model released", html)
        self.assertIn('href="https://example.com"', html)

    def test_claim_user_transaction_success(self):
        """Tests atomic claim user transaction when user status is idle."""
        mock_transaction = MagicMock()
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.get.side_effect = lambda key: "idle" if key == "status" else None
        mock_doc_ref.get.return_value = mock_snapshot

        now_dt = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        claimed = claim_user_transaction(mock_transaction, mock_doc_ref, now_dt)

        self.assertTrue(claimed)
        mock_transaction.update.assert_called_once_with(mock_doc_ref, {"status": "processing", "updated_at": now_dt})

    def test_claim_user_transaction_already_processing(self):
        """Tests that atomic claim lock fails if status is already processing."""
        mock_transaction = MagicMock()
        mock_doc_ref = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.get.side_effect = lambda key: "processing" if key == "status" else None
        mock_doc_ref.get.return_value = mock_snapshot

        now_dt = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        claimed = claim_user_transaction(mock_transaction, mock_doc_ref, now_dt)

        self.assertFalse(claimed)
        mock_transaction.update.assert_not_called()

    def test_cleanup_stale_locks_batch(self):
        """Tests that cleanup_stale_locks resets processing users using batch commit."""
        mock_db = MagicMock()
        mock_doc = MagicMock()
        mock_doc.id = "stale_user@example.com"
        mock_doc.reference = "ref_stale"
        mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.get.return_value = [mock_doc]
        mock_batch = MagicMock()
        mock_db.batch.return_value = mock_batch

        now_dt = datetime(2026, 7, 20, 14, 0, 0, tzinfo=timezone.utc)
        cleaned = cleanup_stale_locks(mock_db, now_dt)

        self.assertEqual(cleaned, ["stale_user@example.com"])
        mock_batch.update.assert_called_once_with("ref_stale", {"status": "idle", "updated_at": now_dt})
        mock_batch.commit.assert_called_once()

    def test_hitl_send_now_suppression_guardrail(self):
        """Tests that HITL Send Now bumps trigger to tomorrow 8:00 AM local if requested within 2 hours of scheduled trigger."""
        agent = UserOnboardingAgent()
        now_utc = datetime(2026, 7, 20, 11, 0, 0, tzinfo=timezone.utc)
        scheduled_trigger = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

        profile = {
            "email": "user@example.com",
            "topic": "AI",
            "timezone": "America/New_York",
            "next_trigger_utc": scheduled_trigger,
            "updated_at": now_utc,
        }

        updated = agent.handle_hitl_send_now(profile, now_utc)
        # Tomorrow 8:00 AM EST (2026-07-21 12:00 UTC)
        expected_trigger = datetime(2026, 7, 21, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(updated["next_trigger_utc"], expected_trigger)

    def test_run_daily_pipeline_flow(self):
        """Tests end-to-end pipeline execution with mocked Firestore DB and send_news_email."""
        mock_db = MagicMock()
        mock_db.collection.return_value.where.return_value.where.return_value.limit.return_value.get.return_value = []

        mock_due_doc = MagicMock()
        mock_due_doc.id = "due_user@example.com"
        mock_due_doc.to_dict.return_value = {
            "email": "due_user@example.com",
            "topic": "Quantum Computing",
            "timezone": "America/New_York",
            "next_trigger_utc": datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc),
            "status": "idle",
            "active": True,
        }
        mock_db.collection.return_value.where.return_value.where.return_value.where.return_value.limit.return_value.get.return_value = [mock_due_doc]

        mock_transaction = MagicMock()
        mock_db.transaction.return_value = mock_transaction

        with patch("app.pipeline.claim_user_transaction", return_value=True), \
             patch("app.pipeline.send_news_email", return_value={"status": "dry_run_success"}) as mock_send:

            now_utc = datetime(2026, 7, 20, 12, 30, 0, tzinfo=timezone.utc)
            results = run_daily_pipeline(mock_db, now_utc)

            self.assertEqual(results["status"], "completed")
            self.assertEqual(results["processed_count"], 1)
            self.assertEqual(results["processed_users"], ["due_user@example.com"])
            mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
