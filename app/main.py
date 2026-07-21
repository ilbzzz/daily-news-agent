"""Cloud Run HTTP Web Service Entrypoint for Daily Top News Summary AI Agent."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from app.pipeline import run_daily_pipeline

try:
  from google.cloud import firestore
except ImportError:
  firestore = None


class NewsAgentRequestHandler(BaseHTTPRequestHandler):
  """HTTP Request Handler exposing endpoints for Cloud Scheduler trigger and health checks."""

  def _set_headers(
      self, status_code: int = 200, content_type: str = "application/json"
  ):
    """Sets HTTP response status code and headers."""
    self.send_response(status_code)
    self.send_header("Content-Type", content_type)
    self.end_headers()

  def do_GET(self):
    """Health check and A2A Agent Card metadata endpoint."""
    # Handle GET /.well-known/agent-card.json requests for A2A registration
    if self.path in ("/.well-known/agent-card.json", "/agent-card.json"):
      self._set_headers(200)
      host_header = self.headers.get("Host", "")
      base_url = (
          f"https://{host_header}"
          if host_header
          else "https://daily-news-agent-runner.a.run.app"
      )
      card_payload = {
          "protocolVersion": "1.0",
          "protocol_version": "1.0",
          "name": "Daily Top News Summary AI Agent",
          "description": (
              "Personalized daily news digest agent delivering 8:00 AM"
              " summaries."
          ),
          "version": "1.0.0",
          "url": base_url,
          "preferredTransport": "JSONRPC",
          "defaultInputModes": ["text/plain", "application/json"],
          "defaultOutputModes": ["application/json", "text/plain"],
          "capabilities": {"streaming": False},
          "skills": [{
              "id": "run_daily_pipeline",
              "name": "Run Daily Pipeline",
              "description": (
                  "Triggers news collection, takeaway synthesis, and email"
                  " dispatch."
              ),
          }],
      }
      self.wfile.write(json.dumps(card_payload).encode("utf-8"))
    # Handle GET /healthz and /healthcheck requests
    elif self.path in ("/", "/healthz", "/healthcheck"):
      self._set_headers(200)
      # Return JSON health status
      self.wfile.write(
          json.dumps(
              {"status": "healthy", "service": "daily_news_agent"}
          ).encode("utf-8")
      )
    else:
      # Return 404 for unrecognized GET paths
      self._set_headers(404)
      self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))

  def do_POST(self):
    """Cloud Scheduler pipeline execution trigger endpoint."""
    # Handle POST /run-pipeline triggered by Cloud Scheduler
    if self.path in ("/run-pipeline", "/"):
      try:
        # Initialize Firestore client if library is present
        db = firestore.Client() if firestore is not None else None

        # Execute daily pipeline run
        results = run_daily_pipeline(db=db)

        # Write HTTP 200 OK JSON response
        self._set_headers(200)
        self.wfile.write(json.dumps(results).encode("utf-8"))
      except Exception as e:
        # Return HTTP 500 Internal Server Error if pipeline encounters an unhandled exception
        self._set_headers(500)
        self.wfile.write(
            json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
        )
    else:
      # Return 404 for unrecognized POST paths
      self._set_headers(404)
      self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))


def run_server(port: int = 8080):
  """Starts the HTTP server listening on specified port."""
  # Bind HTTP server to all network interfaces on specified port
  server_address = ("", port)
  httpd = HTTPServer(server_address, NewsAgentRequestHandler)
  print(f"Daily News Agent HTTP Server listening on port {port}...")
  # Start listening loop
  httpd.serve_forever()


if __name__ == "__main__":
  # Read PORT environment variable provided by Cloud Run (default 8080)
  port = int(os.environ.get("PORT", "8080"))
  run_server(port)
