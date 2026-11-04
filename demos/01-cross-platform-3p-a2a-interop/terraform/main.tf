data "google_project" "current" {
  project_id = var.project_id
}

locals {
  effective_issuer_uri = var.wif_issuer_uri != "" ? var.wif_issuer_uri : var.external_3p_agent_url
  required_apis = [
    "aiplatform.googleapis.com",
    "agentregistry.googleapis.com",
    "agentidentity.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "networkservices.googleapis.com",
    "networksecurity.googleapis.com",
    "modelarmor.googleapis.com",
    "iap.googleapis.com",
  ]
}

resource "google_project_service" "required" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ==============================================================================
# 1. Dedicated Least-Privilege Service Account for Agent Runtime / Staging
# ==============================================================================
resource "google_service_account" "agent_runtime_sa" {
  project      = var.project_id
  account_id   = "adk-interop-runtime-sa"
  display_name = "ADK Cross-Platform Interop Runtime Service Account"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "runtime_sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/agentregistry.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.agent_runtime_sa.email}"
}

resource "google_storage_bucket" "agent_staging" {
  name                        = "${var.project_id}-agent-runtime-staging"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

resource "google_storage_bucket_iam_member" "runtime_sa_staging_admin" {
  bucket = google_storage_bucket.agent_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_runtime_sa.email}"
}

# ==============================================================================
# 2. Direction 1 (Inbound): IAM Workload Identity Federation (WIF)
#    Allows the external 3P Agent to exchange its native OIDC JWT at
#    sts.googleapis.com (RFC 8693) for a short-lived Google OAuth token.
# ==============================================================================
resource "google_iam_workload_identity_pool" "external_3p_pool" {
  project                   = var.project_id
  workload_identity_pool_id = var.wif_pool_id
  display_name              = "External 3P Agent Pool"
  description               = "Federates external 3P SaaS/Agent identities into GCP Agent Runtime without static keys."
  depends_on                = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "external_3p_oidc" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.external_3p_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = var.wif_provider_id
  display_name                       = "External 3P OIDC Provider"
  description                        = "OIDC provider trusting the external 3P agent platform."

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.agent_role" = "assertion.agent_role"
  }

  attribute_condition = "assertion.sub == '${var.wif_allowed_subject}'"

  oidc {
    issuer_uri = local.effective_issuer_uri
    jwks_json  = var.wif_jwks_json != "" ? var.wif_jwks_json : null
  }
}

# Grant the federated 3P principal permission to query Vertex AI Reasoning Engines
resource "google_project_iam_member" "federated_3p_agent_invoker" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.external_3p_pool.name}/subject/${var.wif_allowed_subject}"
}

# ==============================================================================
# 3. Direction 2 (Outbound): Agent Registry + Auth Manager + Credential Binding
#    Registers the external 3P A2A agent in Agent Registry and binds credentials
#    so the GCP ADK Orchestrator resolves endpoint + auth dynamically at runtime.
# ==============================================================================
resource "google_agent_registry_service" "external_3p_agent" {
  provider     = google-beta
  project      = var.project_id
  location     = var.region
  service_id   = var.external_3p_agent_id
  display_name = "External 3P Course & Support Agent"
  description  = "External 3P A2A agent hosted on Hugging Face Spaces / SaaS runtime."

  # Note: When agent_spec.type = "A2A_AGENT_CARD", top-level `interfaces` must be
  # omitted—Agent Registry extracts connection details directly from the Agent Card JSON.
  agent_spec {
    type = "A2A_AGENT_CARD"
    content = jsonencode({
      name                = var.external_3p_agent_id
      description         = "Handles external LMS course roster lookups, syllabus checks, and student support tickets."
      url                 = "${trimsuffix(var.external_3p_agent_url, "/")}/a2a"
      version             = "1.0.0"
      preferredTransport  = "JSONRPC"
      protocolVersion     = "0.3.0"
      defaultInputModes   = ["text/plain"]
      defaultOutputModes  = ["text/plain"]
      capabilities        = {
        streaming = false
      }
      skills = [
        {
          id          = "course_syllabus_lookup"
          name        = "course_syllabus_lookup"
          description = "Looks up course syllabus, grading policy, and assignment deadlines from the 3P LMS."
          tags        = ["lms", "courses", "syllabus"]
        },
        {
          id          = "student_support_ticket"
          name        = "student_support_ticket"
          description = "Creates or checks status of a student support ticket in the external 3P platform."
          tags        = ["lms", "support", "tickets"]
        }
      ]
    })
  }

  depends_on = [google_project_service.required]
}

