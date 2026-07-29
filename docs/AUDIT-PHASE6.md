# Phase 6 adversarial audit — Clerk auth, the parent surface, the sign-in UI

Adversarial read of §9, §13, §17, §18 and DEVIATIONS 6.x, driven live rather than
read. Ranked by **real user harm**: a child seeing undecodable text or a dead end
outranks any token edge case.

Everything below is either **CONFIRMED** (reproduced, with the exact commands) or
**SUSPECTED** (reasoned, not triggered), and each is labelled. Categories where I
found nothing are said plainly at the end rather than padded.

## How this was run

- The API on `:8000` and the web dev server on `:5173` were **not restarted or
  killed**. All enforced-auth testing ran on API instances I started on `:8010`
  (enforced), `:8011` (JWKS stub), `:8012` (issuer mismatch), plus a small
  reverse proxy on `:5175`/`:5176` that serves the existing `:5173` SPA but
  routes `/api/*` to an enforced API. Browser work used gstack `browse` with
  `BROWSE_SERVER_PORT=24781`; the daemon is stopped.
- Real Clerk sessions were minted through the Backend API for
  `user_3HBc8LNBQdWzlwbgKrzXYdH1SDa`. **All three sessions created are revoked**
  (`GET /v1/sessions?user_id=…` now returns `revoked` for every row). No users
  were created, no dashboard setting was changed, no secret value is printed here.
- Three kid rows were created through `POST /api/kids` to exercise ownership and
  then deleted. `kids` is back to the five rows it held at the start.

---

## ENVIRONMENT — the premise of this audit was false, and that is itself finding 0

**CONFIRMED. Severity: high (process/verification integrity).**

The task brief states `:8000` runs with auth ENFORCED and `DEMO_BYPASS_AUTH=false`.
It did not. The process serving `:8000` — the one the `:5173` dev server proxies
every request to — was started with the flag **on**:

```
$ ps -o pid,lstart,command -p 58576
58576  Wed Jul 29 12:42:27 2026  .venv/bin/python .venv/bin/uvicorn storyforge.main:app --port 8000
$ ps eww 58576 | tr ' ' '\n' | grep DEMO
DEMO_BYPASS_AUTH=true
DEMO_BYPASS_ROLE=parent
```

`services/api/.env` says `DEMO_BYPASS_AUTH=false`; the process environment
overrides the dotenv, so the file is not evidence. Three responses confirm the
bypass was live, not just configured:

```
$ curl -s -w " [%{http_code}]" http://localhost:8000/api/kids          # NO Authorization header
[] [200]
$ curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer <real RS256>" \
       -H 'content-type: application/json' -d '{"text":"The cat sat.","stage_id":3}' \
       http://localhost:8000/api/validate
403
```

`GET /api/kids` unauthenticated → **200**, and a token whose payload genuinely
contains `public_metadata:{role:"admin"}` → **403** on the admin route. Both are
the signature of `current_principal` taking the bypass branch
(`auth.py:714-728`) and serving everything as `demo_local_parent` with
`role=parent`. The same token against my enforced instance returns **200** on
`/api/validate`, so the token was fine and the server was not.

Consequences:

1. The prior verification listed in the brief (`no token -> 401`, real token
   `-> 200` on `/api/validate`, `<UserButton/>` at 48×48 on a signed-in page) was
   run against something; it was not run against this `:8000`, because this
   `:8000` cannot produce a 401. Any browser observation made through `:5173`
   during that period was an observation of an **unauthenticated app**.
2. `/api/kids/162/progress -> 404` was cited as proof that a parent cannot see
   another parent's kid. It is not: the caller was `demo_local_parent`, and kid
   162 is owned by nobody at all (see C4). The 404 is real; the inference is not.
3. **Nothing in any HTTP response says the bypass is on.** The banner
   (`auth.py:434-438, 718-724`) is a server log line. `GET /api/health` reports
   `database`, `decoder`, `anthropic` and never mentions it
   (`services/api/storyforge/api/health.py`). This is DEVIATIONS 6.16's failure —
   "the app looked signed-in because the API was running §18's `DEMO_BYPASS_AUTH`"
   — recurring one phase after it was written down, and the fix that was applied
   (make the *SPA* refuse to boot without a Clerk key) does not detect it, because
   the SPA had a valid key the whole time.

   **The single highest-leverage fix in this report:** add the bypass to the
   `/api/health` payload (`{"demo_bypass_auth": true, "principal":
   "demo_local_parent"}`) and to a response header, so "is this API actually
   checking anything?" is answerable in one curl by anyone, including a grader.

