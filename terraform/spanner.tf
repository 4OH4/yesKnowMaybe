# Spanner Graph knowledge store — the M1 canonical/production backend behind the
# storage adapter (DECISIONS.md: knowledge store).
#
# The database is provisioned EMPTY: tables and the property-graph (GQL) DDL are
# owned by the storage adapter's schema migrations, not Terraform, so schema
# changes travel with application code and stay expressible against the Kùzu
# backend too. GoogleSQL dialect is required for GQL / Spanner Graph queries.

resource "google_spanner_instance" "kg" {
  name             = var.spanner_instance
  display_name     = "DIKMS knowledge store"
  config           = "regional-${var.region}"
  processing_units = var.spanner_processing_units
  edition          = "STANDARD"

  force_destroy = !var.spanner_deletion_protection

  depends_on = [google_project_service.spanner]
}

resource "google_spanner_database" "kg" {
  instance            = google_spanner_instance.kg.name
  name                = var.spanner_database
  database_dialect    = "GOOGLE_STANDARD_SQL"
  deletion_protection = var.spanner_deletion_protection
}
