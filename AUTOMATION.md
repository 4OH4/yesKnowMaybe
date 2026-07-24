# Automation

How the build gets driven day-to-day. The unit of automation is the **epic**
(a milestone in `ROADMAP.md`), run in two phases: author the whole epic's
tickets up front in **Linear**, review and approve them there, then hand the
approved set to a supervising session that draws tickets off the queue and
delegates each to its own background agent working on a branch. Step in only
when something's actually stuck. This is native to Claude Code plus the
Linear MCP server, not a scripted tmux/story-cycle setup.

## Two-phase model

Scoping is a distinct up-front phase for the entire epic, gated by human
approval, and separated from implementation.

1. **Phase 1 — Ticket authoring.** At the start of an epic, one authoring
   pass expands *every* `BACKLOG.md` line under that milestone into a
   detailed Linear issue — task description and acceptance criteria — in the
   epic's Linear project. Nothing is implemented yet.
2. **Approval gate (HITL).** Rupert reviews and edits the tickets in Linear
   and moves each from `Backlog` to `Todo`. `Todo` *means* approved.
3. **Phase 2 — Implementation.** A supervising session draws approved
   tickets off the queue in dependency order and dispatches each to a
   background agent that works it on its own branch. Agents implement
   against a ticket they're *given*; they don't scope their own.

The point of the split: the judgement-heavy part (what the work actually is)
happens once, up front, where it's cheap to review a coherent set together —
rather than trickling out one improvised scope at a time inside sessions
that are also trying to write code.

## Where things live

Tickets replaced the previous `stories/*.md` files. Linear is the working
state of the build; the repo docs stay the source of *intent* and settled
design.

| Concept | Lives in |
| --- | --- |
| Milestone / epic (M1, M2, …) | Linear **project**, one per milestone, in the `YesKnowMaybe` team |
| One reviewable unit of work | Linear **issue** in that project |
| Sequencing | Issue **`blocked by` relations**, not a prose "depends on" field |
| Approval state | Issue **workflow state** (see below) |
| Open question raised while authoring | Label `needs-decision` + the question stated in the ticket's Approach section |
| Agent stuck mid-implementation | Label `needs-input` + a comment on the ticket |
| The work itself | A git branch and draft PR, linked back to the issue |

One-line intent still starts in `BACKLOG.md`; it is not kept in sync with
ticket status afterwards. Once an epic is authored into Linear, Linear owns
status — tick the backlog lines off in one pass when the milestone closes.