*(Mid-audit, at ~17:58, the `:8000` process disappeared on its own — I never
signalled it. `:5173` is still up. Most likely the concurrent agent working in
`pipeline.py`/`warm.py` restarted it. The evidence above was captured before that.)*

---

## CONFIRMED findings, ranked by harm to a real user

### C1 — Every kid-mode session runs at stage 4 regardless of the child's real stage. A stage-1 reader is served text that is 17.6% decodable.

**CONFIRMED. Severity: CRITICAL — this breaks §0/§1, the product's entire claim,
in the shipped database state, through the normal UI, with no attacker.**

Reproduction (SPA served from the live `:5173` bundle, API enforced):

```
browse goto http://localhost:5175/read/1648      # kid 1648 = "kid", kids.stage_id = 1
browse js "window.__cap=[];const of=window.fetch;window.fetch=function(u,o){
             if(String(u).includes('stories/request'))window.__cap.push(o&&o.body);
             return of.apply(this,arguments)};'ok'"
browse click @e1                                  # tap "Dragons"
browse js "JSON.stringify(window.__cap)"
=> ["{\"stage_id\":4,\"theme_id_or_slug\":\"dragons\"}"]
```

A child whose row says **stage 1** causes a **stage 4** request. The story that
comes back, run through the project's own validator at stage 1:

```
POST /api/validate {text: "A dragon ran into a cave. It had fire. …", stage_id: 1}
=> decodability = 0.176,  violations = 28 / 34 scored words
   (same text at stage 3 = 0.786, at stage 4 = 1.0)
```

§1 requires ≥95%. This is 17.6%.

The chain, all four links confirmed:

1. `services/api/storyforge/migrations/versions/ab5a866f52fc_…py:96` —
   `op.execute("UPDATE kids SET parent_id = NULL WHERE parent_id IS NOT NULL")`.
   The Phase 6 migration adds the FK and deliberately orphans every pre-existing
   kid. The migration's own docstring argues NULL is the safe direction, and for
   *confidentiality* it is. Nothing accounted for what it does to availability.
   Live: all five kid rows have `parent_id IS NULL`.
2. `services/api/storyforge/api/kids.py:127-130` — `_LIST_KIDS_SQL` filters
   `WHERE parent_id = :parent_id`, so `GET /api/kids` returns `[]` for every real
   Clerk user. Verified with a real token: `[] [200]`.
3. `apps/web/src/lib/api.ts:334-337` — `resolveKid` returns
   `kids.find(k => k.id === kidId) ?? kids[0] ?? null`. With an empty list it
   returns `null`, permanently.
4. `apps/web/src/routes/KidSession.tsx:109` —
   `const stage = kid?.stage_id ?? (stageFromQuery > 0 ? stageFromQuery : DEFAULT_STAGE)`
   with `DEFAULT_STAGE = 4` at line 71.

`DEFAULT_STAGE`'s comment says it "only applies when the parent has no kids at
all". After this migration **that is the state of every account in the database**,
and the fallback is silent: the theme picker just drops the "hello {name}"
greeting, which nobody reads as "this child's curriculum position has been
discarded".

There is a second, narrower path to the same defect that survives even after
`parent_id` is backfilled: `resolveKid` is awaited in an effect
(`KidSession.tsx:114`) while `stage` is computed during render, so a child who
taps a theme before `GET /api/kids` returns also gets `DEFAULT_STAGE`. I did not
trigger this one (local `/api/kids` answers in ~2 ms) — **SUSPECTED**, but the
fix for the confirmed half fixes it too: refuse to request a story until `kid` has
resolved, and drop `DEFAULT_STAGE` in favour of the lowest stage.

**Expected:** a stage-1 reader is served stage-1 text.
**Observed:** every reader is served stage-4 text.

### C2 — `POST /api/stories/request` takes the stage from the client, and no later layer re-checks it. Reachable from the address bar.

**CONFIRMED. Severity: CRITICAL.**

`services/api/storyforge/api/stories.py:237` accepts `stage_id` from the body with
only `ge=1, le=12`; `:448-455` checks the stage *exists*; `:251` filters
`stage_id <= :stage`. The docstring at `:19-24` calls that filter "a child-safety
bound, not an optimisation". It is a bound **relative to a number the client
chose**, which makes it not a bound.

