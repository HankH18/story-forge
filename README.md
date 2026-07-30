# Story Forge

A K–5 English reading game with a **machine-verified decodability guarantee**.

An LLM generates stories; a deterministic validator proves every story is
readable using only the phonics rules a specific child has already mastered. The
LLM is an untrusted generator. A pure function is the gate.

## Quick start

```bash
make install   # uv sync (API, Python 3.12) + bun install (web)
make dev       # Postgres via Docker, API on :8000, web on :5173
make test      # API + decoder tests, plus a web typecheck
make eval      # §14 harness: 11 stages x 3 themes x 5 stories, real API
make help      # all targets
```

`make eval` is where the guarantee is tested rather than asserted. It runs the
real pipeline against the real model, checks §1's acceptance criteria, and exits
non-zero when they are not met — a bad pass rate is reported as bad. Latest
results, with the model ids, pricing date and total cost: **`docs/EVAL.md`**.
`make eval EVAL_ARGS="--estimate-only"` projects the spend without calling the
API; `make eval-smoke` is the cheap 4-story regression gate.

`make dev` blocks until Postgres is genuinely accepting connections before
starting the API, so migrations never race an unready socket.

Verify the stack is actually up:

```bash
curl -s localhost:8000/api/health
```

Components not yet built report `pending` with the phase that implements them.
They are never silently omitted and never reported as `ok`.

## Layout

| Path | What |
|---|---|
| `apps/web` | React 18 + Vite + TypeScript + Tailwind v3 |
| `services/api` | FastAPI, Python 3.12 (uv-pinned) |
| `services/api/storyforge/decoder` | The validator. Pure — no network, no DB, no clock. |
| `packages/shared` | OpenAPI-generated TypeScript client |
| `data/seed` | Stages, GPCs, sight words |
| `docs/DEVIATIONS.md` | Every deviation from the build doc, with rationale |

## Child safety

Kid mode collects nothing. No email, no date of birth, no microphone, no camera,
no free-text input, no third-party trackers, and no avatar uploads. Text-to-speech
is on-device via the browser's `speechSynthesis`.

Voice and biometrics are **deliberately absent**: post-2025 COPPA amendments treat
voiceprints as biometric identifiers, so the architecture collects zero audio.

Every child-facing string passes three independent layers — a deterministic
blocklist, allowlist-constrained generation, and a moderation pass.

## Build status

Phase 0 (scaffold) and Phase 1 (decoder) — see `docs/DEVIATIONS.md` for
decisions taken along the way.

## Remotes

GitLab (`labs.gauntletai.com`) is the canonical origin; GitHub is a mirror.
`origin` carries both push URLs, so one `git push` lands on both. Credentials are
never stored in the remote — supply a token in a one-off push URL.
