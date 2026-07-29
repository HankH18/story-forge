# Deploying Story Forge to DigitalOcean App Platform

Build doc §16 phase 7. Artifacts: `services/api/Dockerfile`, `.dockerignore`,
`.do/app.yaml`. Deviations for this phase are `docs/DEVIATIONS.md` 7.1–7.9.

Shape (§3): **one app, one domain, three components** — a Docker-built FastAPI
service on `/api`, the Vite SPA as a static site on `/`, and a managed Postgres 16.
Plus a `PRE_DEPLOY` job that runs Alembic once.

```
  https://story-forge-xxxxx.ondigitalocean.app
        │
        ├── /api/*  ──►  service `api`   (services/api/Dockerfile, 1 instance, 1 worker)
        └── /*      ──►  static site `web` (apps/web → dist/, served from the edge)
                                │
        job `migrate` (PRE_DEPLOY) ──►  managed Postgres 16 `db`  ◄──┘
```

---

## 0. Before you touch DigitalOcean

**The source repo has to be somewhere App Platform can read.** This project's
canonical remote is `labs.gauntletai.com` — a **self-hosted** GitLab. App Platform
builds from GitHub, gitlab**.com**, Bitbucket, or a container registry, and cannot
reach a self-hosted instance. Two supported paths:

- **GitHub mirror (what `.do/app.yaml` assumes).** Push to
  `github.com/HankH18/story-forge`, then install the *DigitalOcean* GitHub app on
  that repo (App Platform prompts you the first time). The spec references
  `repo: HankH18/story-forge`, `branch: main` — change both if yours differ.
  Submission still goes to GitLab; this mirror only exists so DO can build.
- **Container registry.** Build and push the API image yourself, then replace each
  `github:` block on `api`/`migrate` with `image:`. The static site still needs a
  git source, so this path does not remove the mirror — it only removes DO's build
  of the API.

**Collect these five things.** Nothing below works without them:

| Value | Where it comes from |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys |
| `CLERK_SECRET_KEY` | Clerk dashboard → API keys → Secret key |
| `CLERK_JWKS_URL` | `https://<your-subdomain>.clerk.accounts.dev/.well-known/jwks.json` |
| `VITE_CLERK_PUBLISHABLE_KEY` | Clerk dashboard → API keys → Publishable key (`pk_…`) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | cloud.langfuse.com → project settings (optional — blank is a supported no-op) |

They are all in `services/api/.env` and `apps/web/.env.local` already. **Read them
from there; do not copy them into any file that git tracks.** `.do/app.yaml` ships
placeholders on purpose.

---

## 1. Create the app

```bash
doctl auth init                       # paste a DO API token
doctl apps spec validate .do/app.yaml # structural check; --schema-only works offline
doctl apps create --spec .do/app.yaml
```

The first deploy provisions the Postgres cluster, which takes several minutes
before any build starts. `doctl apps list` gives you the app id; keep it.

## 2. Set the secrets — after creation, never in the file

`.do/app.yaml` carries `REPLACE_IN_DO_DASHBOARD` for every credential. Replace
them **in the App Platform console** (Settings → your component → Environment
Variables), which encrypts each value to an `EV[1:…]` blob so it is never legible
again, not even to you.

| Component | Key | Type | Note |
|---|---|---|---|
| `api` | `ANTHROPIC_API_KEY` | SECRET | |
| `api` | `CLERK_SECRET_KEY` | SECRET | |
| `api` | `LANGFUSE_SECRET_KEY` | SECRET | |
| `api` | `CLERK_JWKS_URL` | plain | public document |
| `api` | `LANGFUSE_PUBLIC_KEY` | plain | |
| `web` | `VITE_CLERK_PUBLISHABLE_KEY` | plain, **BUILD_TIME** | see below |
| `api` | `DATABASE_URL` | plain | already correct — leave it alone |
| `api` | `ENVIRONMENT=production` | plain | already correct — **do not remove** |

Two of those rows are load-bearing:

