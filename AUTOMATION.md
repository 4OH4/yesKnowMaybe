# Automation

How the build gets driven day-to-day: hand the backlog over once, let Claude
Code delegate each story into its own context, and only step in when
something's actually stuck. This replaces a hand-rolled tmux/story-cycle
setup — the mechanism below is native to Claude Code, not scripted.

## Mechanism

**Agent view + background subagents**, not the Workflows runtime.

- **Agent view** is a table of independent Claude Code sessions. Dispatching
  a story starts a new row with its own full context. Rows are grouped by
  state (**Needs input / Working / Completed**), with anything needing you
  pinned at the top — you scan and step in only where it matters.
- **Background subagents** run inside a session, concurrently, each in its
  own isolated context that reports only a summary back. Subagents run in
  the background by default and can commit, push, and open a draft PR
  themselves once they finish work in their own worktree.
- **The Notification hook** (`agent_needs_input` / `agent_completed`) fires
  when a session is stuck or done. Wire this to a desktop notification or a
  Teams/Slack webhook so a check-in doesn't depend on watching a screen.

**Why not Workflows or Agent Teams:** Workflows are a script-driven fan-out
of many same-shaped agents with no human touch until a final report — right
for later, bulk tasks (e.g. batch-tuning entity-resolution thresholds
across many candidate pairs once there's real data to tune against), wrong
for an ongoing build that needs occasional real judgement calls. Agent
teams are session-bound and ephemeral — no persistence, no resume — built
for live collaborative work in one sitting, not a build running across many
cycles while unattended. Neither fits "hand it over and get pinged when
stuck" as well as agent view.

## The handover prompt

Given to the lead session once, at the start of a work session (not
re-explained per story):

> Work through `BACKLOG.md` in `ROADMAP.md` order. For each item, dispatch
> it as its own background session:
>
> 1. Read `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md`, and the specific
>    backlog line.
> 2. Scope the item into a story with acceptance criteria (write it to
>    `stories/<slug>.md`).
> 3. Implement it in its own git worktree.
> 4. Run tests/lint until they pass, or stop after two rounds make no
>    further progress.
> 5. Review the diff specifically against the four non-negotiable
>    constraints in `CLAUDE.md` (traceability, non-destructive merge, HITL
>    ontology gating, no classified material) — not just general code
>    quality.
> 6. If clean: commit, push, open a draft PR.
> 7. If not clean, or a constraint is at risk, or the story is genuinely
>    ambiguous: stop, do not force a commit, and flag it rather than
>    guessing.
>
> Keep no more than 3 stories running at once; dispatch the next when one
> finishes. Don't wait for my approval between stories unless something's
> flagged under step 7.

## Per-story contract

Every dispatched session/subagent operates under the same contract,
regardless of which backlog item it's handling:

1. **Own context, own worktree.** No shared state with other in-flight
   stories; conflicts are structurally avoided, not resolved after the
   fact.
2. **Test-until-pass, bounded.** Two rounds of fix-and-retest; a third
   failure escalates rather than looping indefinitely.
3. **Review against the four constraints explicitly**, not just "does this
   look like reasonable code" — this is the check that actually protects
   traceability and non-destructive merging as the project grows past
   anyone reading every diff by hand.
4. **Commit small, per story** — consistent with the existing preference
   for small, frequent commits; one story is one reviewable unit.
5. **Escalate rather than guess.** Any of: tests not passing after two
   rounds, a constraint genuinely at risk, or real ambiguity not resolved by
   `DECISIONS.md` — the session stops and surfaces the question instead of
   picking an answer and moving on.

## Concurrency

Cap at 3 concurrent stories. This is a stated instruction to the lead
session, not a script parameter — it's a modest-parallelism preference
(faster than fully sequential BMAD cycles, without needing to reason about
larger-scale fan-out or its cost).

## Checking in

- **Agent view**, scanned periodically: "Needs input" rows are pinned at
  the top — that's the whole check-in, no transcript-reading required.
- **Notification hook**, for not having to watch the screen: wire
  `agent_needs_input` to a Teams/Slack webhook or desktop notification, so
  a stuck story surfaces on its own.
- **Completed, clean stories** need no check-in at all — commit, push, and
  draft PR happen without you, per the per-story contract above. Reviewing
  merged PRs periodically is a separate, lighter task from babysitting the
  build itself.

## Where this sits relative to the rest of the project

- Follows `ROADMAP.md` ordering — the lead session shouldn't jump milestones
  out of sequence even if a later backlog item looks quick.
- Draws its non-negotiable review checklist directly from `CLAUDE.md`.
- `stories/` (written by the scoping step) is the closest equivalent to a
  BMAD story file, but generated and consumed automatically rather than
  hand-curated per cycle.
- Revisit **Workflows** once there's a genuine bulk, no-judgement-needed
  task — e.g. once M2's entity resolution is live and there's a backlog of
  candidate-duplicate pairs to batch-score, or an M0-style extraction audit
  across many documents at once. Not needed for driving the milestone build
  itself.