```
POST /api/stories/request {"stage_id":12,"theme_id_or_slug":"dragons"}
=> 200, story 19, stage_id 5, fallback.kind "lower_stage"
   "The dragon had a cave. … The dragon began to give fire fast. …
    They became close. The man said thanks."

POST /api/validate {text: <that text>, stage_id: 1}
=> decodability = 0.130,  violations = 40 / 46
```

The ladder itself is honest — I verified across a full stage × theme matrix that
it never climbs above the requested stage (`s6 → served s5 lower_stage`,
`s12 → served s5 lower_stage`). The defect is only that the anchor is untrusted.

**It is reachable without any tooling.** Because `kid` is null (C1), the `?stage=`
query parameter wins:

```
browse goto "http://localhost:5175/read/1648?stage=12"
browse click @e1
=> posted {"stage_id":12,"theme_id_or_slug":"dragons"}
=> on screen: "The dragon had a cave."   (13.0% decodable for this reader)
```

**`stories.py:40-47` argues the ownership binding happens later, at
`POST /api/readings`. It does not check the stage there either.** Clean repro:

```
POST /api/kids     {"display_name":"Repro","stage_id":1}      -> 201, kid 4169
POST /api/stories/request {"stage_id":12,...}                  -> story 1686, stage 5
POST /api/readings {"kid_id":4169,"story_id":1686}             -> 201
=> readings.id 5465 | kid 4169 (stage 1) | story 1686 (stage 5) | reading.stage_id 1
```

`services/api/storyforge/api/readings.py:582-593` resolves `owned_kid` and checks
`story.status in SERVABLE_STATUSES`, and stops there. There is no
`story.stage_id <= kid.stage_id` anywhere in the request path, the read path, or
the write path. **Nothing server-side stops it at any layer.**

**Fix shape:** `StoryRequest` should carry `kid_id` (which is what §9 specifies —
`POST /api/stories/request {kid_id, theme_id}`; DEVIATIONS 4.7 deferred it and it
was never reinstated), resolve the stage through `auth.owned_kid`, and
`start_reading` should reject a story above the kid's stage as a second gate.

**Consequence beyond content (SUSPECTED, mechanism confirmed):** `readings.stage_id`
is stamped from the kid (`readings.py:600`) while §8's "5 distinct stories
completed at current stage" counts those rows. Five stage-5 stories obtained this
way therefore satisfy the stories criterion for a stage-1 child. The accuracy
criterion is denominated on stage-1 GPCs (`_STAGE_EVIDENCE_SQL`, `g.stage_id =
:stage_id`), so this is not a clean forge on its own — but the guard DEVIATIONS
6.7 added counts the right rows for the wrong stories.

### C3 — All five existing readers are permanently unreachable; the Phase 6 DoD is unverified.

**CONFIRMED. Severity: high.**

Because `parent_id IS NULL` on all five kids (C1, link 1), for every real Clerk
account:

- `GET /api/kids` → `[]` (parent home shows "no readers").
- `GET /api/kids/{162,163,1648,2622,2665}/progress` → 404 for everybody.
- Every reading, every `gpc_stats` row and every promotion decision accumulated
  in Phase 5 is now behind a row nobody owns. The data is intact; no principal
  can reach it.

The DoD is "auth-scoped e2e: two parents cannot see each other's kids". That
property **is** correctly implemented — but it could not have been demonstrated
against this database, because there were zero owned kids and exactly one Clerk
user. I established it by creating one kid under the real Clerk parent and one
under `demo_local_parent`, then crossing them:

```
kid 4096 -> owner user 774 (Clerk)      kid 4097 -> owner user 969 (demo)
GET /api/kids/4097/progress   as 774 -> 404 {"detail":"kid 4097 not found"}
GET /api/kids/999999/progress as 774 -> 404 {"detail":"kid 999999 not found"}
POST /api/readings {"kid_id":4097,...} as 774 -> 404 {"detail":"kid 4097 not found"}
```

Bodies are byte-identical; ownership travels through `auth.owned_kid`
(`auth.py:790-804`) and `owned_reading` as documented. **DEVIATIONS 6.9 and 6.10
hold.** The gap is that the shipped data cannot exercise them, and a migration
that silences the whole dashboard should not have been merged without a
backfill or a documented "assign your readers" step.

