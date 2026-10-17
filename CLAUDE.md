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
  coherent, reviewable change. Don't batch unrelated work, and don't mix
  formatting-only changes with logic changes. Use conventional commits —
  `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `ci:`, with an
  optional scope, e.g. `feat(storage): add Kùzu backend`. (History before
  October 2026 used Linear-style `YES-n:` prefixes; see the mapping in
  `DECISIONS.md`.)
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
   approval. In practice: an ontology change reaches `main` only as a
   `needs-human` PR that Rupert merges, recorded in `DECISIONS.md`.
4. **No client-identifying or classified/export-controlled material.** This
   system only ever ingests open, public sources.

## Commands

Development is on Windows. Use the Bash tool (Git Bash) for git, gh and uv, so
the permission allowlist in `.claude/settings.json` applies; run Python
through `uv run`, never `python3`, which can resolve to a different
interpreter here.

```bash
uv sync                          # install deps (also in a fresh worktree)
uv run pytest                    # tests: offline, no credentials
uv run ruff check                # lint
uv run dikms <command>           # the pipeline CLI (parse, chunk, extract, align, ...)
uv run python scripts/board.py show
uv run python scripts/lead.py start
```

`data/corpus/` and `output/` are git-ignored, so they exist only in the main
checkout. A worktree that needs them copies them in and never `git add`s
them. `dikms fetch` and `dikms extract` need the network or Vertex
credentials, so they are human steps, never agent ones.

## Workflow

The backlog is GitHub Issues on 4OH4/yesKnowMaybe, tracked on a GitHub Project
board. `AUTOMATION.md` explains the model; this section holds the rules.
`BACKLOG.md` keeps one-line intent for milestones whose issues haven't been
written yet.

### Writing issues
Agents write most new issues: follow-ups, splits and new work. Rupert reads an
issue first to understand what is being built and why, then reviews the PR
against it, so write for that reader before the implementing agent.
`gh issue create` doesn't apply the issue forms, so follow
`.github/ISSUE_TEMPLATE/` section by section, and:
- **Context** opens with a short plain-language paragraph: the problem or gap
  today, what the change makes possible, and why it's needed now (what it
  unblocks). References (ROADMAP.md milestone, ARCHITECTURE.md /
  DECISIONS.md sections, the BACKLOG.md line, related issues) come after it.
- **Task** opens with **Summary:**, two to four sentences on what is being
  built and how it fits the pipeline. The precise spec follows under
  **Details:**.
- Every issue has a **Constraint check** against the four constraints above.
- Anything needing credentials, a live Vertex/Spanner run, Terraform, an
  ontology change or a real decision is `needs-human`, not `agent-ready`.
- Explain internal names and jargon on first use, but keep the precision:
  acceptance criteria, scope and verification stay specific.

### Picking up work
- Work only on issues in the `Ready` column labelled `agent-ready`, unless
  Rupert or the lead has assigned you something directly.
- Check "Depends on" in the issue. If a dependency isn't merged, don't start;
  comment on the issue instead.
- Stay within the files and modules listed in the issue's scope. If you need
  to change something out of scope, stop and comment explaining why rather
  than expanding the change.

### Branches, PRs and merging
- One issue per branch, named `issue-<number>-<short-slug>`, in its own git
  worktree when running alongside other agents. Rebase on `main` before
  opening a PR.
- The PR description follows `.github/pull_request_template.md`: `Closes
  #<issue>`, the acceptance criteria ticked with evidence, verification
  output, the constraint check, design notes and follow-ups.
- Until #14 lands, the repo carries pre-existing `ruff` errors: a PR must add
  none, and must not fix unrelated old ones.
- Update docs (`ARCHITECTURE.md`, `DECISIONS.md`, `ROADMAP.md`, `SETUP.md`,
  this file) if your change affects them.
- Rupert reviews and merges every PR. Never merge to `main` yourself.

### When to stop and ask
Stop and comment on the issue, rather than guessing, when:
- the acceptance criteria are ambiguous or contradict this file,
  `ARCHITECTURE.md` or `DECISIONS.md`;
- the work would put any of the four constraints at risk;
- it needs an ontology change, credentials, a new external service or a
  significant new dependency;
- you find a design problem the issue doesn't anticipate.

## Agent automation

Development runs as one **lead** session coordinating up to **3 worker**
agents, plus a short-lived **reviewer** agent for each finished PR. The setup
is adapted from the news-bot repo (see `AUTOMATION.md`).

