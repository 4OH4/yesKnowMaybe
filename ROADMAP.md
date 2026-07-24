# Roadmap

Staged to de-risk the two genuinely uncertain assumptions first — whether
ontology-constrained extraction produces usable output on real, messy
documents, and whether the confidence-weighted entity resolution pipeline
behaves sensibly — before investing in UI or lower-risk engineering.

## M0 — Parsing, chunking, and extraction proof of concept

Define the initial ontology (see `ARCHITECTURE.md`) covering a small,
hand-picked set of real documents — e.g. the Defence Investment Plan plus a
handful of press releases and one company website. Build the parsing stage
(PDF/HTML → clean text with structural metadata) and chunking (structure-
respecting, not fixed-size) before extraction, since extraction quality is
capped by both. Run extraction using the chosen extraction model (see
`DECISIONS.md`), returning supporting quotes rather than claimed offsets.
No review queue UI yet — inspect raw output directly.

**Goal:** does ontology-constrained extraction produce sensible, well-typed
entities and relations on real, messily-parsed and chunked material, before
anything else is built around it.

**Status (2026-07-24): COMPLETE.** Pipeline built and run end-to-end on the
real corpus via Vertex/Gemini 2.5 Flash. Manual review done and captured as the
eval seed (`notes/M0-review.md`). Extraction judged usable — well-typed,
fully-provenanced entities/relations with verbatim supporting quotes. The
ontology was tuned against real output from v0 to **v2** (Organisation with
typed attributes; Capability/Location/Role; governance + investment relations;
named individuals out of scope). Engineering hardening added along the way
(per-chunk failure isolation, 429 backoff, ToC-heading fix). Deferred to M1:
re-extract the corpus under v2, relation domain/range validation, proposal
dedup, progress logging, and a Vertex quota increase — see `BACKLOG.md`.

**Exit criteria:**
- Extraction output reviewed manually and judged usable — i.e. review
  would plausibly be faster than doing the work by hand.
- Parsing and chunking pipeline handles the hand-picked document set
  without materially degrading extraction (checked by spot comparison
  against the source).
- Manual review notes from this stage captured as the seed of the eval
  set (see `DECISIONS.md` — eval set timing), not discarded once M0 ends.

## M1 — Traceability, span alignment, and storage

**To start:** lock the storage choice (Spanner Graph vs alternative) — still
flagged open in `DECISIONS.md` — before building any schema against it. This is
the first M1 decision, not something to settle silently mid-build.

Get extracted entities/relations into a real graph + vector store (Spanner
Graph or alternative, per `DECISIONS.md`) with document/section-level
provenance attached from the moment of extraction, plus the post-hoc span
alignment step (matching extraction-returned quotes against source text to
derive verified offsets where possible — see `ARCHITECTURE.md`). No
agentic retrieval yet — confirm storage and manual query/trace-back work
first (direct GQL/Cypher-equivalent queries, manually tracing any fact back
to its source, with and without a resolved exact span).

**Exit criteria:**
- Can store extracted data and manually trace any fact back to its
  originating document and section.
- Span alignment step working and its success/failure rate on the M0
  document set understood (not every fact needs a resolved span, but the
  mechanism needs to be shown working on real output).
- Eval set from M0 formalised and usable as a regression check going
  forward.

## M2 — Review queue v1

Build the priority-tiered review queue (ontology changes > new nodes >
candidate `same_as` edges > mentions), including the full entity-resolution
pipeline (alias lookup, embedding similarity, LLM adjudication,
confidence-weighted edges, reviewed/rejected flag). Assign source
authoritativeness via the rule-based default (see `DECISIONS.md`) at this
point, since it's the first milestone where it's actually load-bearing for
review prioritisation.

**Exit criteria:** first point the system is usable end-to-end for
push-based ingestion — feed it a document, review what it proposes, accept
or reject, data lands correctly in the store, with sources correctly
scored for authoritativeness.

## M3 — Agentic retrieval and chat query

Build the retrieval-planning agent (dual-channel graph + vector search,
iterative) and the natural language chat interface. `same_as` edges are
evaluated per-edge at query time, not via transitive closure (see
`ARCHITECTURE.md` — a deliberate constraint on the retrieval design, not
an oversight to fix later).

**Exit criteria:** first point the system is actually answering research
questions; also the first real test of whether confidence-weighted
`same_as` edges behave sensibly at query time, including checking that no
false transitive clustering is occurring.

## M4 — Graph exploration and entity detail views

Node-centric graph browsing UI (one-hop expansion). Fixed-template entity
detail pages (Programme, Organisation, Hardware, Standard, Capability) with click-through
traceability and significance-ranked supporting edges.

**Exit criteria:** the three intended interfaces (chat, graph explorer,
card search/browse) all working against the same underlying store.

---

## Deferred / explicitly out of scope for now

- **Gap detection** — structural/staleness signals surfaced as a
  prioritised list, feeding agentic research on demand. Build once the core
  loop (M0–M4) is proven.
- **AG-UI** — genuinely agent-generated detail view layouts, steered by the
  ontology. Build once fixed templates have proven the content model is
  right.
- **Automated/pull ingestion** — continuous monitoring of known sources
  without a user trigger. Push-based ingestion and on-demand agentic
  research are sufficient for now.

## Resourcing note

This is a solo side project competing for time against departmental and
client work. Keep each milestone tightly scoped — if a milestone starts
expanding beyond its stated exit criteria, that's a signal to stop and
descope rather than keep building.
