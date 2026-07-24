# Working instructions for Claude Code

This file is for you, the coding agent. Read it before starting work. Read
`ARCHITECTURE.md` and `DECISIONS.md` before making any structural change, and
`ROADMAP.md` before starting a new milestone.

## Who this is for and how to work

This is Rupert's personal side project, built almost entirely through agentic
coding sessions rather than by hand. He is a senior, technically fluent
engineer — don't explain concepts unless asked, don't hedge, push back if a
request seems to conflict with the architecture or decisions log rather than
silently complying.

## Conventions

- **Language:** British English in all comments, docs, commit messages, and
  any user-facing text.
- **Stack:** Python for the ingestion/extraction/retrieval backend (the
  GraphRAG and agent-orchestration ecosystem is Python-first). Settled as
  the primary language (see `DECISIONS.md`); flag there if any component
  needs to diverge.
- **Dependencies:** keep light. Prefer the standard library and a small
  number of well-established packages over pulling in a framework for
  something that can be written directly. Justify any new dependency in the
  commit message.
- **Testing:** no TDD in early exploratory stages (M0–M1). Once the
  extraction pipeline and storage layer have stabilised (post-M1), move to
  opportunistic testing — add tests for things as they prove worth
  protecting, not as a blanket policy from day one.
- **Commits:** small and frequent. Each commit should represent one
  coherent, reviewable change. Don't batch unrelated work.
- **Config over hardcoding:** ontology definitions, confidence thresholds,
  and source-authoritativeness weightings should live in config/data files,
  not embedded in code, since these will be tuned repeatedly based on real
  extraction output.

## Non-negotiable design constraints

These come from the concept triage and decisions log — do not silently
work around them:

1. **Full traceability.** Every extracted fact must carry a link back to its
   source document, section/span, and extraction timestamp. No exceptions,
   even in early prototypes — retrofitting provenance later is much harder
   than building it in from M0.
2. **Never destructively merge entities.** Entity resolution produces
   confidence-weighted `same_as` edges between distinct nodes, never a
   physical fusion. See `ARCHITECTURE.md` for the full model.
3. **HITL gating on ontology changes.** The agent may propose new entity or
   relation types; it must never commit them without explicit human
   approval.
4. **No client-identifying or classified/export-controlled material.** This
   system only ever ingests open, public sources.

## Backlog usage

`BACKLOG.md` holds one-line items — intent only, no detail. When briefing you
on a specific item, Rupert will add constraints and context conversationally
at that point; don't expect the backlog line itself to be a full spec. Ask if
something is genuinely ambiguous, but default to the architecture and
decisions log for anything already settled there.

## When you hit an open question

Check `DECISIONS.md` first — it may already be settled, including "flagged
as open, needs experimentation" entries. If genuinely unresolved, propose an
approach and flag it as needing sign-off rather than picking silently and
moving on, particularly for anything touching the four constraints above.
