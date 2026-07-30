# RESUME — regenerated automatically, do not hand-edit

**Generated:** 2026-07-30 17:57:07 CDT by `scripts/refresh_resume.sh`.
Every figure here was read from the thing it describes at that moment. If a number
looks stale, the generator is the bug, not the number.

## Where things stand right now

| | |
|---|---|
| HEAD | `2cac2c6` on `k5-learning-app-gstack-setup` |
| uncommitted files | 9 |
| GitHub `master` | `5a7b4f2` |
| stages in local db | 11 |
| smallest allowed_words stage | 771 (§18's floor is 150) |
| git worktrees | 2 (1 main + 1 sibling checkout = quiet; more = loop fixers in flight) |
| live app /api/health | HTTP 200 — https://storyforge-vtwne.ondigitalocean.app |

## The single thing in flight

An audit-fix loop, driven by **direct `Agent` calls, NOT the `Workflow` tool.**
Workflow was tried and abandoned: it was killed six times, and because it returns
immediately and runs in the background it cannot be driven from a one-shot
`claude -p` process — run 1 exited rc=0 having produced a plan, a monitor and
zero findings for exactly that reason. Direct agents complete before the caller
moves on, so nothing is orphaned. The two dead workflow scripts are still on disk
under the session's `workflows/scripts/` directory; they are history, not a plan.

Per round: subsystem auditors (a few at a time, each returning defects AND
improvements with reproductions) -> adversarial verify, DEFAULT TO REFUTED ->
implement what survives, one at a time, running the suite after EACH change ->
gate -> push. Findings are written to `docs/DEVIATIONS.md` as they land, under a
band nobody else is using, because a crash between the work and the write-up
loses the work.

**If `git worktree list` shows more than 2 entries, fixes are stranded.** Each
fixer commits inside its own worktree and a merge phase brings them in; a session
that dies between those two steps leaves real work on branches nobody merged. Find
them with `git branch -a` and `git log --oneline HEAD..<branch>` — do not trust
agent self-reports, because a crashed agent prints nothing. Merge one at a time,
running the suite after each.

## What is done

Phases 0-8 complete. Eleven stages (stage 1 retired — it held 78 words against a
150 floor, and its inventory could not express negation because no stage-1 word
contained the letter `o`). Live on DigitalOcean from a container image, no git
provider connected. §17's demo runs end to end; its playground step is now
**stage 2**, not stage 3.

## What is NOT done, and the first one matters

1. **Prod's SCHEMA is current; its CONTENT is not.** Measured read-only on
   2026-07-30 against cluster `storyforge-pg`, db `storyforge`:
   `alembic_version = f3a91c6d0e24`, **11 stages (1-11)** — so the renumber
   HAS applied and the earlier "prod is pre-renumber with 12 stages" claim in
   this file was FALSE. Prod holds 111 stories (105 warm / 3 rejected / 3
   complete), 4 kids, 6 readings, 5 answers.
   What is stale is the POOL, generated under the old prompts and old word
   list: `--check-mcq2` against prod reports **13 of 52 servable two-choice
   rows answerable with the story unread (25%), 4 of them pointing at the
   WRONG card**, plus 2 rows with recorded answers needing an explicit
   decision. Prod stories still contain `amid` — the exact word the user
   reported ("no five-year-old is using the word 'amid'").
   Rebuilding the image is `docker build --platform linux/amd64` (arm64 gives
   `exec format error` — that already cost one deploy), push, update the tag.
2. Prod's pool should be REPLACED, not repaired row by row. Re-warming from
   scratch costs real money — the local warm's own recorded figure is
   **$16.29 across 637 generated stories ($0.0256 avg)**. Copying the
   already-gated local pool in is free and is the better route, but prod has
   4 real kids and 6 readings whose story_id references would cascade, so it
   needs a migration, not a truncate.
3. Open, documented, not blockers: one grammar false positive on coordinated
   subjects (`"Sam and the rabbit spin."`); §1.6's p95<12s recorded as unmet and
   unreachable with the criterion unrelaxed; `ruff format --check` fails on ~39
   pre-existing files while `ruff check` is clean.

## Traps that have each cost real time here

- **A check that passes because it asks the wrong question** is this project's
  signature defect. **Thirteen shipped so far:** a grammar verdict computed then
  discarded on write; a promotion counter counting readings not distinct stories,
  with four tests pinning the bug as the contract; "0 blocklisted words" true
  against a list holding only stems while 50 inflections were live; 839 segments
  tagged with art against ZERO image files, every <img> 404ing into a silent
  placeholder; `GET /api/themes` 404ing for eight phases; a coverage gate
  counting servable rather than promotable stories; a worktree-isolated fix phase
  with no merge step, which would have discarded three rounds of work;
  `_STRIPPED_STORY_SQL` referenced with an explaining comment but never written;
  `_RECORD_MCQ2_REMOVAL_SQL` defined, explained, and never executed; a tap-word
  question whose sentence said SOUND while every proof said LETTERS; §8's evidence
  guard written as a read-then-write that N callers could forge; §7's "the kid
  hasn't read" clause never implemented at all, so a child cycled 3 stories
  forever and sat at "3 of 5" permanently while BOTH supply gates read green; and
  moderation rule 7, which had a stated rule and no detection behind it.
  The lesson each time is the same: **ask what the check would have to observe to
  fail, and go confirm it can observe that.**
- **A test that cannot fail is the same defect wearing a lab coat.** Sabotage
  every test you write — break the source it covers and confirm it goes red.
  One agent here found two of its own new fixtures were incapable of failing:
  the first reproduced a different defect than the one it was written for, the
  rewrite reproduced nothing at all.
- **Gate against a git CLONE of HEAD, not `git archive`.** `git archive` into
  /tmp produces a directory that is not a repo, so eval provenance records
  `git_dirty=None` and one test fails for a reason that has nothing to do with
  your change. `git clone --no-hardlinks . /tmp/x` gives a real repo at HEAD
  with the in-flight edits excluded. (The eval renderer itself is honest here —
  it prints "dirty-tree state NOT RECORDED" rather than claiming clean.)
- **Do not run the suite while several agents are live.** Four concurrent
  pytest runs against the one Postgres produce `connection refused` bursts and a
  DIFFERENT set of failures every run, while the container still reports healthy
  at 16/100 connections. Varying failures across runs means contention, not a
  regression — wait for the agents, then gate once.
- **Measure against a private DB copy** when anything else is writing:
  `docker exec storyforge-db sh -c 'pg_dump -U storyforge storyforge | psql -U storyforge -d sfscratch'`
  then `DATABASE_URL=.../sfscratch`. A green shared-DB run is as untrustworthy as a red one.
- **Never `git add -A` while an agent is live.** I did, four times, and committed
  another agent's half-finished work under a message describing something else.
- **Never test auth against a bypassed server.** `/api/health` reports
  `demo_bypass_auth`; confirm `garbage token -> 401` before believing any response.
- Source `.env` BEFORE overriding `DATABASE_URL`, never after. Sourcing after
  clobbers it, and a seed run reported success against the wrong database.
- Chrome MCP is not connected. Use `~/.claude/skills/gstack/browse/dist/browse`
  with an isolated `BROWSE_SERVER_PORT`.

## Credentials

`services/api/.env` is mode 600 and gitignored; git history holds zero real keys.
Push with a one-off URL, never a stored PAT: `git push origin master` reaches BOTH
remotes, which is what the DO spec's `deploy_on_push` relies on. The DO token was
supplied per-command via `DIGITALOCEAN_ACCESS_TOKEN` and never written to
`~/.config/doctl`. A new session will need the user to re-supply both.