**Session lifecycle.** Every lead session starts with `/lead-start` and ends
with `/lead-finish` (`.claude/skills/`). The lead runs `/lead-finish` itself
once it has been idle for 45 minutes: no agents running, nothing to start,
and no new human activity (`/lead-start` step 6).
- Both run `scripts/lead.py`, which does the mechanical steps: update `main`,
  sync dependencies, remove merged worktrees and branches, sync the board,
  and print the status. It never deletes anything with uncommitted or
  unmerged work; it lists it instead.
- The skills add the steps that need judgement: starting workers and
  reviewers, reviewing PRs, reporting, and writing a handoff note (the
  `lead-handoff` memory) for the next session.
- Workers and reviewers are project subagents defined in `.claude/agents/`
  (`worker.md`, `reviewer.md`); their briefs live there. Workers run on
  Sonnet, switched to Opus for `needs-human` or design-heavy issues. The lead
  and reviewers run on Opus. Reviewers have no file-editing tools, and
  workers can't start subagents.

**Rupert's role is to read and merge.** Agents do all the preparation, so
every PR reaches him ready to merge as it is:
- Never ask him to edit files, fill in templates, tick boxes or make
  follow-up commits. If a PR needs a change, including review feedback he
  gives in a comment, an agent makes it.
- Approval is the merge. For `needs-human` work (decisions, ontology changes,
  eval expectations), the agent writes the final version, including the
  `DECISIONS.md` entry, and opens a normal, non-draft PR. Merging it is the
  sign-off; declining or commenting is the rejection or the request for
  changes.
- Only ask for what agents can't do: credentials, extraction runs against
  Vertex, quota requests, `terraform apply`, GitHub settings, a decision
  between real alternatives. Give the shortest exact steps.

**Board.** The GitHub Project board is the source of truth for status. Use
`scripts/board.py` to read and move cards rather than editing them by hand:
- `uv run python scripts/board.py show`: issues grouped by status.
- `uv run python scripts/board.py set <issue> "<status>"`: statuses are
  Backlog, Ready, In progress, Agent review, Human review, Done. "Agent
  review" means agents are still reviewing or reworking the PR; "Human
  review" means it waits only for Rupert.
- `uv run python scripts/board.py promote [--dry-run]`: moves closed issues to
  Done, then moves open `agent-ready` issues from Backlog to Ready once every
  "Depends on #N" issue is closed. It never promotes `needs-human` issues.

**Workers** each take one issue: set the card to In progress, branch from
up-to-date `origin/main` in their own worktree, implement and verify
(including property tests for code that can produce plausible-but-wrong
output, and an explicit four-constraint pass), rebase, push, open the PR, set
the card to Agent review, and report back. Workers don't merge, don't edit
other issues, and don't move cards other than their own.

**Reviewers** give every PR an independent, adversarial, read-only review
with a fresh context. The brief names only the PR, its issue and any
load-bearing claims to re-derive, never the worker's report. A reviewer
checks the PR text against the diff and its own test run, tries to show the
change falls short of the issue's goals, checks each of the four constraints
explicitly, and writes its findings for Rupert in a "For human review"
section at the top of the PR body. It marks findings **Defect:** (fix in
this PR) or **Follow-up:** (a new issue), so review rounds fix defects rather
than redesign. At most two reviews per PR; if the second still finds defects,
the lead reports the PR to Rupert instead of looping.

**The lead** loops: promote, start workers for Ready issues (never two whose
file scopes overlap), start a reviewer for each finished PR, send defects
back to the worker, re-run the checks, post a "Lead review" comment, move the
card to Human review, and tell Rupert what's ready to merge and in what order.

**Shared files.** Several issues touch `pyproject.toml`, `uv.lock`,
`src/dikms/cli.py` and `src/dikms/config.py`. Rebase on `main` just before
pushing. Resolve `uv.lock` conflicts by taking `main`'s version and re-running
`uv lock`, never by hand-editing.

**Tooling notes.**
- Agent worktrees live under `.claude/worktrees/` (git-ignored). Never
  `git add` them.
- **GraphQL budget.** GitHub allows 5,000 GraphQL points per user per hour,
  shared by the lead, every agent and `gh` itself (`gh pr view/edit/list` and
  `gh project` all use it). Prefer REST (`gh api
  repos/4OH4/yesKnowMaybe/pulls/<n>`, `.../issues/<n>`) for reading PRs and
  issues in loops, and don't call `board.py show` repeatedly. If it reports
  "GraphQL rate limit exhausted; resets at <time> UTC", wait or use REST.

## When you hit an open question

Check `DECISIONS.md` first — it may already be settled, including "flagged
as open, needs experimentation" entries. If genuinely unresolved, propose an
approach and flag it as needing sign-off rather than picking silently and
moving on, particularly for anything touching the four constraints above.
