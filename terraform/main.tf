# IaC for the DIKMS pipeline (DECISIONS.md: infrastructure-as-code).
#
# Covers: the APIs the pipeline needs (Vertex AI, Spanner), the Spanner Graph
# knowledge store (M1 canonical backend — see spanner.tf), and a pipeline
# service account with ADC-impersonation access so unattended runs need no
# downloaded key (see iam.tf). The embedded Kùzu backend needs no cloud
# resources and is not represented here. Application/graph schema (DDL) is owned
# by the storage adapter's migrations, not Terraform.
#
# NOTE: writing this IaC is agent-automatable; running `terraform apply` creates
# billable resources and handles credentials — that stays a human step (SETUP.md).

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote state in GCS so it survives destroy/apply cycles and isn't tied to
  # one machine. The bucket is created out-of-band (not managed here) so a
  # `terraform destroy` of the deployment never removes the state itself.
  backend "gcs" {
    bucket = "yesknowmaybe-tfstate"
    prefix = "dikms"
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

# APIs the pipeline needs. Vertex AI serves Gemini extraction + embeddings;
# Spanner serves the knowledge store.
resource "google_project_service" "vertex_ai" {
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "spanner" {
  service            = "spanner.googleapis.com"
  disable_on_destroy = false
}
