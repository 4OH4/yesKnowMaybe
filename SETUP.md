# Setup

Local setup for the M0 pipeline. Parsing and chunking need only Python and the
package dependencies; extraction additionally needs a GCP project with Vertex
AI enabled and credentials.

## 1. Python environment

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell:  .venv\Scripts\Activate.ps1
# bash/zsh:            source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Fetch the corpus and run parse/chunk (no GCP needed)

```bash
dikms fetch
dikms parse defence-investment-plan --show 500
dikms chunk navalnews-fads-type83-2025
```

Downloaded documents land in `data/corpus/` (gitignored). A PDF that yields
almost no text is flagged as likely scanned/image — out of scope for v1.

## 3. GCP / Vertex AI (needed for `dikms extract`)

**This is a manual, human step — it provisions cloud resources and handles
credentials, which the coding agent cannot and must not do.**

1. Create or select a GCP project, with billing enabled.
2. Enable the Vertex AI API, either with Terraform:
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars   # fill in your project id
   terraform init && terraform apply
   ```
   or directly: `gcloud services enable aiplatform.googleapis.com`.
3. Authenticate with Application Default Credentials:
   ```bash
   gcloud auth application-default login
   ```
4. Point the pipeline at your project, either by editing `vertex.project` in
   [config/extraction.yaml](config/extraction.yaml) or by exporting
   `GOOGLE_CLOUD_PROJECT` (and optionally `GOOGLE_CLOUD_LOCATION`).

## 4. Run extraction

```bash
dikms extract navalnews-fads-type83-2025
```

Writes `output/<id>.json` (machine record, full provenance) and
`output/<id>.review.md` (the manual-review sheet — annotate it to seed the
eval set). Before concluding M0, sanity-check per-document extraction cost
against the expected document volume (ARCHITECTURE.md: extraction model).
