locals {
  effective_bucket_name = var.eval_bucket_name != "" ? var.eval_bucket_name : "${var.project_id}-agent-eval-flywheel"

  required_apis = toset([
    "aiplatform.googleapis.com",
    "agentregistry.googleapis.com",
    "cloudtrace.googleapis.com",
    "telemetry.googleapis.com",
    "observability.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
}

resource "google_project_service" "enabled_apis" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# 1. Dedicated Least-Privilege Service Account for ADK Agent Runtime
# ---------------------------------------------------------------------------
resource "google_service_account" "agent_runtime_sa" {
  account_id   = var.runtime_sa_id
  display_name = "ADK FinOps Governance Agent Runtime SA (Evaluations Demo)"
  description  = "Least-privilege identity for ADK Agent Runtime with Vertex AI model invocation and OpenTelemetry trace/log export."
  depends_on   = [google_project_service.enabled_apis]
}

locals {
  runtime_sa_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/telemetry.tracesWriter",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ])
}

resource "google_project_iam_member" "agent_runtime_sa_roles" {
  for_each = local.runtime_sa_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.agent_runtime_sa.email}"
}

# ---------------------------------------------------------------------------
# 2. Cloud Storage Bucket for Evaluation Datasets, Runs, & OTel Payloads
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "eval_artifacts" {
  name                        = local.effective_bucket_name
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_storage_bucket_iam_member" "runtime_sa_bucket_access" {
  bucket = google_storage_bucket.eval_artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_runtime_sa.email}"
}

# Grant the Vertex AI P4SA access to read/write EvaluationRun and OnlineEvaluator artifacts
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service_identity" "aiplatform_p4sa" {
  provider   = google-beta
  project    = var.project_id
  service    = "aiplatform.googleapis.com"
  depends_on = [google_project_service.enabled_apis]
}

resource "google_storage_bucket_iam_member" "aiplatform_p4sa_bucket_access" {
  bucket     = google_storage_bucket.eval_artifacts.name
  role       = "roles/storage.objectAdmin"
  member     = "serviceAccount:${google_project_service_identity.aiplatform_p4sa.email}"
  depends_on = [google_project_iam_member.aiplatform_p4sa_observability]
}

# Allow OnlineEvaluator and managed EvaluationRuns (running as Vertex AI P4SA) to
# query Cloud Observability `_Trace` (`_AllSpans`), invoke ReasoningEngines, and write metrics
locals {
  p4sa_observability_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.user",
    "roles/observability.viewer",
    "roles/observability.analyticsUser",
    "roles/logging.viewer",
    "roles/logging.viewAccessor",
    "roles/monitoring.metricWriter",
  ])
}

resource "google_project_iam_member" "aiplatform_p4sa_observability" {
  for_each = local.p4sa_observability_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_project_service_identity.aiplatform_p4sa.email}"
}

# ---------------------------------------------------------------------------
# 3. Cloud Monitoring Quality Drift Alert Policy (OnlineEvaluator Scores)
# ---------------------------------------------------------------------------
# Monitored resource type for Agent Platform Online Monitors is:
#   `aiplatform.googleapis.com/OnlineEvaluator`
# Metric type is:
#   `aiplatform.googleapis.com/online_evaluator/scores`
resource "google_monitoring_alert_policy" "agent_quality_drift" {
  count        = var.enable_quality_drift_alert ? 1 : 0
  display_name = "Agent Platform OnlineEvaluator - Quality Drift (< ${var.quality_drift_threshold})"
  combiner     = "OR"
  enabled      = true

  documentation {
    content   = "Triggered when an Agent Platform OnlineEvaluator rolling average score drops below ${var.quality_drift_threshold}. Inspect failing traces in Console -> Agent Platform -> Agents -> Evaluation -> Online monitors."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "OnlineEvaluator Score Below Threshold"
    condition_threshold {
      filter          = "resource.type = \"aiplatform.googleapis.com/OnlineEvaluator\" AND metric.type = \"aiplatform.googleapis.com/online_evaluator/scores\""
      comparison      = "COMPARISON_LT"
      threshold_value = var.quality_drift_threshold
      duration        = "1800s"

      aggregations {
        alignment_period   = "600s"
        # `online_evaluator/scores` is a DELTA DISTRIBUTION metric; use ALIGN_PERCENTILE_50
        # (ALIGN_MEAN is rejected for DISTRIBUTION valueType).
        per_series_aligner = "ALIGN_PERCENTILE_50"
      }
    }
  }

  alert_strategy {
    auto_close = "86400s"
  }

  depends_on = [google_project_service.enabled_apis]
}
