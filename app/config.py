"""Centralized configuration for Daily Top News Summary AI Agent."""

import os

SENDGRID_API_KEY: str = os.environ.get("SENDGRID_API_KEY", "")
SENDGRID_SENDER_EMAIL: str = os.environ.get(
    "SENDGRID_SENDER_EMAIL", "digest@newsagent.ai"
)
DRY_RUN_MODE: bool = os.environ.get("DRY_RUN_MODE", "false").lower() == "true"
FIRESTORE_COLLECTION: str = os.environ.get("FIRESTORE_COLLECTION", "users")
GCP_PROJECT: str = os.environ.get("GCP_PROJECT", "default-gcp-project")
