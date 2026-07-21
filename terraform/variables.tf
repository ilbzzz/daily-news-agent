variable "gcp_project" {
  type        = string
  description = "The GCP project ID to deploy resources into."
}

variable "gcp_region" {
  type        = string
  default     = "us-central1"
  description = "The GCP region for deployment."
}

variable "scheduler_cron" {
  type        = string
  default     = "*/30 * * * *"
  description = "Cron expression for triggering the daily news pipeline."
}

variable "cloud_run_endpoint" {
  type        = string
  default     = "https://daily-news-agent-runner.a.run.app/run-pipeline"
  description = "Target Cloud Run endpoint URL triggered by Cloud Scheduler."
}
