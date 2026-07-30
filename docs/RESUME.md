# RESUME — regenerated automatically, do not hand-edit

**Generated:** 2026-07-30 06:08:05 CDT by `/tmp/refresh_resume.sh`.
Every figure here was read from the thing it describes at that moment. If a number
looks stale, the generator is the bug, not the number.

## Where things stand right now

| | |
|---|---|
| HEAD | `5f04591` on `k5-learning-app-gstack-setup` |
| uncommitted files | 0 |
| GitHub `master` | `7d58263` |
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

1. **Production is running the PRE-RENUMBER image.** The deployed tag is
   `c5125be-amd64` and its database still has 12 stages; migration
   `f3a91c6d0e24` applies at the next deploy's PRE_DEPLOY job. Prod is therefore
   self-consistent but old, and the local tree is eleven stages. Rebuilding is
   `docker build --platform linux/amd64` (arm64 gives `exec format error` —
   that failure already cost one deploy), push, then update the tag in the spec.
2. Prod was warmed with 44 stories under the OLD prompts, old word list and old
   art. They should be replaced, not migrated.
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
