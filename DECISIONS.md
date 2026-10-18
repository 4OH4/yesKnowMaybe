# Decisions

A log of key architectural and scope decisions, so later work doesn't
re-litigate settled questions. New entries go at the top. Each entry: what
was decided, why, and what alternatives were considered.

---

### Extraction prompt: supporting quotes must be one contiguous passage, changed before the M1 re-extraction
**Decision:** Approved (HITL, #23) — rule 3 of the extraction system
instruction (`src/dikms/extraction/prompt.py`) now also says the quote must be
one contiguous passage of the chunk, never phrases joined together from
separate places, and that where the support is spread out the model quotes
the single most supportive passage. It also says never to skip words with an
ellipsis (`...` or `…`): a quote may contain one only where the chunk itself
does. That is not a blanket ban, because sources contain genuine ellipses (the
FADS article's one ellipsis quote is a real `…` in the source). No other
rule, and nothing in the per-chunk user prompt or the response schema,
changes. The change lands before the M1 re-extraction (#11), which now
depends on it.
**Why:** span alignment (#4) locates each fact's quote in its chunk to derive
a verified offset. Most of the facts it could not place had a *stitched*
quote, phrases from different parts of the chunk joined into one string,
which cannot be found as a single span: 55 of the DIP's 62 unresolved facts
and 5 of the MOD Digital Strategy's 10, as counted in the independent review
of PR #16 and recorded in that PR's `notes/M1-alignment.md`. These figures,
and the 72 below, are taken from that note as of a571455, after its recount;
a later recount can be traced from there. One stitching
pattern is explicit elision, where the model marks the join with an ellipsis
(e.g. `current and future warhead...programmes`): 31 of the DIP's 1,735
quotes and 5 of the MOD's 487 contain an ellipsis, and none of those 36 is
found in its chunk. The old rule asked for a short verbatim span but never
said it had to be contiguous. Changing the prompt before #11 means one
credentialled run both refreshes the output under the current ontology and
measures the effect on alignment rates.
**Consequence (comparability with M0):** the M0 comparison was already
prompt-confounded before this change. The MOD and DIP outputs (2,222 of the
2,291 M0 facts) were extracted between 14:19 and 14:59 UTC on 2026-07-24;
rules 6 (no named people, extract the Role) and 7 (prefer broad types, don't
propose instance types) arrived with ontology v2 in d201324 at 15:08 UTC.
Only the FADS output (69 facts) was produced under today's rules 1–2 and
4–8. So #11's output differs from most of the M0 output in the ontology, in
rules 6 and 7, and now in rule 3, and its comparison note cannot attribute a
change in a prompt-affected metric (entity/relation counts by type,
proposals, alignment rates) to the ontology alone. The note must say so, and
#11's output becomes the baseline for later comparisons rather than M0's.
Extraction is also non-deterministic run to run (`notes/M0-review.md`), so
the M0 comparison was indicative only.
Stitched quotes still keep document/section-level provenance, so this is an
exact-span improvement, not a traceability fix.
**Alternatives considered:** leave the prompt alone for #11 and change it
afterwards — this was presented as keeping #11 a clean ontology-only
comparison, but that comparison was never available, because rules 6 and 7
already separate the prompt behind most of the M0 output from today's; it
would still have needed a second credentialled run to measure the prompt
change. Don't change it — accept about 3% of facts without an exact span
(72 of the 2,291 M0 facts, 3.1%). Both rejected: deferring buys no
comparability, a second run costs more, and the stitching is cheap to ask the
model to stop.

---

### `extracted_from` covers extracted entities only; relations carry provenance as properties
**Decision:** Approved (HITL, #28): the `extracted_from` description in
`config/ontology.yaml` now says the edge runs from an extracted entity to its
`Source`, one per extraction, and that a relation has no such edge but carries
the same provenance block as its own properties and traces back through its
`source_id`. Only the description changes. `domain` stays `["*"]`, which ranges
over node labels anyway, and `range`, `structural` and every other type are
untouched. The ontology stays at **version 3**: the file's rule is to bump
"whenever a type is added, removed or materially redefined", and this corrects
the prose to match the model the store already implements rather than
redefining the type. Nothing derived from the ontology changes:
`extracted_from` is structural, so it is not in the extraction prompt, and the
storage schema built from the file is identical before and after.
**Why:** A property-graph edge cannot start from another edge, in Kùzu or
Spanner Graph, so the storage schema from #3 (PR #15) limits `extracted_from`
to entity endpoints and has each relation carry `source_id`, `chunk_id` and
the rest of the provenance block itself (ARCHITECTURE.md → Knowledge store →
Schema decisions). The old wording, "from any extracted entity/relation",
promised relation→`Source` edges that cannot exist, and the ingestion path
(#10) had been specified against it; its acceptance criterion is amended to
match. Full traceability (constraint 1) is unaffected: every relation still
resolves to its `Source` through its own `source_id`.
**Alternatives considered:** leave the wording — rejected, the ontology would
disagree with the schema and anyone reading it would expect edges that don't
exist; reify each relation as a node so it can carry an `extracted_from` edge
— rejected, it changes the graph's shape for every fact to recover provenance
the relation already holds as properties.

---

### Build automation: GitHub Issues and a Project board, run by lead/worker/reviewer agents; Linear dropped
**Decision:** The backlog moves from Linear to GitHub Issues on this repo,
tracked on a GitHub Project board (Backlog → Ready → In progress → Agent
review → Human review → Done). A lead session (`/lead-start`,
`/lead-finish`) runs the board with up to three worker agents and an
independent, read-only reviewer per PR, adapted from the news-bot repo. Rupert
approves work by merging PRs, `needs-human` ones included. This replaces both
the Linear two-phase model (`/supervise`) and the interim rule that agents stop
before committing. Mechanics are in `AUTOMATION.md` and `CLAUDE.md`.
**Why:** Linear's MCP connection kept needing re-authentication and the board
drifted from reality: YES-6 sat in In Review with no PR, and YES-5 was
labelled needs-input only because the agent hit an account spend limit, with
its work left uncommitted in a worktree. The news-bot setup keeps state next
to the code, needs only `gh`, and bounds review cost (two rounds, Defect vs
Follow-up), which YES-6 showed was needed.
**Alternatives considered:** keep Linear and fix the connection — rejected,
the overhead wasn't paying its way for a solo project; return to
`stories/*.md` — rejected, no state, relations or PR links.

The M1 tickets were exported from Linear and recreated unchanged in substance.
Git history before this point uses the Linear identifiers:

| Linear | GitHub | Title |
| --- | --- | --- |
| — | #2 | Run the build from GitHub Issues and a Project board |
| YES-5 | #3 | Storage-adapter interface and provenance-bearing schema |
| YES-6 | #4 | Post-hoc span alignment |
| — | #5 | Ontology v3 (the decision behind PR #1) |
| YES-7 | #6 | Relation domain/range validation |
| YES-8 | #7 | Extraction run ergonomics |
| YES-9 | #8 | Kùzu backend |
| YES-10 | #9 | Spanner Graph backend |
| YES-11 | #10 | Ingestion path |
| YES-12 | #11 | Re-extract the corpus (needs-human) |
| YES-13 | #12 | Trace-back query tool |
| YES-14 | #13 | Eval set as a regression check |
| — | #14 | CI and the pre-existing ruff failures |

---

### Ontology v3: four relation domain/range constraints widened, the rest held
**Decision:** Approved (HITL, YES-7) — widen four declared constraints to match
observed valid usage, and bump the ontology to **version 3**:

| Relation | Change |
|---|---|
| `develops` | `domain` += `Programme` |
| `delivers` | `domain` += `Programme` |
| `part_of`  | `range`  += `Hardware` |
| `funds`    | `range`  += `Organisation` |

All other domain/range violations observed in the M0 corpus are **deliberately
left as violations**, to be surfaced as warnings by the validator (YES-7) rather
than legislated away.

**Why:** A domain/range check over the three committed `output/*.json` files
(628 relations, current ontology) flagged 70 violations — 11.1%. Of those, 42
are cases where the extraction is plainly right and the ontology was plainly too
narrow: a programme *does* deliver hardware ("Project NYX delivers armed
autonomous drones", ×21), a subsystem *is* part of a platform ("APKWS part_of
Typhoon", ×7), a government *does* fund an agency ("UK Government funds DSTL",
×7). Declaring those invalid would generate persistent noise against correct
output and train the reader to ignore the warning channel.

The remaining 28 are held as violations on purpose, because each is a real
quality signal rather than an ontology gap:
- `implements_standard` from `Organisation` (×4) is really flagging that
  treaties (Polaris Sales Agreement, Northwood Declaration) are being mistyped
  as `Standard` — an entity-typing problem, not a relation-scope one.
- `range: Term` on `develops`/`delivers`/`funds` (×8) is extraction slop
  ("develops → processes"), and several such cases should resolve to
  `Capability` under v2's type set.
- `part_of` with a `Term` domain (×7) is the Set-C over-granularity the M0
  review explicitly declined to accommodate.

**Evidence caveat:** only the FADS output (43 of 1,548 entities) post-dates
ontology v2; the two policy PDFs were extracted before `Capability`, `Location`
and `Role` existed. This mainly inflates the `range: Term` buckets, which are
*not* being widened, so it does not undercut the four changes. The clean v2/v3
baseline comes from the YES-12 re-extraction.

**Alternatives considered:** widen everything to silence all 70 — rejected, it
discards the signal the validator exists to produce and would bless genuine
mistyping; hold all four and accept 11% standing warnings — rejected, warning
channels that are mostly false positives stop being read; defer the whole call
until after YES-12 re-extracts under v2 — rejected, the four changes are
orthogonal to the v2 type additions (they concern `Programme` domains and
`Hardware`/`Organisation` ranges, not the `Term`→`Capability` shift), so the
extra round trip buys nothing.

---

### Project managed with uv, pinned to Python 3.13
**Decision:** The project uses **uv** for Python, virtualenv and dependency
management. Python is pinned to **3.13** via `.python-version`; `uv.lock` is
committed for reproducible resolution. Dev tooling (pytest, ruff) is a uv
`[dependency-groups]` group, not a shipped optional extra. The hatchling build
backend is unchanged.
**Why:** The embedded store backend (kuzu) has no cp314 wheel yet and its source
build is heavy, so the working 3.14 venv could not install it — the project
needs a supported interpreter now that kuzu is a core dependency. uv pins the
interpreter and locks the dependency set in one tool, which matters for the
unattended-automation model (a background session gets the exact same
environment from `uv sync`, no drift). `requires-python` is capped at `<3.14`
to match; lift once kuzu ships a 3.14 wheel.
**Alternatives considered:** stay on 3.14 and wait for a kuzu wheel — rejected,
blocks M1 storage work now; plain venv+pip — rejected, no lockfile and no
interpreter pinning, weaker reproducibility for automated runs.

---

### Knowledge store: Spanner Graph is the canonical target, behind a thin storage-adapter interface with Kùzu as an embedded backend
**Decision:** Lock the M1 storage direction as **Spanner Graph** (native GQL,
integrated vector search, GraphRAG-ready) as the canonical/production target,
but build all ingestion, provenance-write, and query code against a **thin
storage-adapter interface** — not against Spanner APIs directly. **Kùzu**
(embedded, Cypher, native vector index, zero running infrastructure) is a
first-class backend behind that same interface. The adapter surface is the
minimum the architecture actually needs: node/edge upsert with arbitrary edge
properties (confidence, source, timestamp), multi-hop traversal, and vector
similarity over the same data. `same_as` is stored as pairwise edges in both
backends — never a merged group id — per the transitivity decision below.
**Why:** Spanner keeps the store consistent with the Vertex extraction platform
and is the right long-term home, but it bills continuously with no
scale-to-zero and forces a human provisioning + service-account checkpoint into
every session — which fights the unattended-automation model in `AUTOMATION.md`.
An adapter with an embedded Kùzu backend lets background sessions build schema,
write real data, run trace-back queries, and pass tests with no credentials and
no provisioned instance, while the Spanner backend is exercised when credentials
are present. The adapter is cheap insurance regardless: at low-thousands scale
the store is not the hard part, and not hard-coupling to one vendor keeps the
Spanner cost/lock-in question reversible.
**Alternatives considered:**
- Spanner Graph only (as previously documented) — rejected: sole reliance forces
  a human checkpoint into automated M1 and pays for an idle distributed DB at a
  non-distributed scale.
- Embedded store only (Kùzu / Postgres+pgvector+AGE) — rejected as the *sole*
  target: gives up the GCP consistency and the intended production home; kept as
  a backend, not the destination.
**Consequence:** M1's first task becomes *define the adapter interface*, then
implement the Kùzu backend (unattended-buildable) and the Spanner backend behind
it. Schema must be expressible in both (property graph + vector column);
anything that only works in one backend is a design smell to flag. Provisioning
the Spanner instance and its credentials remains a manual human step (per
`SETUP.md`), not an automated story.
**HITL note:** owner-approved at the M1 kickoff decision point.

---

### Embeddings: Vertex embeddings for the vector channel and entity resolution
**Decision:** The vector channel (M1 storage) and entity-resolution shortlisting
(M2) run on **Vertex embeddings** (`gemini-embedding`-class model via Vertex
AI), consistent with the Gemini/Vertex extraction platform. The exact model and
output dimensionality are a sub-decision to pin before schema is frozen (the
store's vector column width depends on it) — default to a single fixed dimension
across the corpus and record it in `config/extraction.yaml` alongside the
extraction model.
**Why:** Keeps the pipeline on one model provider/platform rather than mixing in
a local embedding stack, and gives higher-quality vectors for resolution than a
small local model. Per-call cost is negligible at this corpus scale (consistent
with the M0 extraction cost finding).
**Trade-off accepted:** embeddings need the same Vertex credentials as
extraction, so the entity-resolution loop is not fully runnable in a
credential-free unattended session — acceptable, since resolution is M2 and the
adapter's Kùzu backend still lets M1 storage/query be built and tested
unattended with stub or cached vectors.
**Deferred:** exact model + output dimension (768 vs larger); revisit only if
resolution quality or storage cost makes the default wrong.

---

### Ontology v2: capability/location/role added, governance + investment relations; named individuals out of scope
**Decision:** From the M0 review (see `notes/M0-review.md`), ontology bumped to
version 2:
- **New entity types:** `Capability` (an ability to achieve an effect, distinct
  from the delivering programme and the realising hardware), `Location` (one
  type covering sites/facilities/infrastructure), and `Role` (an organisational
  post).
- **New relations:** `governs`, `member_of` (organisational governance);
  `invests_in`, `supports` (investment). `part_of` extended to organisations,
  `develops`/`delivers`/`funds` extended to cover `Capability`.
- **Named individuals are out of scope.** People are NOT extracted or stored;
  where a named person holds a post, only the `Role` is captured. Enforced in
  the extraction prompt.
- **Over-granular proposals rejected** (specific technologies, fighter
  generations, sub-strategies). Handled by prompt guidance steering such items
  to existing broad types, not by adding entity types.
**Why:** Both policy documents (MOD Digital Strategy, Defence Investment Plan)
independently proposed the governance/investment relations and a `Capability`
type — strong convergent evidence of a real gap, not one document's quirk.
`Role` without `Person` keeps the useful organisational structure (who holds
what post) while avoiding a store of named individuals, which adds personal-data
handling obligations for no benefit to a programme/capability knowledge tool.
**Alternatives considered:**
- Adopt `Person` as well as `Role` — rejected by the owner; named individuals
  are deliberately excluded.
- Separate `Facility`/`Infrastructure`/`Site` types — rejected in favour of one
  `Location` type; the distinctions weren't worth the ontology weight.
- Adopt the long tail of overlapping relation verbs and granular entity
  proposals — rejected; consolidated / steered via prompt guidance instead.
**Deferred (not in v2):** `Person`; procurement relations (`procures`,
`supplies`, …); domain/range validation of relations; proposal dedup.

---

### `Organisation` replaces `Company`; kind and nationality are attributes
**Decision:** Merge `Company` into a broader `Organisation` entity type
covering governmental and non-governmental bodies alike (companies,
departments, military commands, agencies, academic institutions,
multinational bodies). The kind is a controlled-vocabulary `org_type`
attribute (government/military/commercial/academic/ngo/multinational/other),
with a free-text `nationality` attribute (controlling nation, or
"multinational", omitted when global). Ontology bumped to version 1.
**Why:** The first real M0 extraction run (Naval News/FADS) proposed exactly
this: "Royal Navy", "Navy Command", "UK Strategic Command" fit neither
`Company` nor `Programme`. It also exposed a pre-existing inconsistency — the
`funds` relation already referenced a "government body" source with no such
entity type to originate from. One umbrella type with a typed attribute is
cleaner than parallel `Company`/`GovernmentBody`/... types, which would
reintroduce the entity-type ambiguity the model was tripping over.
**Alternatives considered:**
- Add `GovernmentBody` alongside `Company` (as the model first proposed) —
  rejected: two overlapping org types invites mistyping and multiplies as
  new org kinds appear (academic, NGO, multinational).
- Sub-types as distinct entity types — rejected for the same reason; an
  attribute keeps one node type and lets `org_type` be filtered at query time.
**HITL note:** approved by the owner (constraint 3). No data migration —
nothing is stored yet (pre-M1). `funds`/`develops`/`delivers` domains updated
from `Company` to `Organisation`.
**Deferred:** attribute-level provenance (an entity carries one provenance
for now); revisit if per-attribute sourcing is needed.

---

### Config and hand-authored data files are YAML
**Decision:** Human-authored configuration and data — the ontology, the
source manifest, extraction/pipeline config — are YAML. This settles the
format left provisional when the source manifest was first added as JSON.
**Why:** These files are edited by hand and tuned repeatedly (see the
config-over-hardcoding convention), so inline comments and low syntactic
noise matter; JSON has neither. PyYAML is a single, well-established
dependency, consistent with the light-dependency convention. Applies to
config/data only — machine-to-machine interchange and stored pipeline
output stay JSON (stdlib, no dependency, schema-stable).
**Consequence:** the initial `data/sources.json` is migrated to
`data/sources.yaml`.

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