resource "google_agent_identity_auth_provider" "external_3p_auth" {
  provider         = google-beta
  project          = var.project_id
  location         = var.region
  auth_provider_id = "${var.external_3p_agent_id}-auth"
  description      = "Auth Manager provider storing outbound credentials for the external 3P A2A agent."

  auth_provider_type_params {
    api_key {
      api_key = var.external_3p_api_token
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_agent_registry_binding" "external_3p_binding" {
  count        = var.source_agent_urn != "" ? 1 : 0
  provider     = google-beta
  project      = var.project_id
  location     = var.region
  binding_id   = "${var.external_3p_agent_id}-binding"
  display_name = "GCP Orchestrator to External 3P Agent Auth Binding"
  description  = "Binds the GCP Agent Runtime orchestrator to the external 3P agent and Auth Manager provider."

  source {
    identifier = var.source_agent_urn
  }

  target {
    identifier = join(":", [
      "urn",
      "agent",
      "projects-${data.google_project.current.number}",
      "projects",
      data.google_project.current.number,
      "locations",
      var.region,
      "agentregistry",
      "services",
      google_agent_registry_service.external_3p_agent.service_id,
    ])
  }

  auth_provider_binding {
    auth_provider = google_agent_identity_auth_provider.external_3p_auth.id
  }

  depends_on = [
    google_agent_registry_service.external_3p_agent,
    google_agent_identity_auth_provider.external_3p_auth,
  ]
}

# ==============================================================================
# 4. Dual-Mode Agent Gateway + Model Armor + IAM Unified Access Policy (Optional)
#    Set enable_agent_gateway = true to provision CLIENT_TO_AGENT (Ingress) and
#    AGENT_TO_ANYWHERE (Egress) gateways with Model Armor and IAP UAP governance.
# ==============================================================================
resource "google_network_services_agent_gateway" "ingress_client_to_agent" {
  count       = var.enable_agent_gateway ? 1 : 0
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  name        = "agent-client-ingress-gw"
  description = "CLIENT_TO_AGENT ingress gateway governing external 3P calls into Agent Runtime."

  google_managed {
    governed_access_path = "CLIENT_TO_AGENT"
  }

  depends_on = [google_project_service.required]
}

resource "google_network_services_agent_gateway" "egress_agent_to_anywhere" {
  count       = var.enable_agent_gateway ? 1 : 0
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  name        = "agent-external-egress-gw"
  description = "AGENT_TO_ANYWHERE egress gateway governing outbound calls from Agent Runtime to 3P agents."

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.region}"
  ]

  depends_on = [google_project_service.required]
}

resource "google_model_armor_template" "agent_safety_guardrails" {
  count       = var.enable_agent_gateway ? 1 : 0
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  template_id = "cross-platform-agent-guardrails"

  filter_config {
    pi_and_jailbreak_filter_settings {
      confidence_level   = "MEDIUM_AND_ABOVE"
      filter_enforcement = "ENABLED"
    }
    rai_settings {
      rai_filters {
        confidence_level = "MEDIUM_AND_ABOVE"
        filter_type      = "DANGEROUS"
      }
      rai_filters {
        confidence_level = "MEDIUM_AND_ABOVE"
        filter_type      = "HARASSMENT"
      }
      rai_filters {
        confidence_level = "MEDIUM_AND_ABOVE"
        filter_type      = "HATE_SPEECH"
      }
      rai_filters {
        confidence_level = "MEDIUM_AND_ABOVE"
        filter_type      = "SEXUALLY_EXPLICIT"
      }
    }
  }

  template_metadata {
    ignore_partial_invocation_failures = false
    log_sanitize_operations            = true
    log_template_operations            = true
  }

  depends_on = [google_project_service.required]
}

# Critical Day-2 Safeguard: Explicitly allowlist essential Google APIs (both
# standard and .mtls.googleapis.com) on the AGENT_TO_ANYWHERE gateway so the
# Agent Runtime container does not lock itself out with HTTP 498.
resource "google_network_security_authz_policy" "egress_essential_gcp_hosts" {
  count          = var.enable_agent_gateway ? 1 : 0
  provider       = google-beta
  project        = var.project_id
  location       = var.region
  name           = "egress-essential-gcp-hosts"
  action         = "ALLOW"
  policy_profile = "REQUEST_AUTHZ"

  target {
    resources = [google_network_services_agent_gateway.egress_agent_to_anywhere[0].id]
  }

  http_rules {
    to {
      operations {
        hosts {
          exact       = "aiplatform.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "${var.region}-aiplatform.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "${var.region}-aiplatform.mtls.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "agentregistry.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "agentregistry.mtls.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "cloudresourcemanager.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "cloudresourcemanager.mtls.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "logging.googleapis.com"
          ignore_case = false
        }
        hosts {
          exact       = "storage.googleapis.com"
          ignore_case = false
        }
      }
    }
  }
}

resource "google_iam_project_access_policy" "agent_egress_uap" {
  count            = var.enable_agent_gateway ? 1 : 0
  provider         = google-beta
  project          = var.project_id
  location         = "global"
  access_policy_id = "allow-3p-agent-egress-uap"
  display_name     = "Allow Agent Runtime Egress to Registered 3P Agent"

  details {
    rules {
      effect      = "ALLOW"
      description = "Allow attested Agent Runtime instances in this project to egress to the registered 3P A2A agent."

      principals = [
        "principalSet://agents.global.org-${var.organization_id}.system.id.goog/attribute.platformContainer/aiplatform/projects/${data.google_project.current.number}"
      ]

      operation {
        permissions = ["iap.googleapis.com/resources.egressViaIAP"]
      }

      conditions {
        service    = "iap.googleapis.com"
        expression = "destination.agent_registry.service.name == 'projects/${var.project_id}/locations/${var.region}/services/${google_agent_registry_service.external_3p_agent.service_id}'"
      }
    }
  }

  depends_on = [google_agent_registry_service.external_3p_agent]
}

resource "google_iam_projects_policy_binding" "agent_egress_uap_binding" {
  count             = var.enable_agent_gateway ? 1 : 0
  provider          = google-beta
  project           = var.project_id
  location          = "global"
  policy_binding_id = "allow-3p-agent-egress-binding"
  policy_kind       = "ACCESS"
  policy            = "projects/${var.project_id}/locations/global/accessPolicies/${google_iam_project_access_policy.agent_egress_uap[0].access_policy_id}"

  target {
    resource = "//cloudresourcemanager.googleapis.com/projects/${var.project_id}"
  }
}
