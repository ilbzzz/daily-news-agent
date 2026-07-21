# Daily Top News Summary AI Agent

An automated AI agent built with Google Agent Development Kit (ADK) that
delivers a personalized top news summary to user inboxes every morning at 8:00
AM local time.

## Overview

-   **User Onboarding & HITL**: Interactive onboarding collecting email, topic,
    and timezone with thread-safe IANA timezone normalization and proactive HITL
    confirmation.
-   **Resilient Pipeline**: 30-minute Cloud Scheduler trigger processing due
    users with atomic claim transactions, bounded stale lock cleanup
    (`.limit(50)`), and DST-aware schedule advancement.
-   **Email Delivery**: High-reliability SendGrid REST API integration with
    diagnostic error logging and dry-run support.

## Environment Variables

Set the following environment variables before running:

-   `SENDGRID_API_KEY`: API key for SendGrid (Required in production mode).
-   `SENDGRID_SENDER_EMAIL`: Sender email address (Default:
    `digest@newsagent.ai`).
-   `DRY_RUN_MODE`: Set to `true` for local testing without sending actual
    emails.
-   `FIRESTORE_COLLECTION`: Firestore collection name (Default: `users`).
-   `GCP_PROJECT`: GCP Project ID.

## Quickstart & Local Testing

### 1. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .[dev]
```

### 2. Run Unit Tests

```bash
pytest
```

### 3. Run Agent Locally

```bash
export DRY_RUN_MODE=true
agents-cli run
```
