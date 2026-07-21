terraform {
  required_version = ">= 1.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.50.0"
    }
  }
}

provider "google" {
  project = var.gcp_project
  region  = var.gcp_region
}

# Secret Manager for SendGrid API Key
resource "google_secret_manager_secret" "sendgrid_api_key" {
  secret_id = "sendgrid-api-key"
  replication {
    auto {}
  }
}

# Firestore Composite Indexes
resource "google_firestore_index" "user_due_index" {
  project    = var.gcp_project
  database   = "(default)"
  collection = "users"

  fields {
    field_path = "active"
    order      = "ASCENDING"
  }
  fields {
    field_path = "status"
    order      = "ASCENDING"
  }
  fields {
    field_path = "next_trigger_utc"
    order      = "ASCENDING"
  }
}

resource "google_firestore_index" "stale_lock_index" {
  project    = var.gcp_project
  database   = "(default)"
  collection = "users"

  fields {
    field_path = "status"
    order      = "ASCENDING"
  }
  fields {
    field_path = "updated_at"
    order      = "ASCENDING"
  }
}

# Cloud Scheduler 30-minute Trigger
resource "google_cloud_scheduler_job" "pipeline_cron" {
  name             = "daily-news-agent-pipeline-cron"
  description      = "Triggers daily news agent pipeline every 30 minutes"
  schedule         = var.scheduler_cron
  time_zone        = "UTC"
  attempt_deadline = "320s"

  http_target {
    http_method = "POST"
    uri         = var.cloud_run_endpoint
    oidc_token {
      service_account_email = var.scheduler_service_account_email
    }
  }
}
