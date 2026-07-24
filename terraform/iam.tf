# Pipeline service account for unattended runs (BACKLOG.md M1 carry-over:
# "service account instead of personal-user ADC").
#
# No key is generated: a downloaded SA key is a long-lived secret that would end
# up in Terraform state or on disk. Instead the named human principal is granted
# Token Creator on the SA, so unattended runs authenticate via ADC impersonation:
#   gcloud auth application-default login \
#     --impersonate-service-account=<pipeline_service_account_email output>
# The google-genai / Spanner clients pick this up transparently.

resource "google_service_account" "pipeline" {
  account_id   = var.pipeline_sa_account_id
  display_name = "DIKMS pipeline (extraction, embeddings, storage)"
}

# Vertex AI: Gemini extraction + embeddings.
resource "google_project_iam_member" "pipeline_vertex" {
  project = var.project
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

# Read/write the knowledge store database (not instance admin — the pipeline
# does not create/drop instances).
resource "google_spanner_database_iam_member" "pipeline_db" {
  instance = google_spanner_instance.kg.name
  database = google_spanner_database.kg.name
  role     = "roles/spanner.databaseUser"
  member   = "serviceAccount:${google_service_account.pipeline.email}"
}

# Let the named human impersonate the SA (ADC, no key). Skipped if left blank.
resource "google_service_account_iam_member" "impersonation" {
  count              = var.impersonating_principal == "" ? 0 : 1
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.impersonating_principal
}
