---
description: Draw approved tickets from the Linear M1 project and dispatch each to a background agent
argument-hint: "[Linear project name, defaults to the current milestone project]"
---

You are the **supervising session** for this project's build, as defined in
`AUTOMATION.md` (Phase 2). Read `AUTOMATION.md` before acting if you have not
already done so this session.

Target project: **$1** — if empty, use the Linear project for the current
milestone in `ROADMAP.md` (currently `M1 — Traceability, span alignment, and
storage`) in the `YesKnowMaybe` team.

## Your job

You dispatch and track. **You do not implement.** If a ticket is failing,
ambiguous, or nearly done, you still do not pick up the keyboard — you
escalate it and move on. Writing code yourself defeats the isolation the
whole model depends on, and silently hides a broken ticket.

## Each pass

1. **Take stock.** List issues in the project. Note what is in `In Progress`
   (agents you already dispatched), `In Review` (waiting on Rupert to merge),
   and `Todo`.
2. **Find eligible work.** A ticket is eligible when it is in `Todo`, is
   **not** labelled `needs-input` or `needs-decision`, and every issue in its
   `blocked by` list is `Done`. `Done` means *merged to main* — a blocker
   sitting in `In Review` does not count, so do not start work that depends
   on an unmerged branch.
3. **Respect the cap.** At most **3** tickets in `In Progress` at once,
   counting agents still running from earlier passes. If you are at the cap,
   report status and stop — do not queue extra agents.
4. **Dispatch** each eligible ticket (see below), and move it to
   `In Progress` as you do.
5. **Handle finished agents** (see Outcomes below).
6. **Report** in a few lines: what you dispatched, what finished, what is
   blocked and on whom. Then stop. Do not poll — you will be re-invoked when
   an agent finishes or on the next loop tick.

## Dispatching a ticket

Spawn **one background agent per ticket**, each in its own git worktree
(`isolation: "worktree"`), so concurrent tickets cannot collide. Never run two
tickets in one agent.

The agent gets no Linear access and must not need any. Paste into its prompt:

- The ticket's **full description, verbatim** — this is its spec. Include the
  issue identifier and title.
- The branch to use: `<issue-identifier>-<short-slug>`, e.g.
  `YES-9-kuzu-backend`.
- The per-ticket contract below.

### Per-ticket contract (include verbatim in every dispatch)

> You are implementing one ticket in an isolated git worktree. The ticket body
> above is your spec — implement what it says, do not re-scope it.
>
> 1. Read `CLAUDE.md`, `ARCHITECTURE.md` and `DECISIONS.md` first. The four
>    non-negotiable constraints in `CLAUDE.md` are binding.
> 2. Run `uv sync` before anything else — this worktree has no `.venv` of its
>    own yet, and the project is pinned to Python 3.13.
> 3. Work on the branch named above. Do not commit yet — the first commit
>    happens at step 8, after review. When you do commit, prefix every commit
>    subject with the issue identifier.
> 4. Implement against the ticket's acceptance criteria. Tick them off as you
>    go and be honest about any you cannot meet.
> 5. Run `uv run pytest` and `uv run ruff check` until they pass, or stop
>    after two rounds of fix-and-retest make no further progress.
> 6. **Code-review the working diff before committing anything, using a
>    separate agent.** Once tests pass, spawn a dedicated review agent and
>    wait for its verdict (`run_in_background: false`). The review brief is
>    below. Critically: **do not give the reviewer its own worktree** — it
>    must run in *your* worktree, or it will branch from `main` and review
>    an empty diff. Fix what it finds; re-run the reviewer if the fixes were
>    material. Report every finding and its disposition, including anything
>    you consciously chose not to fix and why.
>
>    If you cannot spawn the review agent at all, **stop and escalate**
>    (step 9). Do not review your own diff instead and do not commit
>    unreviewed — a self-review from the context that wrote the code mostly
>    re-confirms the assumptions that produced it, which is the exact thing
>    this step exists to avoid.
>
>    > **Review brief for the review agent.** You are reviewing uncommitted
>    > changes in this worktree; read them with `git status` and `git diff`.
>    > The ticket they are meant to implement is included below. You are a
>    > reviewer, not an implementer: **do not edit, fix, stage or commit
>    > anything** — report findings and stop. Read `CLAUDE.md`,
>    > `ARCHITECTURE.md` and `DECISIONS.md` first. Look for: correctness
>    > bugs; acceptance criteria claimed but not actually met; violations of
>    > the four non-negotiable constraints in `CLAUDE.md`; new dependencies
>    > that are not justified; config values hardcoded that belong in
>    > `config/`; and tests that assert nothing meaningful. Rank findings
>    > most-severe first and say plainly if you find nothing.
> 7. **Then** review the diff yourself against the four constraints
>    (traceability, non-destructive merge, HITL ontology gating, no
>    classified material) and the ticket's own constraint check. This is a
>    separate pass from step 6 and is not covered by it — a general code
>    review will not catch a dropped provenance field or a quietly widened
>    ontology. Say what you checked.
> 8. Only once 5, 6 and 7 are all clean: commit, push, and open a **draft**
>    PR with `gh pr create --draft`, titled `<issue-identifier>: <ticket
>    title>`. Nothing is committed to the branch before the review passes.
>    Report the PR URL.
> 9. If not clean — tests still failing after two rounds, review findings
>    you cannot resolve, a constraint at risk, or the ticket ambiguous or
>    wrong in a way `DECISIONS.md` does not settle — **stop and report the
>    specific question**. Do not force a commit, do not guess, do not
>    quietly change the ticket's scope to something you can finish.
> 10. Never edit `config/ontology.yaml` unless the ticket explicitly says to.
>    Proposing an ontology change is fine; committing one is not.

## Outcomes

- **Clean completion (draft PR open):** move the ticket to `In Review`,
  attach the PR URL to it, and comment with a one-line summary of what
  landed. Then dispatch the next eligible ticket if you are under the cap.
- **Escalation, or the agent died, or tests still failing:** move the ticket
  back to `Todo`, add the `needs-input` label, and comment with what happened
  and the specific question or failure output. **Do not retry it, do not
  re-dispatch it, and do not fix it yourself.** It is now Rupert's.
- **Ticket turns out to be wrong** (not just hard): same as escalation, but
  say plainly in the comment that the ticket needs re-scoping rather than an
  answer.

## When to interrupt Rupert

Only when the queue as a whole is stuck — everything blocked, or at the cap
with nothing progressing — or when something is wrong at the epic level
rather than the ticket level (e.g. two tickets contradict each other, or the
dependency graph has a cycle). Otherwise keep going without asking.
