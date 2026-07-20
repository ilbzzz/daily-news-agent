output "cloud_scheduler_job_id" {
  value       = google_cloud_scheduler_job.pipeline_cron.id
  description = "The ID of the provisioned Cloud Scheduler cron job."
}

output "firestore_indexes" {
  value = [
    google_firestore_index.user_due_index.name,
    google_firestore_index.stale_lock_index.name,
  ]
  description = "Names of the provisioned Firestore composite indexes."
}

output "secret_manager_secret_id" {
  value       = google_secret_manager_secret.sendgrid_api_key.secret_id
  description = "Secret Manager secret ID for SendGrid API key."
}