**Fix shape:** the migration needs a companion — either a one-time
`UPDATE kids SET parent_id = (SELECT id FROM users WHERE clerk_id = :owner)`
run by the operator, or an "unclaimed readers" adoption path on `/app`.
`docs/DEPLOY.md` currently says nothing about it.

### C4 — The 401 screen contradicts the header it sits under, and the remedy it offers is a loop.

**CONFIRMED. Severity: medium (parent-facing dead end).**

Setup: an API that verifies signatures but rejects the issuer, so a genuinely
signed-in SPA gets 401 on every call — i.e. exactly the "session expired
mid-session" case `ParentError` exists for.

```
uvicorn … --port 8012  with CLERK_ISSUER=https://wrong-issuer.example
browse goto http://localhost:5176/app
```

`ParentError` renders correctly (`ui/ParentShell.tsx:93-153`): "Please sign in
again", `GET /api/kids → 401`, a "Try again" and a "Sign in". Screenshot
confirms it is painted, not just in the DOM. Two defects:

1. **The header above it reads "SIGNED IN AS HOLCOMBHANK@GMAIL.COM" with a live
   `<UserButton/>`**, because `SignedIn`/`SignedOut` in `AccountControl`
   (`ParentShell.tsx:55-73`) read Clerk's client state, which is still valid.
   The page tells the parent two contradictory things simultaneously.
2. **Clicking "Sign in" returns to `/app` with the same error.**
   `ParentShell.tsx:141-148` links to `SIGN_IN_PATH`; `<SignIn>` cannot render
   for an already-signed-in user and immediately redirects back:

   ```
   URL after clicking "Sign in": http://localhost:5176/app
   console: "The <SignIn/> component cannot render when a user is already signed
             in … Clerk is redirecting to the `afterSignIn` URL instead."
   ```

The remedy for "your session has expired" has to be `signOut()` first (or
`<SignOutButton redirectUrl="/sign-in">`), because the whole premise of the
screen is that Clerk thinks the session is fine and the API disagrees.

### C5 — The 403 branch — the entire DEVIATIONS 6.20 fix — is unreachable.

**CONFIRMED. Severity: medium (dead code presenting as a safety net).**

`ParentError` is rendered from exactly three sites:

- `apps/web/src/routes/ParentHome.tsx:64` — from `fetchKids()` → `GET /api/kids`
- `apps/web/src/routes/ParentDashboard.tsx:81` — from `fetchProgress()` → `GET /api/kids/{id}/progress`
- `apps/web/src/routes/ParentDashboard.tsx:65` — a synthetic `status={404}`

Both real routes are guarded by `require_parent` (`auth.py:736-747`), **which
never returns 403** — it returns the principal unconditionally. `require_admin`
appears on exactly one route in the codebase, `POST /api/validate`
(`api/validate.py:49`), and `grep -rn "api/validate|api/admin" apps/web/src`
finds only a prose comment in `Landing.tsx:46`. No web code calls an admin route.

So `status === 403` at `ParentShell.tsx:103` cannot fire, and
`MISSING_ROLE_CLAIM_DETAIL` (`lib/authPaths.ts:39-44`) — the careful sentence
naming the Clerk dashboard action — is displayed to nobody. `isUnauthorized`
(`lib/api.ts:376-378`) folds 403 in for the same non-existent case, and
`AddKid.tsx:61` repeats the pattern.

