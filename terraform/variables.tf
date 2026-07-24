variable "project" {
  type        = string
  description = "GCP project id to enable the extraction pipeline in."
}

variable "region" {
  type        = string
  description = "Default region; keep consistent with vertex.location in config/extraction.yaml."
  default     = "europe-west2"
}
