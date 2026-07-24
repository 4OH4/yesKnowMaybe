# Decisions

A log of key architectural and scope decisions, so later work doesn't
re-litigate settled questions. New entries go at the top. Each entry: what
was decided, why, and what alternatives were considered.

---

### Primary language is Python
**Decision:** Python is the primary implementation language for the
ingestion, extraction, and retrieval backend. Previously carried as an
unlocked assumption; now settled.
**Why:** The GraphRAG, LLM-orchestration, and Vertex AI client ecosystem is
Python-first, and every core dependency the architecture leans on (document
parsing, embedding/vector tooling, the Spanner Graph and Vertex AI SDKs) has
mature, first-class Python support. Choosing one primary language keeps a
solo, agentically-built project coherent rather than spread across runtimes.
**Not absolute:** an individual component may diverge where there's a clear
reason (e.g. a UI layer in another stack); such a divergence is flagged here
rather than assumed. Consistent with the light-dependency and
config-over-hardcoding conventions in `CLAUDE.md`.

---

### Span-level provenance is mandatory at document/section level; exact span is best-effort, verified via post-hoc alignment
**Decision:** Every fact must link to its source document and chunk/section
— always achievable, always verified, and sufficient on its own to satisfy
the traceability requirement. An exact character-offset span is attempted
separately, by having the extraction LLM return a supporting quote (not a
claimed offset) and aligning that quote back against the source text to
derive a verified offset. If alignment fails, the fact keeps its
document/section-level provenance and is marked as lacking an exact span —
never stored as if a claimed offset were verified.
**Why:** An earlier draft stated span-level traceability as a hard
requirement in this log while softening it to "ideally" in the
architecture doc — a real inconsistency, not just wording. LLMs are known
to be unreliable at emitting correct character offsets directly (they
paraphrase and confabulate positions), so trusting a claimed offset would
mean silently storing unverified provenance under the banner of a "hard
requirement" — worse than not having the exact span at all. Alignment
against a returned quote is verifiable; a claimed offset is not.
**Consequence:** the extraction pipeline must be built around returning
grounding quotes, and a post-hoc alignment step is part of M0/M1, not an
optional refinement.

---

### Extraction model: default to Gemini on Vertex AI, explicit and revisitable
**Decision:** Use a current Gemini model (Pro or Flash class, chosen on the
accuracy/cost trade-off once M0 output is being judged) as the extraction
LLM by default, called via Vertex AI per chunk.
**Why:** The extraction model was previously an undecided dependency
despite M0's entire purpose being to judge that model's output against the
ontology — it's a prerequisite, not a detail to fill in later. Gemini/GCP
keeps the extraction stage on the same platform as the storage direction
(Spanner Graph), avoiding an unnecessary second model provider in the core
pipeline. Rupert's stated preference over the earlier Claude default.
**Not locked in the same way as the four non-negotiable constraints:**
revisit if M0 output quality doesn't justify it, or if cost (checked
against expected document volume) proves prohibitive.

---

### Document parsing and chunking are explicit M0 pipeline stages
**Decision:** Source material is parsed to clean text with structural
metadata (page/section boundaries) as a distinct stage before extraction,
and split into structure-respecting chunks before the extraction LLM sees
it. PDFs are assumed text-native for v1 (no OCR/scanned-document handling).
**Why:** These were previously unaddressed, with the Defence Investment
Plan (a large structured government PDF) named as a first real input
without any decision on how it becomes text. Parsing quality directly caps
extraction quality, and chunking interacts with both entity resolution
(same entity across chunks) and span alignment (offsets are only
meaningful relative to a specific parse) — these are M0 design decisions,
not M2 ones.

---

### Ontology growth is human-in-the-loop only
**Decision:** The agent may propose new entity/relation types during
extraction, but they are never committed to the live ontology without
explicit human approval.
**Why:** An ontology that drifts under autonomous agent control leads to
duplicate/inconsistent types and quality decay. Given this is a personal
tool where the owner is also the reviewer, the review cost is low relative
to the risk of an uncontrolled schema.
**Alternative considered:** Fully autonomous ontology extension — rejected
as too risky for the review cost saved.

