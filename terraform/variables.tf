variable "project" {
  type        = string
  description = "GCP project id to enable the extraction pipeline in."
}

variable "region" {
  type        = string
  description = "Default region; keep consistent with vertex.location in config/extraction.yaml."
  default     = "europe-west2"
}

# --- Spanner knowledge store (must match storage.spanner in config/extraction.yaml) ---

variable "spanner_instance" {
  type        = string
  description = "Spanner instance id for the knowledge store."
  default     = "dikms-kg"
}

variable "spanner_database" {
  type        = string
  description = "Spanner database (provisioned empty; schema is app-owned)."
  default     = "knowledge"
}

variable "spanner_processing_units" {
  type        = number
  description = "Spanner compute. 100 is the billable minimum — appropriate at low-thousands scale; Spanner has no scale-to-zero, so this bills continuously while the instance exists."
  default     = 100
}

variable "spanner_deletion_protection" {
  type        = bool
  description = "Block accidental instance/database deletion. Defaulted off so the store can be torn down for cost control between work; flip on once real data is in it."
  default     = false
}

# --- Pipeline service account (unattended runs via ADC impersonation, no key) ---

variable "pipeline_sa_account_id" {
  type        = string
  description = "Account id (local part) of the pipeline service account."
  default     = "dikms-pipeline"
}

variable "impersonating_principal" {
  type        = string
  description = "IAM principal allowed to mint tokens for the pipeline SA, e.g. \"user:you@example.com\". Enables `gcloud auth application-default login --impersonate-service-account=...` so no key file is ever created. Leave blank to skip the binding."
  default     = ""
}