**Workflow states** (the team's existing set, given fixed meanings):

- `Backlog` — drafted by the authoring pass, **not** yet approved. Never dispatched.
- `Todo` — approved by Rupert. Eligible for dispatch once its blockers are `Done`.
- `In Progress` — a background agent holds it.
- `In Review` — implemented, draft PR open, awaiting Rupert's review/merge.
- `Done` — merged.

## Mechanism

**Agent view + background subagents + the Linear MCP server**, not the
Workflows runtime.

- **Linear** is the durable queue. It survives across sessions, which is the
  thing `stories/*.md` was standing in for — but with real state, relations,
  and a UI to review in.
- **Agent view** is a table of independent Claude Code sessions. Dispatching
  a ticket starts a new row with its own full context. Rows are grouped by
  state (**Needs input / Working / Completed**), with anything needing you
  pinned at the top.
- **Background subagents** run inside a session, concurrently, each in its
  own isolated context and worktree, reporting only a summary back. They can
  commit, push, and open a draft PR themselves.
- **The Notification hook** (`agent_needs_input` / `agent_completed`) fires
  when a session is stuck or done. Wire this to a desktop notification or a
  Teams/Slack webhook so a check-in doesn't depend on watching a screen.

**Who touches Linear:** only the authoring session (Phase 1) and the
supervising session (Phase 2). Background implementation agents get the
ticket body pasted into their dispatch prompt and need no Linear access —
the supervisor owns every state transition and comment on their behalf. That
keeps ticket state consistent even when an agent dies mid-run.

**Why not Workflows or Agent Teams:** Workflows are a script-driven fan-out
of many same-shaped agents with no human touch until a final report — right
for later, bulk tasks (e.g. batch-tuning entity-resolution thresholds
across many candidate pairs once there's real data to tune against), wrong
for an ongoing build that needs occasional real judgement calls. Agent
teams are session-bound and ephemeral — no persistence, no resume — built
for live collaborative work in one sitting, not a build running across many
cycles while unattended. Neither fits "hand it over and get pinged when
stuck" as well as agent view.

## Phase 1 — the authoring prompt

Given to a single authoring session at the start of an epic. It writes
tickets, it does **not** implement anything.

> We're starting <milestone, e.g. M1>. Author the full ticket set for it in
> Linear.
>
> 1. Read `CLAUDE.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `ROADMAP.md` (the
>    milestone's section and exit criteria), and every `BACKLOG.md` line
>    under that milestone.
> 2. Create (or reuse) a Linear project named `<milestone> — <short title>`
>    in the `YesKnowMaybe` team, with a description summarising the
>    milestone's exit criteria.
> 3. For each backlog item — or coherent group of items that forms one
>    reviewable unit — create an issue in that project, in state `Backlog`,
>    following the ticket shape below exactly. Fill every section.
> 4. Sequence them with `blocked by` relations so the implementation order
>    is explicit and respects `ROADMAP.md`. Don't invent work beyond the
>    milestone's exit criteria; if the backlog looks under- or over-scoped
>    for those criteria, flag it rather than silently adjusting.
> 5. Where scoping a ticket forces a decision not already settled in
>    `DECISIONS.md` — especially anything touching the four non-negotiable
>    constraints — state it in the ticket's Approach section, add the
>    `needs-decision` label, and repeat it in your summary. Do not resolve
>    it silently.
> 6. Stop when the set is drafted. Do not implement, commit code, or open
>    PRs. Leave everything in `Backlog`. Summarise the set — identifiers,
>    titles, dependency order — and surface every flag for review.

## Ticket shape

Every issue's description follows this structure. Keep it tight: a ticket is
a contract for one reviewable unit of work, not a design essay — link to
`ARCHITECTURE.md` / `DECISIONS.md` rather than restating them.

```markdown
**Backlog line(s):** <verbatim BACKLOG.md item(s) this derives from>

## Intent
One short paragraph: what this delivers and why it exists now. Phrase as the
capability gained, not the tasks performed.

## Scope
- In scope: <what this does>
- Out of scope: <what it deliberately does not do — push to another ticket
  or a later milestone rather than letting scope creep>

## Approach
Intended implementation direction, grounded in the architecture and
decisions log. Cite sections rather than re-deriving settled choices. Flag
any call the author had to make that isn't already settled there.

## Acceptance criteria
- [ ] <specific, checkable outcome>
- [ ] <…>
- [ ] Tests/lint pass (or an explicit note if testing is deferred per the
      M0–M1 policy in `CLAUDE.md`)

## Constraint check
Which of the four non-negotiable constraints (`CLAUDE.md`) this touches, and
how it satisfies each. N/A where genuinely irrelevant — but traceability
almost never is.
- **Traceability:** <…>
- **Non-destructive merge:** <…>
- **HITL ontology gating:** <…>
- **No client/classified material:** <…>

## Testing notes
How this is verified — unit/integration tests, a manual query script, a spot
check against source. Note anything the agent shouldn't have to rediscover
(fixtures, sample docs, expected non-determinism).

## Notes for the implementer
Anything a fresh background agent needs and can't infer from the repo:
relevant existing modules, gotchas, ordering constraints within the ticket.
```

## Approval gate

Rupert reviews the drafted tickets as a set in Linear, edits them in place,
and moves each to `Todo`. Implementation is only dispatched from `Todo` —
this is the single HITL checkpoint that replaces per-ticket scoping review.
Anything left in `Backlog` is not dispatched. Tickets labelled
`needs-decision` should be resolved (edit the ticket, log the decision in
`DECISIONS.md`, drop the label) before approval.

## Phase 2 — the supervising session prompt

Given once per epic to the supervising session, not re-explained per ticket:

> Supervise implementation of the `<milestone>` Linear project. Loop:
>
> 1. List issues in the project in state `Todo`, excluding any labelled
>    `needs-input`. Pick ones whose `blocked by` issues are all `Done`, in
>    dependency order.
> 2. For each, move it to `In Progress` and dispatch a background agent with
>    the ticket's full description pasted into its prompt, plus the per-ticket
>    contract below. The agent works in its own git worktree on a branch
>    named `<issue-identifier>-<short-slug>`, and prefixes its commit
>    subjects with the issue identifier.
> 3. Keep at most 3 running at once; dispatch the next as one finishes and
>    its dependencies are met.
> 4. When an agent reports clean completion — draft PR open — move the
>    issue to `In Review` and attach the PR URL to it.
> 5. When an agent escalates, or dies, or reports tests still failing or
>    review findings it could not resolve: move the issue back to `Todo`,
>    add the `needs-input` label, and comment on it with what happened and
>    the specific question. Do not retry it, do not reassign it, and do not
>    work around it yourself.
> 6. Don't wait for my approval between tickets. Stop and ask only if the
>    whole queue is blocked, or something looks wrong at the epic level
>    rather than the ticket level.

## Per-ticket contract

Every dispatched implementation agent operates under the same contract,
regardless of which ticket it's handling:

1. **Implement a given ticket, don't scope one.** The spec is the ticket
   body it was handed; if it's wrong, escalate, don't quietly rewrite it.
2. **Own context, own worktree, own branch.** No shared state with other
   in-flight tickets; conflicts are structurally avoided, not resolved after
   the fact. Branch `<issue-identifier>-<short-slug>`; commit subjects
   prefixed with the identifier so the work is traceable back to the ticket
   from git alone.
3. **Test-until-pass, bounded.** Two rounds of fix-and-retest; a third
   failure escalates rather than looping indefinitely.
4. **Code review before the first commit, by a separate agent.** Once tests
   pass, the implementation agent spawns a dedicated **review agent** and
   waits for its verdict. The reviewer runs in the implementation agent's
   worktree — *not* its own, or it would branch from `main` and review an
   empty diff — reads the uncommitted diff, and reports findings without
   editing anything. The implementer fixes what comes back and reports each
   finding and its disposition. Nothing reaches the branch until this
   passes; if the reviewer cannot be spawned, the ticket escalates rather
   than falling back to self-review. Reviewing after committing means the
   branch history already contains the thing you were checking for, and a
   same-context self-review mostly re-confirms the assumptions that
   produced the code — which is why the reviewer is a separate agent rather
   than a second look by the same one.
5. **Review the diff against the four constraints explicitly** (`CLAUDE.md`:
   traceability, non-destructive merge, HITL ontology gating, no classified
   material), plus the ticket's own constraint check — a **separate pass**
   from the code review above, because a general reviewer will not flag a
   dropped provenance field or a quietly widened ontology as a defect. This
   is the check that actually protects traceability and non-destructive
   merging as the project grows past anyone reading every diff by hand.
6. **Commit small, per ticket** — consistent with the existing preference
   for small, frequent commits; one ticket is one reviewable unit. On a
   clean run: commit, push, open a **draft** PR, and report the URL back.
7. **Escalate rather than guess.** Any of: tests not passing after two
   rounds, review findings that can't be resolved, no reviewer available, a
   constraint genuinely at risk, or the ticket ambiguous or wrong in a way
   `DECISIONS.md` doesn't resolve — stop, don't force a commit, and surface
   the question instead of picking an answer and moving on.

## Concurrency

Cap at 3 concurrent **tickets** — note that a ticket is now up to two agents
(the implementer, plus the review agent it spawns and waits on), so the peak
is six, not three. The cap counts tickets in `In Progress`, not agents.

This is a stated instruction to the supervising
session, not a script parameter — a modest-parallelism preference (faster
than fully sequential BMAD cycles, without needing to reason about
larger-scale fan-out or its cost).

## Checking in

- **Approval gate**, once per epic: reviewing the drafted ticket set in
  Linear is the main judgement call, done deliberately up front rather than
  in fragments.
- **The Linear project board**, glanced at during implementation: `Todo` +
  `needs-input` is the queue of things actually waiting on you; `In Review`
  is PRs waiting to be merged. Nothing else needs attention.
- **Agent view**, for anything mid-flight: "Needs input" rows pinned at the
  top, no transcript-reading required.
- **Notification hook**, for not having to watch the screen: wire
  `agent_needs_input` to a Teams/Slack webhook or desktop notification.
- **Completed, clean tickets** need no check-in at all — commit, push, draft
  PR and the move to `In Review` happen without you. Reviewing PRs
  periodically is a separate, lighter task from babysitting the build.

## Where this sits relative to the rest of the project

- Follows `ROADMAP.md` ordering — author and implement one milestone at a
  time; the supervising session shouldn't jump milestones out of sequence
  even if a later backlog item looks quick.
- `BACKLOG.md` remains one-line intent, authored by hand. Linear tickets are
  its expansion, authored once per epic (Phase 1) and consumed unchanged
  during implementation (Phase 2). The **ticket shape** above is what
  `stories/_TEMPLATE.md` used to be — batch-authored for the whole epic and
  human-approved once, rather than hand-curated per cycle. `stories/` is
  superseded.
- Draws its non-negotiable review checklist directly from `CLAUDE.md`, both
  in the ticket constraint check and in the per-ticket implementation review.
- Linear's GitHub integration, if connected, will link branches and PRs to
  issues automatically. Don't depend on it: the supervisor attaches the PR
  URL and moves state explicitly, so the workflow holds without it.
- Revisit **Workflows** once there's a genuine bulk, no-judgement-needed
  task — e.g. once M2's entity resolution is live and there's a backlog of
  candidate-duplicate pairs to batch-score, or an M0-style extraction audit
  across many documents at once. Not needed for driving the milestone build
  itself.