- **`VITE_CLERK_PUBLISHABLE_KEY` is `BUILD_TIME`.** Vite inlines
  `import.meta.env.*` into the bundle at build; changing it later has no effect
  until the site is rebuilt. And it is deliberately *not* SECRET: Clerk publishes
  it in every frontend bundle. It is the only key the SPA may ever hold (§2, §13);
  everything else stays behind FastAPI. If it is missing the app renders a
  full-screen "did not start" page rather than a working-looking unauthenticated
  app (DEVIATIONS 6.16).
- **`DATABASE_URL` is composed, not `${db.DATABASE_URL}`.** DO's own binding hands
  out a bare `postgresql://` URL; SQLAlchemy resolves that to psycopg2, which is
  not installed, and `create_async_engine` raises. The spec composes
  `postgresql+psycopg://…?sslmode=require` from `${db.USERNAME}` and friends.
  Sharp edge: a `%` in the generated password breaks Alembic (`set_main_option`
  runs the URL through ConfigParser interpolation). DO's passwords are
  `AVNS_`+alphanumerics, so this has not bitten — but if `migrate` dies with an
  interpolation error, that is why.

Then redeploy so the values take: `doctl apps create-deployment <app-id>`.

## 3. What happens on every deploy

1. **Build** — `services/api/Dockerfile` with the **repo root** as context (the
   image needs `data/` and `services/api/` under one root; see the header comment
   in the Dockerfile). The static site builds with bun against the committed
   `apps/web/bun.lock`.
2. **`migrate` (PRE_DEPLOY)** — `alembic upgrade head`, exactly once. **The API
   image never migrates at start**: N instances starting together would race each
   other through the same DDL. A failed migration aborts the deploy, so the new
   code never reaches traffic against a schema it cannot use.
3. **Health check** — `GET /api/health` until it answers 200.

## 4. After the FIRST deploy only — seed, precompute, warm

The database is empty. Run these once, in order, from a console on the running
`api` component (`doctl apps console <app-id> --component api`, or the *Console*
tab in the dashboard). They are the same three `make` targets, minus the local
`make` wrapper:

```bash
cd /app/services/api

python -m storyforge.seed        # make seed       — stages, GPCs, sight words, themes, assets (§4)
python -m storyforge.precompute  # make precompute — the allowed_words table (§5.4), ~2s
python -m storyforge.warm        # make warm       — pre-generate the story pool (§7). SLOW. Costs money.
```

- `make seed`'s `migrate` prerequisite is already satisfied by the PRE_DEPLOY job.
- All three are **idempotent**; re-running them is safe.
- `make warm` calls Anthropic per story, takes many minutes, and is the only step
  that spends money. It is also the step §17's demo depends on — without a warm
  pool, `POST /api/stories/request` returns **503** telling you to run it
  (DEVIATIONS 4.11). Run it in a `tmux`/`screen` or expect the console to
  disconnect.
- Watch its exit code. `run_warm` exits **non-zero** if any story lands with no
  questions or no tagged segments (DEVIATIONS 5.14) — repair with
  `python -m storyforge.warm --backfill`.

## 5. One manual Clerk step the API cannot do for you

Every `require_admin` route (`POST /api/validate`, the allowed-words browser —
i.e. §17 demo items 1 and 2) will **403 for everyone**, including you, until a
human customises the session token. A real Clerk session token carries no
`public_metadata` claim by default, so `auth.py` correctly resolves every session
to `parent` (DEVIATIONS 6.20, verified empirically — this is not settable through
the Clerk API).

> Clerk dashboard → **Sessions** → *Customize session token* →
> `{"public_metadata": "{{user.public_metadata}}"}`
> and set `public_metadata = {"role": "admin"}` on your own user.

Also add the app's domain to Clerk's allowed origins, or the SDK will refuse to
mint tokens for it.

## 6. Verify — the same checks that were run locally

```bash
APP=https://<your-app>.ondigitalocean.app

curl -s $APP/api/health | jq          # every component "ok"; "error" anywhere = broken
curl -s -o /dev/null -w '%{http_code}\n' $APP/api/kids     # must be 401
curl -s -o /dev/null -w '%{http_code}\n' $APP/read/1       # must be 200 (catchall_document)
```

`/api/health` **returns HTTP 200 even when the database is down** — the verdict is
in the body (`status: ok | pending | error`). DO's health check can only read the
status code, so **a database outage will not fail DO's check.** The Dockerfile's
own `HEALTHCHECK` does read the body; DO does not use it. Read the JSON yourself
after a deploy, do not trust the green dot.

