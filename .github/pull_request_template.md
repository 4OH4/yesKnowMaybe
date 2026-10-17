Closes #

## For human review
<!-- Filled in by the independent reviewer: judgement calls you approve by merging, risks and open questions. "None" if there are none. -->

## Summary
<!-- What changed and why, in a few sentences. -->

## Acceptance criteria
<!-- Copy the checklist from the issue and tick off each item, with evidence. -->
- [ ]

## How it was verified
<!-- Commands run and their results. -->
```bash
uv run ruff check
uv run pytest
```

## Constraint check
<!-- What was checked for each of the four non-negotiable constraints in CLAUDE.md. -->
- [ ] **Traceability:** no fact is written or returned without its source, chunk, section and timestamp; no unverified span presented as verified
- [ ] **Non-destructive merge:** no node fusion or group id; `same_as` only as pairwise, confidence-weighted edges
- [ ] **HITL ontology gating:** `config/ontology.yaml` unchanged (or: this is a `needs-human` ontology PR, recorded in `DECISIONS.md`)
- [ ] **Open material only:** nothing client-identifying, classified or export-controlled added

## Design notes / decisions
<!-- Notable choices worth recording in DECISIONS.md, or "None". -->

## Suggested follow-ups
<!-- New issues this work revealed, or "None". -->
