# Backlog

One-line items — intent and rough constraint only. Expand conversationally
with Claude Code when actually briefing a piece of work; don't treat these
as specs.

## M0 — complete
All items done. Outcome: parsing/chunking/extraction pipeline built and run
end-to-end on the real corpus (see `notes/M0-review.md`); ontology tuned from
v0 to **v2** (Organisation with typed attributes; Capability/Location/Role;
governance + investment relations; named individuals out of scope).
- [done] Draft initial ontology as a config file, not hardcoded
- [done] Pick and hand-select the small real document set — 3 chosen (data/sources.yaml), all fetch/parse cleanly
- [done] Build PDF parsing (text + section/page structure) and HTML main-content extraction
- [done] Build structure-respecting chunking, retaining chunk position relative to the parsed document
- [done] Pick extraction model — Gemini 2.5 Flash via Vertex; per-doc cost negligible at Flash pricing on the M0 corpus (a formal envelope is still worth a check before volume)
- [done] Write the ontology-constrained extraction prompt/pipeline, returning supporting quotes not claimed offsets
- [done] Manually review raw extraction output against the sources; failure patterns noted (`notes/M0-review.md`)
- [done] Capture manual review notes as the seed of the eval set (`notes/M0-review.md`)

## M1
Storage, provenance-at-write, and span alignment. Storage direction is now
locked (see `DECISIONS.md`): Spanner Graph is the canonical target, built behind
a thin storage-adapter interface with Kùzu as an embedded, unattended-buildable
backend. Core:
- **Define the storage-adapter interface first** — node/edge upsert with arbitrary edge properties, multi-hop traversal, vector similarity; schema must be expressible in both backends. Build against the interface, never Spanner APIs directly
- Implement the **Kùzu backend** (embedded, no credentials) so M1 storage/query is buildable and testable unattended
- Implement the **Spanner Graph backend** behind the same interface (exercised when Vertex/Spanner credentials are present; instance provisioning is a manual human step, not an automated story)
- Pin the **Vertex embedding model + output dimension** before freezing schema (the vector column width depends on it); record it in `config/extraction.yaml`
- Define storage schema for entities/relations including mandatory document/section provenance fields and optional exact-span fields (model the pipeline's Provenance/AlignmentStatus already in `dikms.models`)
- Build post-hoc span alignment step: match extraction-returned quotes against source text to derive verified offsets; mark unaligned facts explicitly (AlignmentStatus) rather than dropping or faking a span
- Write ingestion code: extraction output → store, with provenance attached at write time
- Manual query script to trace any stored fact back to its source, with and without a resolved span
- Formalise the M0 eval set into something reusable for regression checks — must tolerate run-to-run non-determinism (temperature 0 still varied), not exact-match

Carried from the M0 review (hardening, do alongside M1):
- Re-extract the corpus under ontology v2 (current committed outputs are v1-era) to produce real v2 data to ingest
- Add relation domain/range validation (the `part_of` overload passes silently today — validator only checks type existence)
- De-duplicate/aggregate type proposals before review (they recur per chunk)
- Add `--verbose` per-chunk progress logging for long runs (DIP is opaque for ~25 min)
- Request a Vertex Gemini quota increase before larger runs; consider a service account instead of personal-user ADC for unattended runs

## M2
- Design review queue data model (item, type, priority tier, status)
- Build source authoritativeness rule table (domain-based default, manually overridable)
- Build alias/synonym list mechanism, seeded from M0 extraction output
- Implement embedding similarity shortlist step for candidate duplicates
- Implement LLM adjudication step for shortlisted candidates
- Implement confidence-weighted `same_as` edge creation, non-destructive
- Implement reviewed/rejected flag to prevent re-litigating settled matches
- Build minimal review queue UI (can be very plain — functional over polished)
- Wire bulk-accept for new-node and mention tiers; block bulk-accept for candidate merges

## M3
- Build retrieval-planning agent: query → strategy (graph traversal / vector / both) → iterate
- Implement dual-channel retrieval (semantic + relational)
- Wire high-confidence `same_as` edges into retrieval-time identity resolution, evaluated per-edge (no transitive closure)
- Check for and test against false transitive clustering on real resolved-entity data
- Build chat interface, answers referencing traceable source facts

## M4
- Build node-centric graph explorer (one-hop expansion from a selected entity)
- Build fixed detail-view templates for Programme, Organisation, Hardware, Standard, Capability
- Build lighter-weight detail-view templates for Term, Source, Tender, Location, Role
- Wire click-through traceability and significance-ranked source edges into detail views
- Build card-based search/browse UI

## Later (deferred, not scheduled)
- Gap detection: structural sparsity + staleness signals → prioritised list
- Agentic web research triggered from a chosen gap → review queue
- AG-UI: agent-generated (not templated) detail view layouts
- Automated/continuous source monitoring