This is not wrong code; it is a claim in `DEVIATIONS 6.20` ("`ParentError` now
separates 403 from 401") that reads as shipped behaviour and is currently
unexercisable. It becomes real the moment the Phase 8 admin portal lands. Either
say so in DEVIATIONS, or land the admin route that needs it.

### C6 — The §18 startup guard has real gaps, and the most likely one is this repo's own `docker-compose.yml`.

**CONFIRMED. Severity: medium-high.**

Each row below is `DEMO_BYPASS_AUTH=true` plus the named variable, importing
`storyforge.auth`:

| configuration | result |
|---|---|
| `ENVIRONMENT=production` | **refused** ✓ |
| `NODE_ENV=production` | **refused** ✓ |
| `DIGITALOCEAN_APP_ID=…` | **refused** ✓ |
| `DATABASE_URL=…@db.ondigitalocean.com:25060/…` | **refused** ✓ |
| `CLERK_SECRET_KEY=sk_live_…` | **refused** ✓ |
| **`DATABASE_URL=postgresql+psycopg://u:p@db:5432/storyforge`** | **BOOTS** ✗ |
| `DATABASE_URL=…@/storyforge?host=/var/run/postgresql` (unix socket) | **BOOTS** ✗ |
| `ENVIRONMENT=prod-us` | **BOOTS** ✗ |
| `PORT=8080` | **BOOTS** ✗ |
| `K_SERVICE=api` (Cloud Run) | **BOOTS** ✗ |
| `CONTAINER_APP_NAME=…` (Azure Container Apps) | **BOOTS** ✗ |
| `AWS_LAMBDA_FUNCTION_NAME=…` | **BOOTS** ✗ |
| `HEROKU_APP_NAME=…` | **BOOTS** ✗ |
| `NOMAD_ALLOC_ID=…`, `OPENSHIFT_BUILD_NAME=…`, `CF_PAGES=1` | **BOOTS** ✗ |
| `DEMO_BYPASS_ROLE=admin` | **BOOTS**, `role=admin` (documented, 6.11) |

**The one that matters:** `db` is in `LOCAL_DB_HOSTS` (`auth.py:430-432`), and
`db` is the service name in this repo's committed `docker-compose.yml`. Stand
that compose file up on a rented VPS with `DEMO_BYPASS_AUTH=true` — the single
most obvious way to "deploy the demo somewhere" without App Platform — and the
guard sees a local-looking database, no environment variable, and a `sk_test_`
key, and boots a **fully unauthenticated API on a public address**. Same for a
unix-socket `DATABASE_URL`, whose hostname parses to `""`.

The guard's protection is entirely a function of *someone having set a variable*.
A bare `docker run -e DEMO_BYPASS_AUTH=true` on a public host sets none of them.
`.do/app.yaml` is safe because it hard-codes `ENVIRONMENT: production` — that is
the documented path and it works. Every other path is unguarded.

**Fix shape (cheap, high value):** invert the test. Require an explicit positive
laptop assertion rather than the absence of ~25 negative signals — e.g. refuse
unless `STORYFORGE_LOCAL_DEV=i-understand-this-disables-auth` is present *and*
the process is not listening on `0.0.0.0`. Bind-address is the honest
discriminator: a laptop demo binds `127.0.0.1`, a deployment does not. Failing
that, at minimum add `PORT`, `K_SERVICE`, `CONTAINER_APP_NAME`,
`AWS_LAMBDA_FUNCTION_NAME`, `HEROKU_APP_NAME`, and switch the `ENV_NAME_VARS`
comparison from exact-set membership to a `prod`/`stag`/`live` prefix match.

### C7 — `safeRedirectPath` misses backslash, tab, CR and LF. Only Clerk's own check stops the redirect.

**CONFIRMED (function-level bypass), CONFIRMED (currently not exploitable
end-to-end). Severity: medium — a defence-in-depth control that does not work,
masked by a third-party control the code does not know it depends on.**

`apps/web/src/lib/authPaths.ts:55-60` rejects a value that does not start with
`/` or that starts with `//`. The WHATWG URL parser treats `\`, and leading
whitespace including TAB/CR/LF, as equivalent to `/` when resolving:

```
raw                        safeRedirectPath        new URL(result,'http://localhost:5173')
"//evil.example/x"         "/app"                  http://localhost:5173/app          ✓
"https://evil.example"     "/app"                  http://localhost:5173/app          ✓
"javascript:alert(1)"      "/app"                  http://localhost:5173/app          ✓
"/\\evil.example/x"        "/\\evil.example/x"     http://evil.example/x         <== OFF-ORIGIN
"/\\/evil.example"         (passed through)        http://evil.example/          <== OFF-ORIGIN
"/\t/evil.example"         (passed through)        http://evil.example/          <== OFF-ORIGIN
"/\n//evil.example"        (passed through)        http://evil.example/          <== OFF-ORIGIN
"/\r\n//evil.example"      (passed through)        http://evil.example/          <== OFF-ORIGIN
"/\\@evil.example"         (passed through)        http://evil.example/          <== OFF-ORIGIN
"/%2F%2Fevil.example"      (passed through)        http://localhost:5173/%2F%2F… ✓ (percent stays encoded)
"/ //evil.example"         (passed through)        http://localhost:5173/%20//…  ✓ (space is encoded, not stripped)
```

So the three shapes DEVIATIONS 6.23 claims to neutralise **are** neutralised; five
others are not. End-to-end through the real sign-in route:

```
browse goto "http://localhost:5175/sign-in?redirect_url=%2F%5Cevil.example%2Fx"
=> URL after: http://localhost:5175/app
=> console: "Clerk: Redirect URL http://evil.example/x is not on one of the
             allowedRedirectOrigins, falling back to the default redirect URL."
```

**The app's validator let it through; Clerk's `allowedRedirectOrigins` caught it.**
Today that is a complete stop, so there is no live open redirect. But the code
comment presents `safeRedirectPath` as the control, and the value is used only as
`forceRedirectUrl` on `<SignIn>`/`<SignUp>` (`routes/SignIn.tsx:79,100`) — the day
anyone passes it to `navigate()`, `window.location`, or a `<Link to=…>`, it is a
live open redirect with a session attached. Fix is one line: after the existing
checks, `if (/[\\\s]/.test(raw)) return AFTER_SIGN_IN_PATH`, or better, resolve
against `location.origin` and compare origins.

### C8 — JWKS fails closed, but with no stale-key fallback: a Clerk blip locks out every user, including a child mid-story.

**CONFIRMED. Severity: medium (availability). The fail-open question is answered:
it fails CLOSED, which is correct.**

Against a controllable JWKS stub (`CLERK_JWKS_TTL_S=5`, real Clerk keys):

| stub state | result |
|---|---|
| healthy | `200` |
| **500 after the TTL lapses** | `503 {"detail":"JWKS unavailable: HTTPStatusError: Server error '500 …'"}` |
| **connection refused** | `503 {"detail":"JWKS unavailable: ConnectError: All connection attempts failed"}` |
| rotated (`kid` replaced) | `401 {"detail":"unknown signing key 'ins_3HB…'"}` |
| healthy again | recovers |

No fail-open: an unreachable JWKS never verifies anything. Good.

The cost is that `JwksCache._fetch` (`auth.py:267-287`) lets the transport
exception escape before `key_for` can fall back to the keys it is still holding —
keys that are **still valid and would verify the token correctly**. So a JWKS
outage lasting longer than the 600 s TTL takes the entire product down: the
parent surface renders "Something went wrong" (503 is neither 401 nor 403 nor 0),
and kid mode retries three times and parks on the printer screen forever (C10).

RFC-conformant caches serve stale on failure. Two lines: catch `JwksUnavailable`
inside `key_for`, and if `kid in self._keys`, log loudly and serve the stale key
(optionally capped at some multiple of the TTL).

**SUSPECTED, same code:** `_last_rotation_fetch` is set on *every* `_fetch`
(`auth.py:270`), including ordinary TTL refreshes, so the 60 s rotation cooldown
is armed by routine traffic. When Clerk publishes a new signing key, a user
holding a token signed by it can be 401'd for up to 60 s even though a refetch
would fix it. I could not trigger this against real Clerk (it publishes old and
new keys together during rotation, so the window may never be observable in
practice), which is why it is SUSPECTED. Setting `_last_rotation_fetch` only in
the rotation branch would remove the question.

### C9 — A signed-in non-owner at `/read/:otherKidId` is silently rebound to their *own* first reader, and the reading is recorded against that child.

**CONFIRMED. Severity: medium (data integrity; the child sees no error, which is
the §01-correct half).**

```
browse goto http://localhost:5175/read/162     # 162 is not this account's kid
=> full kid shell renders: "hello Audit"        # Audit = kid 4096, THIS account's kid
browse click @e1                                # tap Dragons
=> POST /api/stories/request  200
=> POST /api/readings         201
=> readings row: id 5396 | kid_id 4096 | story 10 | stage 1
```

Answering the brief's question directly: **no child-facing error surface appears.**
§01 is not violated. The theme picker renders, the story loads, the celebration
works. What happens instead is that `resolveKid`'s `?? kids[0]` fallback
(`lib/api.ts:336`) silently substitutes a *different reader* — the URL says 162,
the greeting says "Audit", and the mastery evidence lands on Audit's row.

That fallback is documented and defended in `lib/api.ts:323-333` ("kid mode has no
error state, and 'wrong id in the address bar' is an adult's mistake that a child
would pay for"), and the reasoning is sound for a *typo*. It is wrong for a
*sibling*: a parent with two readers who hands the tablet over on a stale URL gets
child B's session recorded against child A, at child A's stage, with no signal
anywhere. The dashboard then shows a parent facts about their child that are not
about their child — which is precisely the harm `lib/api.ts:345-357` says the
parent surface exists to prevent.

Should it render at all? Yes — bouncing a child to a sign-in wall is worse. But
the substitution must be visible to the adult: land on the theme picker with the
substituted reader's name and a small "reading as {name}" affordance, or, when
the requested id is not in the list and the list is non-empty, send the *adult*
to `/app` to pick.

### C10 — Any API failure in kid mode ends on a friendly screen that never resolves, and a 429 is easy for a child to trigger.

**CONFIRMED. Severity: medium.**

Against a 401-ing API, tapping a theme:

```
[t+3s]  "Writing your story… the printer is working   Try again"
[t+7s]  "Writing your story… the printer is working   Try again"   (indefinitely)
```

This is the designed degradation (`KidSession.tsx:159-169`, three attempts then
`setStalled(true)`), and it is genuinely §01-compliant: no stack trace, no red
box, a friendly sentence and a big button. But "Try again" returns to the picker,
which re-enters the same loop, so a child alone with the tablet has no path
forward at all. The screen says the printer is working when nothing is working.

It is easier to reach than it looks. §9's limiter is 10 requests/minute per user
(`stories.py:76`), and `_SERVE_SQL` is round-robin, so where a (stage, theme) pair
holds one servable story a child pressing "Read another" gets the same story back
(DEVIATIONS 6.7 already says this). Eleven presses in a minute — entirely ordinary
for a five-year-old who wants a *different* dragon story — is a 429, three retries,
and the permanent printer. I hit the 429 myself while enumerating the pool.

Worth considering: on stall, fall back to the last successfully-served story
rather than to an infinite wait. The child re-reads something decodable instead
of staring at a printer.

### C11 — A revoked Clerk session's token keeps working until `exp`.

**CONFIRMED. Severity: low-medium.**

```
GET /api/kids with token T                       -> 200
POST /v1/sessions/{sid}/revoke                   -> 200, status "revoked"
GET /api/kids with the same token T (unexpired)  -> 200
```

`verify_token` (`auth.py:598-616`) checks signature, `exp`, `nbf`, `iss` and
optionally `aud`, and never looks at the `sts` claim (present, and frozen at
`"active"` from mint time) or asks Clerk. This is the ordinary stateless-JWT
trade-off and with Clerk's 60 s browser TTL the exposure is ≤60 s.

It is worth naming only because `auth.py:21-25` makes an adjacent claim that *is*
true — "revoking admin takes effect on the next call rather than at the next
login" — and a reader will generalise it to session revocation, where it is false.
The token I used was Backend-API-minted with a 1800 s lifetime and was accepted
for the full 30 minutes after revocation. One sentence in the docstring fixes the
expectation; checking `sts != "active"` (Clerk emits it) fixes the 60 s window.

### C12 — `/api/themes` and `/api/stages` do not exist. The fallback is safe today by accident of the server's ladder, not because the lists match.

**CONFIRMED. Severity: low today, medium on any content change.**

`services/api/storyforge/main.py:39-44` mounts `health`, `validate`, `stories`,
`kids`, `readings`. §9 lists `GET /api/stages` and `GET /api/themes` as public,
cached routes. Both 404 on every kid-session load, twice per load under
StrictMode:

```
GET /api/themes -> 404   GET /api/themes -> 404
GET /api/kids   -> 200   GET /api/kids   -> 200
```

Answering the brief's question: **the fallback cannot currently offer a theme the
warm pool lacks.** `SEEDED_THEMES` (`lib/api.ts:292-296`) is
`dragons/space/animals`, `data/seed/themes.csv` is exactly those three, and every
one resolves:

```
req s1  dragons -> 200 s1 dragons exact      req s6  dragons -> 200 s5 dragons lower_stage
req s1  space   -> 200 s1 space   exact      req s6  space   -> 200 s5 space   lower_stage
req s1  animals -> 200 s1 animals exact      req s6  animals -> 200 s5 space   lower_stage_other_theme
req s3  ×3      -> 200 exact                 req s12 ×3      -> 200 s5 lower_stage
```

But it is safe for a reason the comment does not give. It is not that the list
matches the pool — it is that `_SERVE_SQL` ignores theme entirely when nothing
matches, so *any* resolvable slug yields a story. The list only has to match
`themes` **the table**, and if it drifts the failure is total:

```
POST /api/stories/request {"stage_id":1,"theme_id_or_slug":"unicorns"}
=> 404 {"detail":"unknown theme 'unicorns' — run `make seed`"}
```

A 404 there is not retryable, so kid mode burns its three attempts and parks on
the printer forever (C10). Adding a theme to `themes.csv` without editing
`lib/api.ts` in the same commit is a soft-brick for every child who taps the new
card. Routing `GET /api/themes` (five lines against a table that already exists)
removes the class.

---

## SUSPECTED (reasoned, not reproduced)

- **`resolveKid` render/effect race** (see C1). Real on a slow network even after
  `parent_id` is backfilled; not triggered locally.
- **Rotation cooldown armed by routine refetches** (see C8). Mechanism read from
  `auth.py:257-270`; real Clerk's overlapping-key publication may make it
  unobservable.
- **Escalated stories satisfy §8's "5 distinct stories at stage"** (see C2).
  The insertion is confirmed; I did not drive five completions to a promotion.
- **`AWS_EXECUTION_ENV`** is in `PLATFORM_VARS` but newer Lambda runtimes
  (al2023) do not set it, so the Lambda case may be unguarded in practice as well
  as in my synthetic test. Not verified against a real Lambda.

---

## Where I found nothing

**Token lifecycle (brief item 2) — no defect. This one is clean.** I instrumented
a live signed-in kid session and sampled `getToken()` + a real authenticated
`fetch` every 10 s:

```
16 probes over 150 s
distinct token jti: 4          (3 refreshes)
HTTP statuses observed: [200]  — no 401, ever
```

Clerk's browser token TTL is **60 s** (measured: `exp - iat = 60`), and
`getToken()` transparently minted a new one ~7 s before each expiry. A refresh was
observed crossing an expiry boundary with a request in flight and it returned 200.
`lib/api.ts`'s getter is literally `() => getToken()` (`lib/auth.tsx:90`), so it
inherits that behaviour. **A long kid-mode reading session does not start failing
mid-story with 401s.** The registration-during-render argument in DEVIATIONS 6.17
is also correct: I saw no unauthenticated first request on any page load.

**JWT verification hardening — nothing to add.** `alg` is pinned before key
lookup, `kid` is mandatory, `require: ["exp","sub"]` is set, `verify_aud`/
`strict_aud` are conditional and correct, and the rotation test exercised the
`kid`-selection path end to end. I did not re-run the `alg:none` / HS256-confusion
cases the brief said were already proven.

**Owner scoping — correct, and the 404-not-403 posture holds.** Verified across
`GET /api/kids/{id}/progress` and `POST /api/readings` with two real principals;
bodies for "not yours" and "does not exist" are byte-identical (C3). DEVIATIONS
6.9 and 6.10 are accurate.

**`require_parent` admits admins without becoming a data bypass.** The admin
account saw only its own kid and 404'd on the other principal's. DEVIATIONS 6.10
holds.

**No layout or rendering defect found.** I looked for one and did not manufacture
one. `ParentError` was confirmed painted by screenshot at 1024×768 (not by
bounding box), the kid theme picker and reading view render correctly at every
state I drove, and the `<UserButton/>` renders. The C4 finding is about the
*content* of a screen that paints correctly, not about its geometry.

---

## What this audit touched, and what it left behind

Created and removed: kid rows `4096` (`Audit`), `4097` (`Demokid`), `4169`
(`Repro`) and their cascaded readings. `kids` is back to its original five rows;
`users` is unchanged (774 admin, 969 demo).

Clerk: three sessions minted through `POST /v1/sessions`, one sign-in ticket
(consumed). **All three sessions revoked** — `GET /v1/sessions?user_id=…` returns
`revoked` for every row. No user created, no dashboard setting changed.

Processes: API instances on `:8010`/`:8011`/`:8012`, proxies on `:5175`/`:5176`,
a JWKS stub on `:8899`, and the `browse` daemon on `:24781` — **all stopped**.
`:5173` was never touched and is still running. `:8000` was never signalled by
this audit and terminated on its own at ~17:58.

No file in the repository was modified except this one.
