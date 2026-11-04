variable "project_id" {
  description = "Google Cloud project ID hosting Agent Runtime and Agent Platform Evaluations."
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region for Agent Runtime, Metric Registry, and Evaluation Runs."
  type        = string
  default     = "us-central1"
}

variable "eval_bucket_name" {
  description = "Optional custom Cloud Storage bucket name for evaluation datasets and artifacts. Defaults to <project_id>-agent-eval-flywheel."
  type        = string
  default     = ""
}

variable "runtime_sa_id" {
  description = "Account ID for the dedicated least-privilege Agent Runtime Service Account."
  type        = string
  default     = "adk-eval-runtime-sa"
}

variable "quality_drift_threshold" {
  description = "Minimum acceptable rolling mean OnlineEvaluator score (0.0 to 1.0) before triggering a Cloud Monitoring Quality Drift incident."
  type        = number
  default     = 0.80
}

variable "enable_quality_drift_alert" {
  description = "Whether to provision the Cloud Monitoring Alert Policy on aiplatform.googleapis.com/online_evaluator/scores."
  type        = bool
  default     = true
}
