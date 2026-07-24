# M0 extraction review and findings

Manual review of the first full extraction pass over the M0 corpus. This is the
seed of the eval set (DECISIONS.md: eval set timing) and the input to the next
ontology revision. Ontology is **version 1** (post the `Organisation` change).
Extraction model: `gemini-2.5-flash` via Vertex (europe-west2), temperature 0.

Nothing in the "ontology findings" below has been applied — every proposed type
is HITL-gated and awaits sign-off.

## What was run

| Source | Type | Chunks | Entities | Relations | Proposals (unique) | Warnings | Failed |
|---|---|---|---|---|---|---|---|
| MOD Digital Strategy (2021) | policy PDF | 18 | 363 | 85 | 39 (35) | 0 | 0 |
| Defence Investment Plan | policy PDF | 44 | 1142 | 517 | 76 (55) | 0 | 0 |
| Naval News — FADS/Type 83 | capability HTML | 1 | 40 | 11 | 0 | 0 | 0 |

Corpus totals: ~1545 entities, ~613 relations, 115 proposals.

## Verdict against M0 exit criteria

- **Usable output — yes.** Entities are well-typed and grounded; every fact
  carries a verbatim supporting quote and full document/section provenance.
  Review is plainly faster than extracting by hand. M0's core question
  ("does ontology-constrained extraction produce sensible output on real,
  messily-parsed material") is answered affirmatively.
- **Parsing/chunking did not materially degrade extraction** — spot-checks
  against the sources hold up; the text-native PDFs came through cleanly.
- **Attribute typing works.** `Organisation.org_type` respected the controlled
  vocabulary (DIP: government 183, military 92, multinational 31, commercial 16,
  academic 3); `nationality` sensible (UK dominant, plus Norway/Russia/Ukraine/
  multinational). The `Organisation` merge resolved the government-body gap —
  the Naval News re-run dropped from 1 proposal to 0.

## Quality observations

- **Document type drives the entity profile.** The capability article (FADS)
  produced hardware/programme-dense output cleanly. The policy PDFs are
  governance- and abstraction-heavy: Term- and Organisation-dense, and they are
  where the ontology strains (see below).
- **Non-determinism.** Two runs of the Naval News article at temperature 0 gave
  27/21 then 40/11 entities/relations. The eval baseline must judge against a
  tolerance, not exact match, and ideally capture 2+ runs per document.
- **`part_of` is overloaded** to Hardware→Hardware and Organisation→Organisation,
  outside its declared range. The validator only checks that types *exist*, not
  that they satisfy declared domain/range — so this passes silently.
- **Proposals recur per chunk** (115 total, ~79 unique) because each chunk
  proposes independently. A dedup step before review is needed.

## Ontology findings — proposals digested (HITL, not applied)

Grouped by strength of evidence. Cross-document = proposed independently by both
policy PDFs.

### A. Adopt — strong, convergent evidence
- **Governance relations** (cross-document): `governs`, `member_of`, org→org
  `part_of` (or a distinct `part_of_organisation`). The policy docs are largely
  about who oversees/belongs to/reports to what, and this is currently
  inexpressible.
- **`Capability` entity type** (DIP, heavy). "Capability" is core defence
  vocabulary currently forced into Programme/Term/Hardware. Highest-value single
  addition.
- **Investment/funding relations** (cross-document): `invests_in`, `supports`,
  and extending `funds` — the DIP is an investment document; these are its
  primary content.

### B. Consider — real but needs a design call
- **`Person` / `Role`** (MOD): named post-holders (e.g. Defence CIO). Genuine
  gap; decide whether people are in scope for this system at all.
- **`Location` / `Facility` / `Infrastructure`** (DIP): bases, shipyards,
  estate. Likely one `Location`/`Site` type rather than three.
- **Procurement relations**: `procures`, `supplies`, `acquires_from`,
  `operates_under` — coherent industrial-supply cluster; consolidate the
  near-duplicates rather than adopt verbatim.
- **Relax declared domain/range** on `part_of` / `delivers` / `funds` to match
  observed valid usage (and add domain/range validation to catch the rest).

### C. Reject / over-granular — do not add as types
- Sub-strategy and generation entities: `5G`, `Cloud`, `Data`, `Defensive
  Cyber`, `4th/5th/6th generation fighters`, `autonomous air systems`. These are
  `Programme`/`Term`/`Hardware` instances; the model wants finer entity
  distinctions than are worth having. Tighten prompt guidance on the
  Programme/Strategy/Term boundary instead.
- Overlapping relation verbs: `collaborates_on`/`collaborates_with`,
  `implements_concept`/`implements_programme`, `delivers_term`, `related_to`,
  etc. Consolidate into a small controlled set; do not adopt the long tail.

## Pipeline findings (engineering, separate from ontology)

- **Fixed:** one failed chunk aborted the whole document (429 quota mid-DIP).
  Now retries 429/503 with backoff and isolates per-chunk failures with partial
  output — see commit history.
- **Fixed:** ToC lines were detected as section headings and bled across body
  chunks; excluded lines ending in a page number.
- **Open (minor):** no per-chunk progress logging — long runs (DIP: 44 chunks)
  are opaque. A `--verbose` counter would help.
- **Open:** Vertex default Gemini quota on a new project is low; the full corpus
  tripped it. Request a quota increase before larger runs, or add request pacing.
- **Open:** add domain/range validation (see `part_of` overload above).
- **Open:** proposal dedup/aggregation before review.

## Open decisions for the owner

1. Approve set **A** for the next ontology revision (version 2)?
2. Scope calls on set **B**: are `Person`/`Role` in scope? One `Location` type or
   several? Which procurement relations make the cut?
3. Confirm set **C** is rejected and handled via prompt guidance, not new types.
4. Engineering priorities: quota increase, domain/range validation, proposal
   dedup, progress logging — which before M1?
