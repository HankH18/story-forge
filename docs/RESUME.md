# RESUME — regenerated automatically, do not hand-edit

**Generated:** 2026-07-30 19:33:57 CDT by `scripts/refresh_resume.sh`.
Every figure here was read from the thing it describes at that moment. If a number
looks stale, the generator is the bug, not the number.

## Where things stand right now

| | |
|---|---|
| HEAD | `1e3851e` on `k5-learning-app-gstack-setup` |
| uncommitted files | 1 |
| GitHub `master` | `1e3851e` |
| stages in local db | 11 |
| smallest allowed_words stage | 769 (§18's floor is 150) |
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

1. **Prod is CURRENT as of 2026-07-30 19:30 CDT — code and pool both.** Image
   `1e3851e-amd64`, pool swapped from the gated local one, test kids wiped with
   the owner's authorisation. Verified by what the ORIGIN SERVES: the new
   bundle 200s, the old one 404s, `/api/health` is ok with
   `demo_bypass_auth: false`, a garbage token 401s, `/api/themes` 200s.
   Prod: 667 stories / 104 warm / 204 questions / 34,221 allowed_words / 0 kids
   / 0 readings / 0 answers / 1 user / 33 assets. `amid` appears in **0**
   servable bodies and **0** servable questions.
   Gates against prod: `--check-coverage` 0, `--check-mcq2` 0,
   `--check-vocabulary` 1, `--check-story-words` 1. The two reds are the
   SAME documented residual local carries (`dragon`, `planet` — absent from
   Dale-Chall 1948, not adult register), ~$1.32 of regeneration, deliberately
   unspent.
2. **NEVER trust a DigitalOcean deploy's `phase`. Read `cause`.** A
   PRE_DEPLOY failure aborts the deploy and App Platform then AUTO-ROLLS BACK
   to the previous image — and the rollback is itself a deployment that reaches
   phase **ACTIVE**. So `doctl apps list-deployments` says ACTIVE, health
   answers 200, the spec lists the new tag, and the OLD code serves.
       doctl apps list-deployments <app> --format ID | head -1
       curl -sH "Authorization: Bearer $TOKEN" .../deployments/<id> | jq -r .deployment.cause
       # "app spec updated"                       -> real
       # "automated rollback after failed deploy" -> you shipped nothing
   THREE deploys were lost to this. The always-correct check is what the origin
   serves: fetch `/`, read the `index-*.js` hash, and confirm the NEW hash
   200s and the OLD one 404s.
   Two causes, both now fixed and tested (`test_deploy_spec.py`): the migrate
   job's `run_command` used shell syntax (`cd X && alembic ...`) but App
   Platform EXEC-forms it, so it hunted for a binary named `cd` and exited
   128; and the LIVE spec's migrate job had **no envs at all**, so alembic fell
   back to config.py's default and tried to reach `localhost:5433` from a
   container. The live spec has drifted from `.do/app.yaml` generally — live
   names the service `app`, the file names it `api` — so the committed spec
   is NOT a description of what is running.
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
- **Postgres is on port 5433, NOT 5432.** `services/api/.env` is the authority;
  read the port out of it rather than typing one. Hardcoding 5432 gives
  `connection refused`, which surfaces as a DIFFERENT set of test failures on
  every run — because each run fails wherever it happened to need the database
  first. That looks exactly like concurrent-writer contention and it is not: it
  is the wrong port. I diagnosed it as contention, wrote that here as a lesson,
  and was wrong; an agent running against the right port got 1133 passed on the
  same tree at the same moment. **Varying failures mean a broken connection far
  more often than they mean a race.** Prove which by connecting once by hand.
- **Real contention does exist and looks different.** When several agents run
  suites at once you get 1-2 failures that MOVE between runs and pass in
  isolation (`test_story_stage_binding`, `test_phase2`, `test_stories_api`).
  The fix is the private DB copy below, not waiting.
- **Measure against a private DB copy** when anything else is writing:
  `docker exec storyforge-db psql -U storyforge -d postgres -c 'CREATE DATABASE sfscratch'`
  then `docker exec storyforge-db sh -c 'pg_dump -U storyforge storyforge | psql -q -U storyforge -d sfscratch'`
  then `DATABASE_URL=postgresql+psycopg://storyforge:storyforge@localhost:5433/sfscratch`
  — note **5433**. A green shared-DB run is as untrustworthy as a red one.
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