Then walk §17 in a browser: kid mode, a story, the parent dashboard.

## 7. Things that will bite

**§18's `DEMO_BYPASS_AUTH` cannot be turned on here, by design.** It is absent from
the spec, and `ENVIRONMENT=production` is present specifically so that adding it
makes the container **refuse to boot** with a message naming the signal it saw.
DO's own `APP_URL`/`APP_DOMAIN` and the remote database host trip the same guard
independently — all three were tested against the built image. If a Clerk outage
takes the hosted demo down, the answer is to run the demo locally, not to weaken
the guard.

**Do not raise `instance_count` or `--workers`.** `stories.limiter` is in-process,
so §9's "10 story requests per minute" becomes 10 × workers × instances the moment
you scale out (DEVIATIONS 4.10). Scaling needs a shared counter first.

**`apps/web/dist` and `services/api/.venv` never enter the image.** The root
`.dockerignore` excludes them along with `.env`, `node_modules`, `.git` and the
test tree. Verify a change to it by exporting a real context, not by reading it:

```bash
printf 'FROM scratch\nCOPY . /\n' | docker build --no-cache -f - -o type=local,dest=/tmp/ctx .
find /tmp/ctx \( -name '.env' -o -name node_modules -o -name .venv \)   # must be empty
```

**Tailwind purges only on build.** A dynamically-named class that works in `make
dev` can vanish from `dist/` silently (STATE invariant 7). Any CSS change must be
re-checked against a served `dist/`, not the dev server.

---

## Local rehearsal (no DigitalOcean account needed)

Everything above except the DO API was exercised this way:

```bash
docker compose up -d
docker build -f services/api/Dockerfile -t storyforge-api:local .

# migrations, exactly as the PRE_DEPLOY job runs them
docker run --rm --network delhi_default \
  -e DATABASE_URL='postgresql+psycopg://storyforge:storyforge@db:5432/storyforge' \
  --entrypoint sh storyforge-api:local -c 'cd /app/services/api && alembic upgrade head'

# the service
docker run -d --name sf-api --network delhi_default -p 8080:8080 \
  -e DATABASE_URL='postgresql+psycopg://storyforge:storyforge@db:5432/storyforge' \
  storyforge-api:local
curl -s localhost:8080/api/health | jq

# the SPA, production bundle
cd apps/web && bun install --frozen-lockfile && bun run build && bun run preview
```

---

## Decision: DO builds from the GitHub mirror (and the trap that comes with it)

App Platform cannot read `labs.gauntletai.com`, so `.do/app.yaml` points at
`github.com/HankH18/story-forge`. This is defensible because the mirror is a
**build input, not a source of truth**: GitLab stays canonical and stays the
submission target, the mirror holds the same commits, and no credential or
deploy key lives on the GitHub side beyond DO's own GitHub app.

**The trap.** All three components set `deploy_on_push: true`, watching the
*mirror*. `git remote -v` currently reads:

    origin  https://labs.gauntletai.com/hankholcomb/story-forge.git (fetch)
    origin  https://labs.gauntletai.com/hankholcomb/story-forge.git (push)
    origin  https://github.com/HankH18/story-forge.git             (push)

Two push URLs on one remote, so a plain `git push origin main` reaches both and
the mirror cannot silently fall behind. That property is the entire reason this
is safe — and it is one `git remote set-url` away from being untrue. If anyone
removes the second push URL, or pushes with an explicit URL, or force-pushes only
one side, **DO will keep building and serving the last commit the mirror saw,
with no error anywhere.** A deploy that appears to succeed while shipping stale
code is worse than a failed one.

Guard: before any deploy that matters, confirm the two are identical —

    git ls-remote https://labs.gauntletai.com/hankholcomb/story-forge.git main
    git ls-remote https://github.com/HankH18/story-forge.git main

Same SHA, or stop. If you would rather not depend on that discipline, the
container-registry path above removes DO's build step for the API entirely; the
static site still needs a git source, so the mirror does not go away.

Per the repo-wide rule, neither remote stores a token: pushes to GitLab supply a
PAT in a one-off URL and the saved remote stays credential-free.
