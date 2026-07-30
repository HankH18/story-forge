# The Fable audit-fix loop — what it was, and why it produced nothing

**Verdict: it did not run. Zero findings, zero verifications, zero fixes.** Not
"the codebase came back clean" — nothing executed. This document exists so nobody
reads the workflow's own summary and concludes otherwise.

## What the summary said, and why it is a trap

Both attempts returned, per round:

    {"round": 1, "found": 0, "survived": 0, "accepted": 0, "fixes": 0}
    {"round": 2, "found": 0, "survived": 0, "accepted": 0, "fixes": 0}
    {"round": 3, "found": 0, "survived": 0, "accepted": 0, "fixes": 0}

That shape is **indistinguishable from a flawless codebase** unless you read the
failure list underneath it. Which is this project's signature defect — a result
that reports success while nothing happened — appearing one final time, in the
tooling built to hunt it.

    attempt 1:  115 agents, 0 done, 115 errored
    attempt 2:  120 agents, 1 done, 119 errored

Every failure: `You've hit your session limit`.

## Three separate causes, in the order I found them

**1. Fan-out width against a session budget.** 36 subsystem finders, each an
independent Fable agent, then a verifier per finding, then a fixer per survivor —
per round, three times. That is ~115 agents before a single fix lands. The first
attempt burned the entire remaining session budget inside the round-1 find phase
and never reached verify. The design optimised for coverage and ignored that
coverage is rationed.

**2. `Workflow` inside `claude -p` orphans itself.** The overnight watchdog
re-invoked the CLI headlessly, and the CLI is a **one-shot process** while
`Workflow` returns immediately and runs in the **background**. So the headless run
launched the loop and exited, killing it. Run 1 exited `rc=0` having produced a
plan, a monitor, and no findings — and would have done that forty more times,
logging success each time. Fixed by forbidding `Workflow` in the watchdog prompt
and driving agents inline with `run_in_background: false`.

**3. A worktree-isolated fix phase with no merge step.** Caught by inspection
before it ran, not after. Fixers were given `isolation: 'worktree'` — correct,
because a `git stash -u` in a shared tree had already erased 48 files and four
modules — but nothing merged the worktrees back. Three rounds of fixes would have
been written, committed nowhere, and discarded when the worktrees were cleaned.
A merge phase now exists, with a test run after **every** merge so a failure is
attributable to one change rather than to a pile.

## What survived and is worth keeping

The **script itself** is sound and cached: run `wf_f3df3f19-776`, script at
`~/.claude/projects/.../workflows/scripts/storyforge-audit-fix-loop-wf_f3df3f19-776.js`.
Resuming replays completed agents from cache. Its useful parts:

- **Per-subsystem finders**, so depth beats breadth and no agent is asked to hold
  the whole system in its head.
- **Adversarial verification defaulting to REFUTED.** Four findings in this
  project were withdrawn as measurement artefacts — a benchmark that measured a
  warm `lru_cache`, a layout overlap that had never been painted, a redirect leak
  that was a substring regex, a decodability gap caused by an empty sight-word
  set. This pass exists because of those.
- **An objective bar for improvements**: an `objective_basis` naming a spec
  section, a measurement or an invariant, or the finding is dropped. That is what
  keeps "implement anything that objectively makes sense" from becoming "implement
  whatever an agent fancied".
- **The signature-defect brief** given to every finder: hunt *a check that passes
  because it asks the wrong question*, with the six that shipped here as examples.

## The replacement

Six agents, Opus, two cycles — sized to a session budget rather than to ambition.
Deliberately not 36. The lesson is not that the design was wrong; it is that a
design whose first phase cannot finish is not a design, it is a wish.
