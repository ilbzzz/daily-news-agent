"""Cloud Run HTTP Web Service Entrypoint for Daily Top News Summary AI Agent."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from app.pipeline import run_daily_pipeline

try:
    from google.cloud import firestore
except ImportError:
    firestore = None


class NewsAgentRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler exposing endpoints for Cloud Scheduler trigger and health checks."""

    def _set_headers(self, status_code: int = 200, content_type: str = "application/json"):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.end_headers()

    def do_GET(self):
        """Health check endpoint."""
        if self.path in ("/", "/healthz", "/healthcheck"):
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "healthy", "service": "daily_news_agent"}).encode("utf-8"))
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))

    def do_POST(self):
        """Cloud Scheduler pipeline execution trigger endpoint."""
        if self.path in ("/run-pipeline", "/"):
            try:
                db = firestore.Client() if firestore is not None else None
                results = run_daily_pipeline(db=db)
                self._set_headers(200)
                self.wfile.write(json.dumps(results).encode("utf-8"))
            except Exception as e:
                self._set_headers(500)
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))


def run_server(port: int = 8080):
    """Starts the HTTP server listening on specified port."""
    server_address = ("", port)
    httpd = HTTPServer(server_address, NewsAgentRequestHandler)
    print(f"Daily News Agent HTTP Server listening on port {port}...")
    httpd.serve_forever()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    run_server(port)
