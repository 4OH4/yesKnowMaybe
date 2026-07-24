# Minimal, provisional IaC for the M0 extraction pipeline.
#
# Scope deliberately small: enable the Vertex AI API on an existing project so
# extraction can run. Storage (Spanner Graph or alternative) is an M1 decision
# and is intentionally NOT provisioned here yet. Expand as later milestones
# land concrete infrastructure (DECISIONS.md: infrastructure-as-code).

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

# APIs the M0 pipeline needs. Vertex AI serves Gemini extraction.
resource "google_project_service" "vertex_ai" {
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}
