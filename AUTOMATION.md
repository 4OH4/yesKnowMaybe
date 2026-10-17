# Automation

How the build is driven day to day, and why it is set up this way. The
operating rules that agents follow live in `CLAUDE.md` ("Workflow" and "Agent
automation"); this file is the rationale and the map.

The model is adapted from the news-bot repo, where it has run a full build
phase: one **lead** session runs a GitHub Project board, hands issues to up to
three **worker** agents, and has every PR checked by a fresh, read-only
**reviewer** agent before Rupert sees it. Rupert's job is to read and merge.

## Where things live

| Concept | Lives in |
| --- | --- |
| Milestone (M1, M2, …) | GitHub **milestone**, mirroring `ROADMAP.md` |
| One reviewable unit of work | GitHub **issue**, following `.github/ISSUE_TEMPLATE/` |
| Sequencing | `Depends on #N` lines in the issue body, read by `scripts/board.py` |
| Status | The **Project board**'s Status column |
| Needs Rupert (decision, credentials, ontology change) | Label `needs-human` |
| Safe for an agent to take unsupervised | Label `agent-ready` |
| The work itself | Branch `issue-<n>-<slug>` and a PR that says `Closes #<n>` |
| Judgement calls Rupert approves by merging | The PR's "For human review" section |
| Settled design | `ARCHITECTURE.md`, `DECISIONS.md` (unchanged) |
| One-line intent for milestones not yet written up | `BACKLOG.md` |

Labels also carry `area:*` (parsing, extraction, alignment, storage, ingest,
query, eval, ontology, infra, docs) and `size:S|M|L`, where L means split it
before an agent picks it up.

## The board

`Backlog` → `Ready` → `In progress` → `Agent review` → `Human review` → `Done`

- **Backlog**: written, but not yet startable: a dependency is still open, or
  it's `needs-human`.
- **Ready**: `agent-ready` with every dependency closed. `board.py promote`
  moves issues here; it never promotes `needs-human` ones.
- **In progress**: a worker holds it.
- **Agent review**: the PR is open and agents are still reviewing or
  reworking it.
- **Human review**: the lead has posted its review; the PR waits only for
  Rupert.
- **Done**: closed.

## A lead session

1. `/lead-start` runs `scripts/lead.py start` (update `main`, sync
   dependencies, tidy merged worktrees and branches, promote unblocked
   issues), then reads the handoff note from the last session.
2. The lead starts workers on Ready issues, at most three at a time, and
   never two whose "Files / modules in scope" overlap.
3. Each worker implements its issue in its own worktree, runs the tests and
   lint, does the four-constraint pass, opens a PR and moves its card to
   Agent review.
4. The lead starts a reviewer for each finished PR. The reviewer writes its
   findings into the PR's "For human review" section, marking each as a
   **Defect** (fix in this PR) or a **Follow-up** (a new issue).
5. Defects go back to the worker, with at most two review rounds. The lead
   re-runs the checks, posts a "Lead review" comment and moves the card to
   Human review.
6. When nothing is left to start or review for 45 minutes, the lead runs
   `/lead-finish`: it tidies up, writes the `lead-handoff` memory and ends the
   session. A fresh `/lead-start` is cheaper than reloading a long session
   whose prompt cache has gone cold.

## Why this shape

- **Approval is the merge.** The earlier setup had a separate up-front
  approval gate on tickets, then left work uncommitted for Rupert to inspect.
  Here the PR is the unit Rupert reviews, and the "For human review" section
  lists exactly what he is approving. `needs-human` work, ontology changes
  above all, still never reaches `main` without his merge, which is what
  constraint 3 in `CLAUDE.md` requires.
- **Independent review, bounded.** On YES-6 (now #4) a separate reviewer
  caught things tests structurally couldn't (wrong numbers in a notes file,
  and a normaliser solving a problem the corpus doesn't have), while
  self-review caught nothing. The same ticket showed that review cost scales
  with rounds and that design changes found in review sprawl if they're
  handled in the same PR. Hence:
  - a fresh-context reviewer for every PR;
  - a two-round cap;
  - the Defect/Follow-up split;
  - property tests before the first review, for code that fails by producing
    plausible-but-wrong output.
- **The constraint pass is explicit.** A general code review won't flag a
  dropped provenance field or a quietly widened ontology as a defect, so both
  the worker and the reviewer check each of the four constraints on its own.
- **Everything in one place.** Issues, branches, PRs and CI sit next to the
  code, and `gh` is the only tool needed. Linear needed an MCP connection that
  kept dropping its authentication, and its board drifted out of step with
  the code.

## What stays with Rupert

- Merging every PR, including `needs-human` ones (decisions, ontology
  changes, eval expectations).
- Anything needing credentials: Vertex/Gemini extraction runs, Spanner,
  `terraform apply`, quota requests, GitHub settings and secrets.
- Choosing between real alternatives when an issue raises one.

## Differences from news-bot

- Windows rather than Ubuntu: scripts run as `uv run python scripts/...`,
  there is no Makefile or pre-commit hook, and agents use Git Bash.
- The uv project is at the repo root, not in `backend/`.
- Workers and reviewers carry this project's four non-negotiable constraints,
  and the PR template has a Constraint check section in place of news-bot's
  contract checks.
- `data/corpus/` and `output/` are git-ignored, so worktrees copy them from
  the main checkout when an issue needs them.
- No CI yet (#14 adds it), and no Claude Code GitHub Action.
- Decisions stay in `DECISIONS.md` rather than ADR files.
