# Architecture

This document describes the technical design as settled during concept
triage. It's the reference for what to build; `DECISIONS.md` records why
things landed this way and what alternatives were considered; `ROADMAP.md`
sequences the build.

## Overview

```
 Sources (docs, web pages)
        │
        ▼
 ┌──────────────────┐
 │  Ingestion /      │  ontology-constrained LLM extraction
 │  Extraction        │  + entity resolution pipeline
 └──────────────────┘
        │  candidate entities/relations, confidence scores
        ▼
 ┌──────────────────┐
 │  Review queue      │  priority-tiered HITL review
 └──────────────────┘
        │  approved
        ▼
 ┌──────────────────┐
 │  Knowledge store   │  hybrid graph + vector, full provenance
 └──────────────────┘
        │
        ▼
 ┌──────────────────┐         ┌──────────────────┐
 │  Retrieval agent   │◄───────┤  Chat interface    │
 │  (dual-channel,     │        └──────────────────┘
 │   iterative)        │
 └──────────────────┘         ┌──────────────────┐
        │                     │  Graph explorer    │  node-centric, one-hop
        └────────────────────►│  Card search/browse│  fixed templates (v1)
                              └──────────────────┘

 (separate, experimental)
 ┌──────────────────┐
 │  Gap detection      │  structural + staleness signals
 │  → prioritised list │  → user picks → agentic web research → review queue
 └──────────────────┘
```

## Ontology

A defined, versioned ontology of entity types and relation types. Grows only
via human-in-the-loop approval (see Decision: Ontology growth gating).

**Initial entity types (starting point for M0, expect refinement once real
extraction is run):**

- `Programme` — e.g. Sense-Decide-Effect, Digital Targeting Web, ASGARD
- `Organisation` — any organisation, governmental or non-governmental
  (companies, government departments, military commands, agencies, academic
  institutions, multinational bodies). The kind is an `org_type` attribute
  (government/military/commercial/academic/ngo/multinational/other), with a
  `nationality` attribute where not global — rather than a separate entity
  type per kind. Supersedes the original `Company` type after M0 review
  showed the ontology had no home for government/military bodies (see
  `DECISIONS.md`).
- `Hardware` — e.g. Ajax, F-35, specific radar systems
- `Capability` — an ability to achieve a military effect (e.g. integrated air
  and missile defence, ISTAR), distinct from the delivering `Programme` and the
  realising `Hardware`. Added in ontology v2 after M0 review.
- `Location` — a geographic place, site, base or facility (sites, facilities
  and infrastructure as one type). Added in ontology v2.
- `Role` — an organisational post (e.g. Chief Information Officer), captured as
  the position, not the individual. Named individuals are out of scope and are
  never extracted or stored (see `DECISIONS.md`). Added in ontology v2.
- `Standard` — e.g. Cursor on Target, Link 16, Sapient
- `Source` — a document or web page; retained as a first-class node, not
  metadata, so facts can be traced to source credibility
- `Term` — domain vocabulary (e.g. DDIL, DLOD) where the concept itself is
  worth tracking, not just as an alias
- `Tender` — an invitation to tender, open or historical. Time-bounded by
  nature (open/closed status, closing date), which sits slightly oddly
  against an otherwise push/static ingestion model — but it's explicitly
  in scope (see project origin) and needs a home in the ontology now
  rather than being silently dropped. Whether tenders are actively
  monitored (pull) is a separate, later question (see Ingestion mode); the
  entity type should exist from M0 regardless, since push-ingested
  documents (e.g. a procurement portal page fed in manually) will still
  reference tenders.

**Initial relation types:**

- `develops` / `delivers` (Organisation → Programme/Hardware/Capability)
- `part_of` (Programme → Programme, Hardware → Programme, Organisation →
  Organisation) — extended to organisational hierarchy in v2
- `implements_standard` (Hardware/Programme → Standard)
- `funds` (Organisation/Programme → Programme/Hardware/Capability) — a
  government body funding a programme is an `Organisation` source, not a
  special case
- `invests_in` (Organisation/Programme → Programme/Capability) — v2
- `supports` (Organisation/Programme → Programme/Capability/Term) — v2
- `governs` (Organisation → Programme/Organisation) — v2
- `member_of` (Organisation → Organisation) — v2
- `extracted_from` (any entity/relation → Source) — the provenance edge,
  mandatory on every extraction
- `same_as` — confidence-weighted, see Entity Resolution below
- `mentions` (Source → any entity) — lighter-weight than a substantive
  relation, used for "mention of existing data" review-queue items