---

### Full traceability is a hard requirement
**Decision:** Every extracted fact must carry a link back to its source
document, section/span, and extraction timestamp, retrievable at minimum via
click-through even where not displayed by default.
**Why:** Misattribution in this domain is actively misleading, not merely
imprecise (e.g. wrongly attributing a capability to a programme could
inform a flawed judgement). The tool is not used for critical decisions, but
still needs to be correctable and auditable rather than simply plausible.
**Consequence:** Provenance must be built into extraction from the start
(M0/M1), not retrofitted — retrofitting after the fact would require
re-processing every previously ingested source.

---

### Entity resolution: composite pipeline, never destructive merging
**Decision:** Entity resolution uses alias lookup → embedding similarity →
LLM adjudication → human review for ambiguous cases, and never physically
fuses two nodes. Instead, `same_as` edges carry a continuous confidence
weight plus a separate reviewed/rejected flag; the retrieval layer treats
high-confidence edges as identity at query time while underlying nodes and
provenance remain distinct.
**Why:** A wrong destructive merge corrupts an existing entity's edges
irreversibly, which is worse than a wrong new-node addition (which just sits
unconnected until corrected). Confidence-as-edge-property is a cleaner,
more general mechanism than discrete match/no-match states, and reuses the
same property elsewhere (e.g. source authoritativeness) rather than adding
a bespoke state machine.
**Alternatives considered:**
- Discrete `possibly_same_as` / `confirmed_same_as` / `not_same_as`
  relationship types — rejected in favour of a continuous confidence weight
  as a simpler, more general mechanism.
- Auto-merge above a fixed confidence threshold — rejected; destructive
  merging is never acceptable regardless of confidence, since it's
  irreversible and traceability requires everything stay correctable.
**Open/deferred:** Exact confidence thresholds for auto-commit vs. review
vs. discard need real extraction output to tune — not resolved yet by
design, resolved by architecture (see `ARCHITECTURE.md`).

---

### Candidate `same_as` edges are never bulk-acceptable in the review queue
**Decision:** Unlike new nodes or mentions of existing data, candidate
entity-resolution merges always require individual review regardless of
review-queue volume.
**Why:** The asymmetry in risk (a wrong merge corrupts an existing entity;
a wrong new node just sits unconnected) justifies a stricter review bar for
this category specifically.

---

### AG-UI (agent-generated interface layout) deferred to post-v1
**Decision:** v1 entity detail views use a fixed, hand-built template per
entity type (Programme, Company, Hardware, Standard) with dynamic content.
Genuinely agent-generated layout is on the roadmap but not built now.
**Why:** Generative UI is the least technically proven part of the concept —
newer and less mature than agentic RAG — and increases the risk of
traceability being inconsistently rendered if the agent has full control
over what's shown and how. De-risking the harder ingestion/retrieval
problems first is a better sequencing choice for a solo, time-constrained
build.
**Trade-off accepted:** traceability is available via click-through rather
than guaranteed on the face of every generated view; acceptable since the
underlying linkage is always present and findable.

---

### Gap detection is an experimental workstream, not a v1 requirement
**Decision:** Structural/staleness-based gap detection, feeding a
user-prioritised list for agentic research, is scoped as research — worth
pursuing once the core system works, not a blocker to a usable v1.
**Why:** "What counts as a gap" isn't a property the system can read off
cleanly; it will need experimentation. It's also the part of the concept
addressing the *discovery* pain point specifically, which is real but
harder to build reliably than the *retention* pain point the core system
already solves.
**Consequence flagged:** the sharper-felt pain point (discovery) is not
what v1 delivers; don't let this be forgotten as the project progresses.

---

### No fixed latency budget for retrieval
**Decision:** Multi-step agentic retrieval with full provenance tracking is
acceptable even if it takes tens of seconds to a couple of minutes for
complex multi-hop questions.
**Why:** This is a research support tool used while working, not an
interactive system needing sub-second responses. Relaxing this constraint
materially de-risks the retrieval architecture.

