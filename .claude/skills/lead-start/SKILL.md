---
name: lead-start
description: Start (or resume) a lead development session for yesKnowMaybe. Syncs the repo and board, starts worker agents on Ready issues, prepares unblocked needs-human PRs, reviews finished PRs, and reports what is ready to merge. Use when the user says "continue", "start", "resume development" or runs /lead-start.
---

# Lead session: start

You are the **lead** described in CLAUDE.md, "Agent automation". The human only reads and merges; never ask them to edit files.

Use the Bash tool (Git Bash) for git, gh, uv and the scripts below. Run scripts as `uv run python scripts/...`, never `python3`.

## 1. Sync

Run `uv run python scripts/lead.py start` from the repo root. It:
- fast-forwards `main`;
- syncs dependencies;
- removes merged worktrees and branches;
- syncs the board.

Read its "Kept back" list. A worktree that is unmerged or has uncommitted changes means an earlier worker's work was left behind. Check its PR (`gh pr list --head <branch>`) and either resume it or report it.

Then read the `lead-handoff` memory, if there is one, for what the last session left to do.

## 2. Plan

- Read the board status that `lead.py` printed, and the open PRs.
- PRs in "Agent review" with no "Independent review" comment yet need a reviewer (step 4); start those first.
- Count the Ready `agent-ready` issues. Read each one with `gh issue view <n> --repo 4OH4/yesKnowMaybe --json title,body,labels,state`.
- Find `needs-human` issues whose dependencies are all closed and which have an "Agent prep" section. Run `uv run python scripts/board.py show` for the list.
- Choose up to **3 concurrent workers** in total, counting issues already In progress. Never run two issues whose "Files / modules in scope" overlap, except the shared files listed in CLAUDE.md, which are handled by rebasing.

## 3. Start workers

For each chosen issue, use the Agent tool with `subagent_type: worker`, `isolation: worktree` and `run_in_background: true`. Send the short worker task message below. Don't wait on one worker before starting the next.

Workers run on Sonnet by default (set in `.claude/agents/worker.md`). Pass `model: opus` instead for:
- `needs-human` issues;
- issues that call for design judgement the issue doesn't settle. Most storage, ingestion and alignment work in this project is of this kind, so lean towards Opus there.

If the human requests changes on a PR that was reported to them with defects left after two reviews, start a fresh `worker` with `model: opus` for that rework.

Keep the lead and the reviewers on Opus. If reviewers start finding noticeably more defects in Sonnet workers' PRs, use Opus for more issue types and note it in the handoff.

## 4. Review as workers report back

For each finished PR:
1. **Independent review.** Start a reviewer with the Agent tool, using `subagent_type: reviewer`, `isolation: worktree` and `run_in_background: true`, and the short reviewer task message below. Give it only:
   - the PR number;
   - the issue number;
   - the consuming issues;
   - the claims in the PR that downstream work depends on most, if any (e.g. a measured rate or a schema rule).

   Don't pass on the worker's report or your own view of the PR, because the point is a fresh context. Only start it once that PR's worker has finished.
2. **When the reviewer reports:**
   - For each **Defect:** it lists, send the worker back with the specific change: by SendMessage if it is still running, otherwise by starting a fresh `worker` with the rework task message below, which works on the existing branch. Before starting a fresh worker, free the branch: if the finished worker's worktree is clean (`git -C <path> status --porcelain` prints nothing) and its HEAD equals `origin/<branch>`, run `git worktree remove <path>`; otherwise don't start the worker, and report the worktree to the human instead. If the rework changes behaviour, start a fresh reviewer afterwards. At most two reviews per PR: if the second still finds defects, report the PR to the human instead of starting a third, and `uv run python scripts/board.py set <N> "Human review"`.
   - **Follow-up:** items are not rework. Open each as a new issue, following `.github/ISSUE_TEMPLATE/`, and link it from your Lead review comment.
   - The worker marks each item it fixed "**Resolved** in <sha>" in "For human review"; check those markers when you re-run the checks, and leave the rest for the human. Don't otherwise rewrite the reviewer's items; if you disagree with one, say so in your Lead review comment.
3. **Re-run the checks.** Use a detached checkout, never one holding the branch: `git fetch origin`, then `git checkout --detach origin/<branch>`, in the worker's worktree or your own. Run `uv sync`, then `uv run pytest` and `uv run ruff check` (until #14 lands, confirm the PR adds no new ruff errors). If CI exists, confirm it is green.
4. **Shared files.** If the PR shares `pyproject.toml`, `uv.lock`, `src/dikms/cli.py` or `src/dikms/config.py` with another open PR, either stack it on that PR's branch (resolve `uv.lock` with `uv lock`) or tell the human the merge order.
5. **Comment.** Post a short "Lead review" comment on the PR: verdict, what you re-ran, the merge order. Then `uv run python scripts/board.py set <N> "Human review"`. Workers set "Agent review" when they open the PR; only you set "Human review".
6. **Sending work back.** If the human requests changes on a Human review PR, or a defect turns up late, set the card back with `uv run python scripts/board.py set <N> "Agent review"` and send the work back as in step 2.

