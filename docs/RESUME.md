# RESUME — regenerated automatically, do not hand-edit

**Generated:** 2026-07-30 17:55:55 CDT by `/tmp/refresh_resume.sh`.
Every figure here was read from the thing it describes at that moment. If a number
looks stale, the generator is the bug, not the number.

## Where things stand right now

| | |
|---|---|
| HEAD | `fffcd2f` on `k5-learning-app-gstack-setup` |
| uncommitted files | 9 |
| GitHub `master` | `5a7b4f2` |
| stages in local db | 11 |
| smallest allowed_words stage | 771 (§18's floor is 150) |
| git worktrees | 2 (1 main + 1 sibling checkout = quiet; more = loop fixers in flight) |
| live app /api/health | HTTP 200 — https://storyforge-vtwne.ondigitalocean.app |

## The single thing in flight

A three-round audit-fix loop, run as a Workflow. Per round: 36 Fable finders (one
per subsystem) -> adversarial verify (default refuted) -> objective triage ->
fix in an isolated worktree -> **merge, testing after every single merge** -> gate.
Rounds 2 and 3 are deduped against earlier findings.

    Run ID:     wf_f3df3f19-776
    Script:     ~/.claude/projects/-Users-hankholcomb-conductor-workspaces-story-forge-delhi/e2581e8b-a8ea-453c-977c-55820e51f3c4/workflows/scripts/storyforge-audit-fix-loop-wf_f3df3f19-776.js
    Resume:     Workflow({scriptPath: "<above>", resumeFromRunId: "wf_f3df3f19-776"})

Resuming returns every completed agent from cache, so a dead session costs only
the agents that were mid-flight. **Read `<transcriptDir>/journal.jsonl` before
assuming a cached result was non-empty.**

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
  signature defect. Six shipped: a grammar verdict computed then discarded on
  write; a promotion counter counting readings not distinct stories, with four
  tests pinning the bug as the contract; "0 blocklisted words" true against a list
  holding only stems while 50 inflections were live; 839 segments tagged with art
  against ZERO image files, every <img> 404ing into a silent placeholder;
  `GET /api/themes` 404ing for eight phases; and a worktree-isolated fix phase
  with no merge step, which would have discarded three rounds of work.
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
