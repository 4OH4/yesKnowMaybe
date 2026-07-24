output "pipeline_service_account_email" {
  description = "Impersonate this for unattended runs via ADC (see iam.tf)."
  value       = google_service_account.pipeline.email
}

output "spanner_instance" {
  description = "Provisioned Spanner instance id (matches storage.spanner.instance in config)."
  value       = google_spanner_instance.kg.name
}

output "spanner_database" {
  description = "Provisioned Spanner database name (matches storage.spanner.database in config)."
  value       = google_spanner_database.kg.name
}