## 5. Report

Keep it short:
- **PRs ready to merge**, in merge order, with one line each.
- **Points from each PR's "For human review" section** that most need the human's attention, and the decisions they approve by merging for `needs-human` PRs.
- **Anything only the human can do**, with exact steps: credentials, extraction runs, quota, settings, real decisions.
- What starts next once those merge.

## 6. Idle shutdown

The session often runs unattended, and once the agents have finished it can only wait for the human. Rather than sit open until the prompt cache goes cold, the lead finishes itself; a fresh `/lead-start` is cheap.

**Idle** means all of these hold:
- no worker or reviewer is running;
- `uv run python scripts/board.py promote` leaves no Ready issue you could start (within the 3-worker limit and the scope-overlap rule);
- no `needs-human` issue with an "Agent prep" section has had all its dependencies closed and still lacks a PR (step 2; `promote` never moves these);
- no PR needs you: none waits for a reviewer, a rework or your Lead review, and no Human review PR has become conflicting (`gh api repos/4OH4/yesKnowMaybe/pulls/<n> --jq .mergeable_state` is `dirty`; if it is `unknown`, GitHub is still computing it, so read it again a few seconds later);
- no human request is unanswered, in the chat or on GitHub.

If the usage guard blocks starting agents, count the session as idle even with work left, and list that work in the handoff note so the next session starts it.

**When you become idle**, after reporting as in step 5:
1. Note the time as `IDLE_SINCE`, in UTC (`date -u +%Y-%m-%dT%H:%M:%SZ`). It is used only in the GitHub `since=` filters.
2. Check `CronList` for an existing idle check and don't add a second. Otherwise schedule one with `CronCreate`, `recurring: false`, about 45 minutes ahead. Cron fields are in **local time**, not UTC, so take them from `date -d '+45 min' '+%M %H %d %m'`. If they came out in the wrong timezone, the time could already have passed, and the check wouldn't fire until next year. Pin the minute, hour, day and month, and nudge the minute off :00 or :30. Prompt: `Lead idle check (idle since <IDLE_SINCE>): follow /lead-start step 6.`
3. Say in one line, in local time, that you'll finish the session then unless something comes up.

**If anything arrives first** (a message from the human, an agent report, a notification), delete the check with `CronDelete` and carry on the loop. Go idle again afterwards if nothing is left.

**When the check fires**, look again:
- run `uv run python scripts/board.py promote`, then look for newly unblocked `needs-human` issues with "Agent prep" as in step 2;
- read GitHub activity since `IDLE_SINCE`, over REST: `gh api "repos/4OH4/yesKnowMaybe/issues/comments?since=<IDLE_SINCE>&per_page=100"` (comments on issues and PRs), `gh api "repos/4OH4/yesKnowMaybe/pulls/comments?since=<IDLE_SINCE>&per_page=100"` (review comments), and `gh api repos/4OH4/yesKnowMaybe/pulls/<n>/reviews` for each open PR (`submitted_at` after `IDLE_SINCE`);
- check each open PR's `mergeable_state` again, since a merge can leave another PR conflicting.

Agents post as the same GitHub user as the human, so tell them apart by time: no agent was running after `IDLE_SINCE`, so anything posted since then by a `User` account (`.user.type == "User"`) came from the human. Ignore `Bot` accounts. Act on it as in steps 2–4: a change request on a PR as in step 4.6, and an answer on a `needs-human` issue by preparing or updating its PR.

If there is work, do it and carry on the loop. If there is none, run `/lead-finish` unattended. Don't wait for, or ask, the human anything. After that, schedule nothing more.

## Task messages

The briefs live in the agent definitions (`.claude/agents/worker.md` and `reviewer.md`), so each spawn message carries only the specifics. Don't paste the briefs, and don't tell agents to read `CLAUDE.md`, which is already in their context.

Worker:

```
Issue #<N> ("<title>"). Consuming issues: <list, or "none">.
<For needs-human issues: "This is a needs-human issue.">
<Anything specific: other in-flight PRs touching shared files, notes left on the issue by the lead.>
```

Worker, rework by a fresh worker (the original has finished):

```
Rework PR #<P> (issue #<N>, branch `<branch>`). Continue on the existing branch; don't create a new one or move the card.
Changes: <each defect or requested change, with the suggested fix>.
<Rebase target, if any.>
```

Reviewer:

```
PR #<P>, closing issue #<N>. Consuming issues: <list, or "none">.
<Load-bearing claims to re-derive first, if any.>
<For a second review: "Second review.">
```