---

### Graph exploration UI is node-centric, not full-graph overview
**Decision:** Users explore the graph by expanding one hop at a time from a
starting entity, never viewing a large rendered subset of the whole graph.
**Why:** Full-graph visualisation becomes an unreadable hairball at even
moderate scale (expected: low thousands of entities); clusters/whole-graph
structure aren't useful for this use case anyway — node-centric exploration
is a well-understood, tractable UI pattern at any scale.

---

### Infrastructure: cloud now, in-house later if classification changes
**Decision:** Build on cloud infrastructure (GCP under consideration) given
current scope is entirely open, public, non-personal data at a high level.
**Why:** No current sensitivity concern justifies the overhead of in-house
infrastructure. A future use case handling higher-classification material
would trigger a move in-house at that point, not now.
**Note:** Spanner Graph identified as a credible knowledge-store candidate
(native GQL + integrated vector search + GraphRAG support via LangChain),
but not locked in. Two adjacent GCP products considered and rejected as
poor fits: Google Knowledge Catalog (built for cataloguing existing
*structured* enterprise data — tables/schemas — not extracting a bespoke
ontology from unstructured documents) and BigQuery agent-analytics context
graphs (for logging an agent's own decision trace, not domain knowledge).

---

### Scope: purely internal tool, no commercial model
**Decision:** This is a personal side project for the owner's own use, with
a possible future life as an internal case study. Not a product, not sold,
not resourced as formal departmental investment.
**Why:** Reflects actual intent; removes the need to interrogate a
commercial model during triage or build.
**Resourcing risk acknowledged:** side projects stall when the day job
intensifies. Mitigation: keep v1 tightly scoped and build with a high
degree of automation (Claude Code-driven), per the milestone staging in
`ROADMAP.md`.

---

### Eval set is seeded at end of M0, not deferred to an unscheduled "later"
**Decision:** A formal extraction/entity-resolution eval set is created
using the manual review already being done in M0, at the M0/M1 boundary —
not left as an unscheduled future task.
**Why:** M0's exit criterion is deliberately subjective ("review would
plausibly be faster than by hand"), which is fine for a first pass, but
without a scheduled eval set, extraction and entity-resolution accuracy
can regress silently across M1–M3 with nothing to catch it. Labelling is
nearly free at the point manual review is already happening in M0 — the
cost of capturing it goes up the longer it's deferred.

---

### Source authoritativeness: rule-based default, not model-judged
**Decision:** Authoritativeness starts as a simple domain/rule table
(known institutional domains → high, established company sites → medium,
otherwise low), manually overridable, rather than LLM-judged from the
start.
**Why:** A rule table is transparent and cheap to correct; adding another
unreliable LLM inference for something this structurally simple isn't
justified yet. Revisit only if the rule table proves insufficient once
it's actually load-bearing (M2).

---

### `same_as` identity is evaluated per-edge, not via transitive closure
**Decision:** High-confidence `same_as` edges are traversed individually
at query time; the system does not automatically treat entities as
identical via chains of pairwise matches (A≈B, B≈C ⇒ A≈C). Any multi-node
clustering, if needed later, is an explicit, separately-reviewed step.
**Why:** Transitive closure over continuous confidence weights can chain a
false cluster together through a sequence of individually-plausible but
weak links, with no single edge wrong enough to be caught on review. This
is a correctness gap in the entity-resolution model, not just a query-time
detail — the M2 edge model must store pairwise edges (not a merged group
ID) precisely so this remains possible to get right in M3.

---

### Build approach: agentic, git-managed, infrastructure-as-code
**Decision:** Built primarily through Claude Code sessions, version
controlled in git, infrastructure managed via Terraform or equivalent
IaC tooling.
**Why:** Consistent with how the owner runs other personal side projects
(cf. Milton); keeps the build reproducible and auditable without manual
infrastructure fiddling.
