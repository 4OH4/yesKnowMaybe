# Setup

Local setup for the M0 pipeline. Parsing and chunking need only Python and the
package dependencies; extraction additionally needs a GCP project with Vertex
AI enabled and credentials.

## 1. Python environment

The project is managed with [uv](https://docs.astral.sh/uv/). Python 3.13 is
pinned in `.python-version` (kuzu, the embedded store backend, has no 3.14 wheel
yet — see `pyproject.toml`). One command creates the venv on the right Python
and installs everything, including the dev group:

```bash
uv sync
```

Run project commands through uv (no manual venv activation needed):

```bash
uv run dikms --help
```

`uv.lock` is committed, so the resolved dependency set is reproducible.

## 2. Fetch the corpus and run parse/chunk (no GCP needed)

```bash
uv run dikms fetch
uv run dikms parse defence-investment-plan --show 500
uv run dikms chunk navalnews-fads-type83-2025
```

Downloaded documents land in `data/corpus/` (gitignored). A PDF that yields
almost no text is flagged as likely scanned/image — out of scope for v1.

## 3. GCP provisioning via Terraform (needed for `dikms extract` and the Spanner store)

**This is a manual, human step. Writing the IaC is automatable, but running
`terraform apply` creates billable resources and handles credentials — which the
coding agent cannot and must not do.** The Terraform provisions everything the
cloud path needs: the Vertex AI and Spanner APIs, the Spanner Graph instance +
(empty) database, and a pipeline service account with impersonation access.

1. Create or select a GCP project, with billing enabled.
2. Provision:
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars   # set project; optionally set
                                                  # impersonating_principal
   terraform init && terraform apply
   ```
   Note the outputs (`pipeline_service_account_email`, `spanner_instance`,
   `spanner_database`). **Cost:** Spanner has no scale-to-zero — the instance
   bills continuously (100 processing units) while it exists. `terraform
   destroy` tears it down between work; `spanner_deletion_protection = false`
   (the default) allows this.
3. Authenticate with Application Default Credentials. Preferred (no key file) —
   impersonate the pipeline SA created above (requires `impersonating_principal`
   to have been set to your `user:` principal):
   ```bash
   gcloud auth application-default login \
     --impersonate-service-account=<pipeline_service_account_email>
   ```
   Or, for a quick local run as yourself: `gcloud auth application-default login`.
4. Point the pipeline at your project/store: edit `vertex.project` and
   `storage.spanner.*` in [config/extraction.yaml](config/extraction.yaml) to
   match the Terraform outputs, or export `GOOGLE_CLOUD_PROJECT` (and optionally
   `GOOGLE_CLOUD_LOCATION`). The default `storage.backend` is `kuzu` (embedded,
   no cloud needed); switch to `spanner` once provisioned.

## 4. Run extraction

```bash
uv run dikms extract navalnews-fads-type83-2025
```

Add `--verbose` to print per-chunk progress to stderr.

Writes `output/<id>.json` (machine record, full provenance) and
`output/<id>.review.md` (the manual-review sheet — annotate it to seed the
eval set). Before concluding M0, sanity-check per-document extraction cost
against the expected document volume (ARCHITECTURE.md: extraction model).
