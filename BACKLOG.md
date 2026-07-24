# Backlog

One-line items — intent and rough constraint only. Expand conversationally
with Claude Code when actually briefing a piece of work; don't treat these
as specs.

## M0
- [done] Draft initial ontology (entity types, relation types, including Tender) as a config file, not hardcoded
- [partly done] Pick and hand-select the small real document set — 3 chosen (data/sources.yaml); the MOD Digital Strategy URL 404s and needs a working replacement
- [done] Build PDF parsing (text + section/page structure) and HTML main-content extraction — validated on the real DIP (text-native, 81pp) and Naval News article
- [done] Build structure-respecting chunking, retaining chunk position relative to the parsed document
- [done] Pick extraction model (default: Gemini via Vertex AI, Pro vs Flash decided against M0 output) — cost sanity-check still pending a real run
- [done] Write the extraction prompt/pipeline, constrained to the ontology config, returning supporting quotes not claimed offsets — built; not yet run (needs GCP/Vertex, see SETUP.md)
- [blocked: needs GCP] Manually review raw extraction output against the source documents; note failure patterns
- [pending] Capture manual review notes/labels as the seed of the eval set (don't discard at M0 end)

## M1
- Stand up chosen graph + vector store (Spanner Graph, or whatever's decided in DECISIONS.md by this point)
- Define storage schema for entities/relations including mandatory document/section provenance fields and optional exact-span fields
- Build post-hoc span alignment step: match extraction-returned quotes against source text to derive verified offsets; mark unaligned facts explicitly rather than dropping or faking a span
- Write ingestion code: extraction output → store, with provenance attached at write time
- Manual query script to trace any stored fact back to its source, with and without a resolved span
- Formalise the M0 eval set into something reusable for regression checks

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
- Build fixed detail-view templates for Programme, Company, Hardware, Standard
- Build lighter-weight detail-view templates for Term, Source, and Tender
- Wire click-through traceability and significance-ranked source edges into detail views
- Build card-based search/browse UI

## Later (deferred, not scheduled)
- Gap detection: structural sparsity + staleness signals → prioritised list
- Agentic web research triggered from a chosen gap → review queue
- AG-UI: agent-generated (not templated) detail view layouts
- Automated/continuous source monitoring
