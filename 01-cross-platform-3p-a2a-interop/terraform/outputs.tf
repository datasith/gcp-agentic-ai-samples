output "project_number" {
  description = "Numeric Google Cloud project number."
  value       = data.google_project.current.number
}

output "staging_bucket" {
  description = "GCS staging bucket for Vertex AI Agent Runtime deployments."
  value       = "gs://${google_storage_bucket.agent_staging.name}"
}

output "wif_provider_resource_name" {
  description = "Full IAM Workload Identity Pool Provider resource name used as the audience in RFC 8693 STS token exchange."
  value       = google_iam_workload_identity_pool_provider.external_3p_oidc.name
}

output "federated_3p_principal" {
  description = "Federated principal URI representing the authenticated 3P agent in GCP IAM."
  value       = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.external_3p_pool.name}/subject/${var.wif_allowed_subject}"
}

output "registered_3p_service_name" {
  description = "Agent Registry Service resource name for the external 3P agent."
  value       = google_agent_registry_service.external_3p_agent.name
}

output "auth_provider_resource_name" {
  description = "Agent Identity Auth Manager Provider resource name."
  value       = google_agent_identity_auth_provider.external_3p_auth.id
}

output "ingress_agent_gateway_id" {
  description = "CLIENT_TO_AGENT Agent Gateway resource ID (when enable_agent_gateway = true)."
  value       = var.enable_agent_gateway ? google_network_services_agent_gateway.ingress_client_to_agent[0].id : null
}

output "egress_agent_gateway_id" {
  description = "AGENT_TO_ANYWHERE Agent Gateway resource ID (when enable_agent_gateway = true)."
  value       = var.enable_agent_gateway ? google_network_services_agent_gateway.egress_agent_to_anywhere[0].id : null
}