This list is a starting point, not a spec to build against blindly — expect
it to be revised after M0 shows what real documents actually produce.

## Provenance and traceability

Every entity and relation extracted must carry:

- Source document/page identifier (`extracted_from` edge to a `Source` node)
- Extraction timestamp
- Ideally, the specific text span supporting the extraction

This is a hard requirement, not a nice-to-have (see Decision: Full
traceability). It does not need to be surfaced on every rendered view, but
must always be present and retrievable via click-through. Where multiple
sources support one entity/fact, edges should be rankable by significance —
initially derived from source authoritativeness (see below), refined later
if needed.

## Source authoritativeness

Sources are scored on authoritativeness (e.g. UK government department site:
high; company website: medium; low-impact blog: low). This score:

- Weights how new information from that source is prioritised in the review
  queue
- Contributes to edge significance ranking where multiple sources support
  one fact
- Is itself just a property on the `Source` node — not a separate subsystem

**Assignment mechanism:** starts as a simple domain/rule-based default
(e.g. `.gov.uk` and known institutional domains → high; established
company sites → medium; everything else → low, manually overridden as
needed) rather than model-judged from the outset — a rule table is
transparent, cheap, and easy to correct, whereas an LLM-judged
authoritativeness score adds another unreliable inference with no clear
benefit at this scale. The user can manually override any source's score
via the review queue. Model-assisted scoring (e.g. for sources not matching
any known rule) can be considered later if the rule table proves
insufficient — not needed for M0–M1, becomes relevant once the review
queue (M2) actually uses it for prioritisation.

## Document parsing

Source material must be converted to clean text before extraction sees it.
This is a distinct, earlier pipeline stage, not an assumed input:

- **PDFs** (e.g. the Defence Investment Plan) — extract text preserving
  section/page structure where possible; government policy PDFs are
  typically well-structured (headings, numbered sections) which should be
  retained as metadata on chunks, not flattened away. Scanned/image PDFs
  are out of scope for v1 — assume text-native PDFs only; revisit if this
  proves a real limitation.
- **HTML/web pages** — extract main content, stripping navigation,
  boilerplate, and ads. Retain the page's own structure (headings) where
  present.
- Output of this stage: clean text plus structural metadata (page/section
  boundaries), which chunking (below) and span alignment both depend on.

## Chunking

Documents are split into chunks before extraction, both because they will
routinely exceed a single effective extraction pass and because output
quality degrades well before hard context limits are reached.

- Chunk boundaries should respect document structure where available
  (section/paragraph boundaries) rather than fixed character counts, to
  reduce entities being split awkwardly across chunks.
- Each chunk retains a reference to its position in the parsed document
  (page/section, and character offset within the parsed text), since this
  is what span alignment (below) resolves against — offsets are only
  meaningful relative to a specific, versioned parse of a specific source.
- Entity resolution must handle the same entity appearing in multiple
  chunks of the same document as a routine case, not an edge case — this
  is the same mechanism as cross-document resolution, applied within a
  single source.

## Span-level provenance: mandatory at document/section level, best-effort at exact span

This was inconsistent between `ARCHITECTURE.md` and `DECISIONS.md` in an
earlier draft — resolved as follows:

