variable "project_id" {
  description = "Google Cloud Project ID hosting Agent Runtime, Agent Registry, and Agent Gateway."
  type        = string
}

variable "region" {
  description = "Google Cloud region for Agent Runtime, Agent Registry, and Agent Gateway (must be a specific region like us-central1, not 'us' or 'eu' multi-region)."
  type        = string
  default     = "us-central1"
}

variable "organization_id" {
  description = "Numeric Google Cloud Organization ID (used to construct the attested SPIFFE principalSet for Agent Identity)."
  type        = string
}

variable "external_3p_agent_id" {
  description = "Service ID for registering the external 3P agent in Agent Registry."
  type        = string
  default     = "external-3p-lms-agent"
}

variable "external_3p_agent_url" {
  description = "Public HTTPS base URL of the external 3P agent (for example, https://<user>-external-a2a-lms-agent.hf.space)."
  type        = string
}

variable "external_3p_api_token" {
  description = "Shared Bearer token or API key stored in Agent Identity Auth Manager for outbound calls from GCP Agent Runtime to the 3P agent."
  type        = string
  sensitive   = true
}

variable "wif_pool_id" {
  description = "Workload Identity Pool ID for authenticating inbound calls from the 3P platform."
  type        = string
  default     = "external-3p-agent-pool"
}

variable "wif_provider_id" {
  description = "Workload Identity Pool OIDC Provider ID."
  type        = string
  default     = "external-3p-oidc-provider"
}

variable "wif_issuer_uri" {
  description = "OIDC Issuer URI of the 3P platform (defaults to external_3p_agent_url, or set to https://token.actions.githubusercontent.com for GitHub Actions OIDC)."
  type        = string
  default     = ""
}

variable "wif_allowed_subject" {
  description = "Expected OIDC 'sub' claim of the 3P agent allowed to impersonate/invoke GCP Agent Runtime."
  type        = string
  default     = "agent:external-3p-lms-agent"
}

variable "wif_jwks_json" {
  description = "Optional inline JWKS JSON string for offline OIDC signature verification (leave empty to use standard /.well-known/openid-configuration discovery on wif_issuer_uri)."
  type        = string
  default     = ""
}

variable "source_agent_urn" {
  description = "Optional URN of the source GCP Agent Runtime agent for the Agent Registry Auth Binding (populated after initial agent deployment)."
  type        = string
  default     = ""
}

variable "enable_agent_gateway" {
  description = "Whether to provision dual-mode Agent Gateways (CLIENT_TO_AGENT + AGENT_TO_ANYWHERE), Model Armor guardrails, and IAM UAP egress policies."
  type        = bool
  default     = false
}
