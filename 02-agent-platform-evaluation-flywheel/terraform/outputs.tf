output "project_id" {
  description = "Configured Google Cloud project ID."
  value       = var.project_id
}

output "region" {
  description = "Configured region for Agent Runtime and Evaluations."
  value       = var.region
}

output "agent_runtime_service_account" {
  description = "Dedicated least-privilege Service Account email for Agent Runtime."
  value       = google_service_account.agent_runtime_sa.email
}

output "eval_artifacts_bucket_uri" {
  description = "Cloud Storage URI for Agent Platform Evaluation datasets, runs, and staging."
  value       = "gs://${google_storage_bucket.eval_artifacts.name}"
}

output "quality_drift_alert_policy_id" {
  description = "Cloud Monitoring Alert Policy resource name for OnlineEvaluator quality drift."
  value       = try(google_monitoring_alert_policy.agent_quality_drift[0].name, "")
}