- **Mandatory:** every extracted entity/relation must link to its source
  document and the specific chunk/section it came from. This is always
  achievable and always verified (it's just pipeline bookkeeping) and
  satisfies the traceability requirement on its own even if exact-span
  alignment fails.
- **Best-effort, verified when present:** an exact text span (character
  offset) is attempted via **post-hoc alignment**, not by trusting an
  offset claimed directly by the extraction LLM. The extraction step
  should return a short supporting quote (the actual text it's grounding
  the extraction in) rather than a claimed offset; a separate alignment
  step then searches for that quote in the source chunk to derive a real,
  verified offset. LLMs are unreliable at emitting correct character
  positions directly but can usually reproduce or closely paraphrase the
  grounding text, which is what makes alignment feasible.
- If alignment fails to find a confident match (quote not found, or only a
  weak fuzzy match), the fact still has valid document/section-level
  provenance — it just lacks a highlighted exact span. This is recorded
  (e.g. an `alignment_status` field), not silently dropped, so it's visible
  which facts have exact-span backing and which don't.
- No unaligned or unverified offset is ever stored as if it were a
  confirmed span. This is the concrete meaning of "traceability is a hard
  requirement" for span level specifically — the document/section link is
  the non-negotiable floor; the exact span is a verified enhancement on
  top of it.

## Extraction model

The extraction LLM is a first-class, explicit choice, not an implementation
detail — M0's entire purpose is judging this model's output against the
ontology, so it must be decided going in. Default: **Gemini** (current
generation, e.g. Gemini 3 Pro/Flash-class model — pick based on the
accuracy/cost trade-off once M0 output is being judged), called via Vertex
AI, called directly per chunk. This keeps extraction on GCP, consistent
with the Spanner Graph direction for storage, and avoids mixing model
providers unnecessarily across the pipeline. Rough cost envelope should be
sanity-checked against expected document volume (thousands of documents,
see `ARCHITECTURE.md` scale note) before M0 concludes — if per-document
extraction cost is materially higher than expected, that's a finding to
bring back as a decision point, not something to discover later at volume.
This is a starting default, not locked in the same way as the four
non-negotiable constraints — revisit if M0 output quality doesn't justify
it or cost proves prohibitive.

## Extraction pipeline

1. Document/page ingested (push: user-supplied, or agentic research result).
2. Parsed to clean text with structural metadata (see Document parsing).
3. Split into chunks respecting document structure (see Chunking).
4. LLM extracts candidate entities and relations per chunk, constrained to
   the current ontology, returning supporting quotes rather than claimed
   offsets.
5. Post-hoc alignment step derives verified spans where possible (see
   Span-level provenance); document/section-level provenance is attached
   regardless of alignment success.
6. Candidates pass through entity resolution (below) before reaching the
   review queue — including same-document, cross-chunk duplicates as a
   routine case.
7. Approved items are committed to the knowledge store.

## Entity resolution

Composite pipeline, not a single mechanism:

1. **Alias/synonym lookup** — cheap first pass against a maintained list of
   known aliases and abbreviations per entity. Not trusted alone: this
   sector has abbreviation collisions (the same acronym meaning different
   things in different contexts), so an alias hit is a candidate, not a
   verdict.
2. **Embedding similarity** — candidate entity (name + surrounding context)
   compared against existing entities of the same type. Produces a
   shortlist, not a decision — used to catch surface-form variation the
   alias list hasn't seen yet, and to help disambiguate collisions by using
   context, not just name.
3. **LLM adjudication** — for candidates above a similarity floor, an LLM is
   given both surface forms plus their surrounding context and asked to
   judge genuine sameness vs. collision vs. near-miss. This is the step
   capable of handling the "same acronym, different concept" case that
   string/embedding matching alone cannot.
4. **Human review** — anything not high-confidence surfaces in the review
   queue as a candidate `same_as` edge.

**Never destructively merge.** Two candidate-duplicate entities are never
fused into one node. Instead:

- A `same_as` edge is created between the two nodes, carrying a **confidence
  weight** (continuous score, not discrete tiers).
- A separate **reviewed/rejected flag** records whether a human has already
  adjudicated this specific pair — independent of the confidence score, so
  the system doesn't re-propose matches that have already been explicitly
  rejected.
- At **query time**, the retrieval layer treats high-confidence `same_as`
  edges as identity (traverses through them as if the nodes were one),
  while the underlying nodes, their own edges, and their original
  provenance remain distinct and fully auditable.
- Confidence bands map to review behaviour (e.g. very high → auto-commit
  edge, low enough → surface as review item, near-zero → no edge created at
  all) — exact thresholds are tuning parameters, not architecture, and will
  need real extraction output to set sensibly.
- On review, the synonym/alias list is updated for entities confirmed as
  the same, so future mentions are more likely to resolve automatically
  (Scenario A rather than needing adjudication again).

**Transitivity is not assumed.** If A `same_as` B (high confidence) and B
`same_as` C (high confidence), the system must not silently treat A and C
as identical by chained inference — a cluster can be pulled together
through a sequence of individually-plausible-but-weak links even when no
single edge is wrong enough to catch on review. At query time, identity
traversal should be evaluated per-edge (following each `same_as` edge on
its own merits), not via transitive closure across a whole component. If
clustering multiple nodes as one identity group is ever needed (e.g. for
display), that should be an explicit, separately-reviewable step — a
proposed cluster, shown as a group for human confirmation — rather than an
automatic consequence of pairwise confidence scores. This is an M3 concern
(first relevant once retrieval-time identity resolution is built) but
worth stating now so M2's edge model doesn't implicitly assume
transitivity by, for example, storing resolved identity as a single group
ID rather than as edges.

## Review queue

Priority-tiered, applies both to direct ingestion and to results returned by
the gap-driven research agent:

1. **Ontology changes** (new entity/relation types) — highest priority,
   always reviewed individually, never bulk-acceptable.
2. **New nodes** — significant, reviewed but bulk-acceptable if the user
   chooses.
3. **Candidate `same_as` edges** (entity resolution) — always reviewed
   individually where confidence is not high enough to auto-commit; a wrong
   merge is worse than a wrong new node, so no bulk-accept for this
   category regardless of volume.
4. **Mentions of existing data** — lowest priority, bulk-acceptable.

When results come from the agentic research workflow (see Gap Detection
below), items are grouped by source, with the source's authoritativeness
score visible alongside each group.

## Knowledge store

Hybrid graph + vector store. Candidate: Spanner Graph (native GQL, built-in
vector search, supports the GraphRAG pattern) — a credible option, not yet
locked in (see `DECISIONS.md`). Whatever the final choice, it must support:

- Native graph traversal (multi-hop)
- Vector similarity search over the same data
- Storing arbitrary edge properties (confidence, source, timestamp) without
  contortion

## Retrieval

Agentic, not fixed hybrid concatenation:

- Given a query, an agent plans a retrieval strategy — deciding whether to
  use graph traversal, vector search, or both, and in what sequence.
- Dual-channel retrieval: semantic queries over text/vector data, relational
  queries over graph structure.
- Iterative — the agent can issue further sub-queries if the first pass
  doesn't resolve the question, particularly for multi-hop questions (e.g.
  "which companies are involved in programmes using technology developed
  under ASGARD").
- No fixed latency budget — this is a research tool, not an interactive
  low-latency system, so multi-step agentic retrieval with full provenance
  tracking is an acceptable trade-off (see Decision: Latency).
- Every answer must be traceable back to source facts, per the traceability
  requirement above.

## Interfaces

**Chat.** Natural language querying against the retrieval agent. Answers
should reference underlying facts in a way that allows click-through to
source (exact UI mechanism TBD at build time).

**Graph explorer.** Node-centric: start from one entity, expand one hop at a
time. Deliberately not a full-graph overview — clusters and whole-graph
visual structure are not useful at the scale this will reach, and a
full-graph render becomes an unreadable hairball quickly (see Decision:
Graph visualisation).

**Card search/browse.** Search (e.g. "government technology programmes")
returns a set of cards; clicking a card opens a detail view. Detail view
content is dynamic (pulled from the graph), but **v1 layout is a fixed
template per entity type** (Programme, Organisation, Hardware, Standard,
Capability), not genuinely agent-generated. True agent-generated layout
(AG-UI), steered by
the ontology and recognising entity categories, is a later roadmap item —
deferred because it's the least technically proven part of the concept and
creates tension with the traceability requirement if not carefully
constrained (see Decision: AG-UI deferral). Traceability in the fixed
templates is available via click-through, with supporting edges ranked by
significance where an entity has multiple sources.

**Template coverage.** M4's fixed templates cover `Programme`, `Organisation`,
`Hardware`, `Standard`, and `Capability` explicitly. The remaining first-class,
clickable types — `Term`, `Source`, `Tender`, `Location`, `Role` — need their
own (simpler) templates in M4 too, not a later add-on: a `Term` node with no
detail view is a dead end a user will hit early. These can be lighter-weight
than the main types (e.g. `Term`: definition, related entities, sources it
appears in; `Source`: authoritativeness, what it contributed, ingestion date)
but should exist from M4, not be silently deferred alongside AG-UI.

## Gap detection (experimental, not v1 core)

Signals considered "gaps" worth flagging:

- **Structural** — nodes with very few edges, or edges only in one
  direction
- **Staleness** — no update in N months on a fast-moving programme

Flagged gaps are presented to the user in prioritised order; the user
chooses what to act on before an agent conducts web research against that
gap. Results from that research re-enter the standard review queue, grouped
by source with authoritativeness weighting.

This is explicitly scoped as a research workstream — worth building once the
core ingestion/retrieval loop is proven, not a hard requirement of an
initial working system.

## Ingestion mode

v1 is push-based: user supplies documents/URLs directly, or triggers
agentic research on demand. Continuous/automated monitoring of known
sources is a later-stage ambition, not in scope now.

## Explicitly out of scope for v1

- Genuinely agent-generated (AG-UI) detail view layouts
- Gap-detection-driven automated research (manual trigger only for now)
- Continuous/automated source monitoring
- Any handling of classified or export-controlled material
