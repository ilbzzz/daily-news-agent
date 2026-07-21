"""Unit tests for Cloud Run HTTP Server Entrypoint in app/main.py."""

from io import BytesIO
import json
import unittest
from unittest.mock import MagicMock

from app.main import NewsAgentRequestHandler


class DummyHTTPRequestHandler(NewsAgentRequestHandler):
  """Subclass of NewsAgentRequestHandler that bypasses socket I/O for unit testing."""

  def __init__(self, path: str, headers: dict = None):
    self.path = path
    self.headers = headers or {"Host": "daily-news-agent-runner-test.a.run.app"}
    self.rfile = BytesIO(b"")
    self.wfile = BytesIO()
    self.response_status = None
    self.response_headers = {}

  def send_response(self, code, message=None):
    self.response_status = code

  def send_header(self, keyword, value):
    self.response_headers[keyword] = value

  def end_headers(self):
    pass


class TestMainRequestHandler(unittest.TestCase):
  """Test suite for HTTP server request handler endpoints."""

  def test_healthz_endpoint(self):
    """Tests GET /healthz endpoint returns HTTP 200 with healthy JSON payload."""
    handler = DummyHTTPRequestHandler(path="/healthz")
    handler.do_GET()

    self.assertEqual(handler.response_status, 200)
    response_body = json.loads(handler.wfile.getvalue().decode("utf-8"))
    self.assertEqual(response_body, {"status": "healthy", "service": "daily_news_agent"})

  def test_agent_card_endpoint(self):
    """Tests GET /.well-known/agent-card.json endpoint returns valid A2A metadata payload."""
    handler = DummyHTTPRequestHandler(
        path="/.well-known/agent-card.json",
        headers={"Host": "daily-news-agent-runner-5qjit44poa-uc.a.run.app"},
    )
    handler.do_GET()

    self.assertEqual(handler.response_status, 200)
    card = json.loads(handler.wfile.getvalue().decode("utf-8"))

    # Verify required A2A protocol schema properties
    self.assertEqual(card["protocolVersion"], "1.0")
    self.assertEqual(card["name"], "Daily Top News Summary AI Agent")
    self.assertEqual(card["version"], "1.0.0")
    self.assertEqual(
        card["url"], "https://daily-news-agent-runner-5qjit44poa-uc.a.run.app"
    )
    self.assertIn("text/plain", card["defaultInputModes"])
    self.assertIn("application/json", card["defaultOutputModes"])
    self.assertEqual(card["capabilities"], {"streaming": False})
    self.assertGreater(len(card["skills"]), 0)
    self.assertEqual(card["skills"][0]["id"], "run_daily_pipeline")
    self.assertIn("news", card["skills"][0]["tags"])

  def test_not_found_endpoint(self):
    """Tests that unrecognized routes return HTTP 404."""
    handler = DummyHTTPRequestHandler(path="/invalid-route")
    handler.do_GET()

    self.assertEqual(handler.response_status, 404)


class TestDailyNewsAgent(unittest.TestCase):
  """Test suite for DailyNewsAgent conversational query method signature."""

  def test_query_polymorphic_arguments(self):
    """Verifies query() accepts prompt, input, message, and arbitrary kwargs without raising TypeError."""
    from app.agent import DailyNewsAgent

    agent = DailyNewsAgent()

    # 1. Test prompt argument
    res1 = agent.query(prompt="Subscribe test@example.com")
    self.assertIn("register", res1.lower())

    # 2. Test input argument (used by Vertex AI Reasoning Engine / agents-cli)
    res2 = agent.query(input="Register test@example.com")
    self.assertIn("register", res2.lower())

    # 3. Test message argument
    res3 = agent.query(message="Subscribe test@example.com")
    self.assertIn("register", res3.lower())

    # 4. Test structured dict input
    res4 = agent.query(input={"text": "Hello world"})
    self.assertIn("Daily News AI Agent received your request", res4)

    # 5. Test arbitrary kwargs
    res5 = agent.query(user_input="General question")
    self.assertIn("Daily News AI Agent received your request", res5)


if __name__ == "__main__":
  unittest.main()
