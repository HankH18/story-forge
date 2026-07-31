# Story Forge — §0 requires dev, test, eval and seed to work from the repo root.
#
# Targets belonging to later phases are present but fail loudly with the phase
# that implements them (docs/DEVIATIONS.md 0.5). A target that silently succeeds
# without doing anything is worse than a missing one: it manufactures a false
# green.

API := services/api
WEB := apps/web

.DEFAULT_GOAL := help
.PHONY: help install db db-stop dev api web test test-api test-web lint \
        migrate seed precompute warm warm-check promote-check prompt-check \
        mcq2-check fit-check story-word-check vocab-check art-check art-repair \
        warm-plan eval eval-smoke codegen clean

# `[0-9]` is load-bearing: `mcq2-check` was invisible to `make help` without it,
# which is a target that exists and cannot be discovered — the same class as a
# gate that is never run. A digit in a target name must not hide it.
help: ## List available targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install API and web dependencies
	cd $(API) && uv sync
	cd $(WEB) && bun install

db: ## Start Postgres and block until it is genuinely accepting connections
	docker compose up -d
	@printf "waiting for postgres"
	@for i in $$(seq 1 60); do \
		if [ "$$(docker inspect --format='{{.State.Health.Status}}' storyforge-db 2>/dev/null)" = "healthy" ]; then \
			echo " ready"; exit 0; fi; \
		printf "."; sleep 1; \
	done; echo " TIMEOUT"; exit 1

db-stop: ## Stop Postgres (data survives in the pgdata volume)
	docker compose down

dev: db ## Boot Postgres, the API and the web app together
	@bash scripts/dev.sh

api: db ## Run only the API
	cd $(API) && uv run uvicorn storyforge.main:app --reload --port 8000

web: ## Run only the web app
	cd $(WEB) && bun run dev

test: test-api test-web ## Run the full test suite

test-api: db ## Run API + decoder tests (needs Postgres for the health tests)
	cd $(API) && uv run pytest

# Typecheck AND tests. This was `bun run typecheck` alone for five phases, which
# is why `docs/STATE.md` could say "395 tests green" while covering zero lines of
# web behaviour — and every web defect the Phase 6 audit found (a silent default
# stage, a client-chosen stage, a redirect validator that did not validate, a
# substituted child) lived in code a type checker has no opinion about
# (DEVIATIONS 7.30). `tsc` runs first: a test that does not compile is a test
# that does not run.
test-web: ## Typecheck the web app and run its unit tests
	cd $(WEB) && bun run typecheck && bun test src

lint: ## Lint and format-check the API
	cd $(API) && uv run ruff check . && uv run ruff format --check .

migrate: db ## Apply database migrations
	cd $(API) && uv run alembic upgrade head

seed: migrate ## Load stages, GPCs and sight words (§4, Appendix A)
	cd $(API) && uv run python -m storyforge.seed

precompute: ## Build the allowed_words table (§5.4)
	cd $(API) && uv run python -m storyforge.precompute

# §7's matrix is EVERY (stage, theme) pair, and the defaults now say so
# (`--stages 1..11 --themes all`). They said `1,2,3` / `dragons` for five phases,
# so this argument-free target filled 3 stages and 1 of 3 themes while
# docs/STATE.md claimed ">=3 per (stage,theme)" — and because §7's ladder answers
# an empty pair by serving a LOWER stage, silently and correctly, nothing reported
# it. §17's demo is dragons at a low stage, a cell that was covered, so the demo
# passed on a nine-empty-stage pool (AUDIT-PHASE7 F4, DEVIATIONS 8.40).
#
# Idempotent: `pool_depth` fills only the shortfall. `make warm-plan` prices it
# first; `make warm-check` is the gate and must be run afterwards.
warm: ## Pre-generate the warm story pool over §7's full 11 x 3 matrix (SLOW, spends money)
	cd $(API) && uv run python -m storyforge.warm

# The guard, not a reminder (DEVIATIONS 8.41). Non-zero while any (stage, theme)
# pair holds fewer than 3 servable stories, which is the only way a partial pool is
# detectable — at runtime it looks like a working product serving fallbacks. Costs
# nothing: one SQL query, no API client. The module flag is what docs/DEPLOY.md §4
# calls, because that step runs in a DigitalOcean console where `make` does not
# exist; this target is the local wrapper.
warm-check: ## Refuse a partial warm pool: non-zero while any (stage, theme) is below §7's floor
	cd $(API) && uv run python -m storyforge.warm --check-coverage

# The SECOND floor, and the one nothing measured until DEVIATIONS 11.70. §7 asks
# whether a child can be SERVED their own stage; §8 asks whether the stories they
# are served carry the evidence that promotes them off it. The two are
# independent — every pair can hold 3 servable stories, `warm-check` can be green,
# and six of the eleven stages can still be terminal dead ends where a perfect
# reader is never promoted no matter how much they read. Costs nothing: two SQL
# queries and the decoder, no API client.
promote-check: ## Refuse a pool no child can be promoted from: non-zero while any stage is below §8's evidence floor
	cd $(API) && uv run python -m storyforge.warm --check-promotability

# The THIRD gate, and the only one that reads a question's TEXT. §11.3's tap-a-word
# instruction is a pure function of (cue, stage), so when DEVIATIONS 11.60 rebuilt
# the frame ladder every row already in the pool kept its old sentence — 125 of
# 125, for two phases, under a green suite and two green gates, 66 of them saying
# nothing about what the child is being asked to do (11.82). Nothing measured it
# because no other check anywhere looks at what a question SAYS. Free: one SQL
# query, no API client. The repair is `--backfill`, which re-derives the prompts in
# place with no API call and cannot touch `options`, `answer` or any recorded
# answer.
prompt-check: ## Refuse a pool serving tap-a-word instructions no current frame produces
	cd $(API) && uv run python -m storyforge.warm --check-prompts

# The FOURTH gate, and `prompt-check`'s other half: that one reads what a
# tap-a-word question SAYS, this one reads what a two-choice question says.
# DEVIATIONS 11.64 taught `check_mcq2` to refuse a question that names the word
# distinguishing its own two answers, and a false card that is the true card with
# `not` in it. Both refusals apply to DRAFTS, so they reached none of the 37
# servable rows already doing it — `_UNENRICHED_SQL` selected zero stories,
# because every clause it has asks what a story is MISSING and a leaking question
# is present, moderated and countable. That is the third occurrence of the same
# forward-only trap (6.24, 11.82). 7 of the 37 pointed the child at the WRONG card.
# Free: one query per (stage, theme) for the sight words, no API client. The
# repair is `--mcq2-repair` (a DELETE that cannot cascade a recorded answer or
# empty a story) followed by `--backfill` (DEVIATIONS 11.99-11.103).
mcq2-check: ## Refuse a pool serving two-choice questions answerable without reading the story
	cd $(API) && uv run python -m storyforge.warm --check-mcq2

# The FIFTH gate, and the first one that looks at what a child SEES rather than
# at what the database holds. §10: "One segment per screen, no scrolling on
# tablet — if text overflows, generation length is wrong, not the layout." Four
# green gates over a pool in which the LAST page of every story at stages 8-11 —
# the page carrying the ending — did not fit the screen, and clipped BETWEEN
# lines, so it read as finished under a `Finish` button (DEVIATIONS 13.1-13.3).
# It renders every servable segment through the real `ReadingView` in the real
# production bundle at both §10 viewports and both §11 reading sizes. Free: one
# SQL query, a build, and a headless browser; no API client. The remedy is a
# per-stage length budget on GENERATION — never a smaller font (§03 floors it at
# 28px) and never a shorter tap target (§11 floors it at 48px).
fit-check: ## Refuse a pool whose pages do not fit the §10 screen at either reading size
	cd $(WEB) && bun run fit-check

# The SIXTH gate, and the first that reads what a picture CHIP is spent on. A
# story word is a word the child is GIVEN instead of decoding — three slots per
# story and no more (§5.5, `MAX_STORY_WORDS`) — and nothing bounded which words
# could fill one: `story_word_bank.min_stage` is a hand-set SEMANTIC floor with no
# relation to whether the reader can already read the word. Measured over the live
# pool: 225 of 326 chips (69.0%) sat on a word the reader could ALREADY decode,
# and 23 more on words 4-7 stages ahead. `dragon` is decodable from stage 1, so
# every dragons story carried a chip over a word its reader could sound out.
# `pipeline.STORY_WORD_MAX_LOOKAHEAD` is the band and `load_vocabulary` enforces
# it at generation; this gate is the audit of what is already in the pool. Free:
# one SQL query and the decoder, no API client. There is no free repair — the word
# is IN the story, so the remedy is generation (DEVIATIONS 14.20).
story-word-check: ## Refuse a pool whose picture chips sit on words the reader can already decode
	cd $(API) && uv run python -m storyforge.warm --check-story-words

# The SEVENTH gate, and the first that reads the pool against something OUTSIDE
# it. Every other gate asks about what a story CONTAINS — a count, a prompt, an
# mcq2, a chip. This one re-validates every servable title and body against the
# CURRENT `allowed_words`, which is a PRECOMPUTE: `make precompute` rebuilds it
# from `data/*`, and the stories in the pool were written against whatever it held
# that day. DEVIATIONS 11.7-11.11 replaced §5.4's SUBTLEX intersection with a
# child-register filter *because `amid` was reaching five-year-olds*, and that
# changed the vocabulary the generator is GIVEN while changing nothing about the
# 119 stories already servable: `amid` is absent from `allowed_words` at all
# eleven stages and nine servable stories still contained it, three offering it to
# a stage-1 child as a tappable option. Nothing caught it because nothing could —
# `amid` IS decodable, so the 0.95 gate and the serve-time re-check pass it every
# time. The objection is REGISTER, not phonics. The principle, which is bigger
# than `amid`: **a precomputed input changing must invalidate the artefacts built
# from it.** Free: two SQL queries and the decoder, no API client. There is no
# free repair — a word sits inside a sentence (DEVIATIONS 22.1-22.9).
vocab-check: ## Refuse a pool whose stories use words the current allowlist no longer permits
	cd $(API) && uv run python -m storyforge.warm --check-vocabulary

# The EIGHTH gate, and the first whose unit is the STORY rather than the segment,
# the question or the word. The owner's first complaint about this app was "there
# are no images in the stories"; the art is real (33 rows, 33 files, HTTP 200 in
# production) and the defect was in SELECTION. §12's "deterministic tiebreak by
# asset id" degraded to lowest-id-always-wins — a standing preference for one
# picture, re-applied on every page — and 4 servable stories rendered ONE picture
# on all seven pages while 40 more rendered two. **No per-segment count could see
# it**: a story whose seven pages all carry asset 24 records `segments_with_art:
# 7`, full marks, because the repeat does not exist at the segment. Also reports
# the screens rendering §06's placeholder with their two causes separated — the
# model had nothing to draw, or the library owns no picture for these tags — which
# is a curation shopping list and not a bug. Free: two SQL queries, no API client.
# The repair is `make art-repair` and is also free (DEVIATIONS 26.1-26.8).
# That shopping list was then FILLED (28.1-28.6): the pool is 43 rows / 43 files,
# every one of §12's 42 tags is reachable in some theme, and the "no asset for
# these tags" half of the placeholder census is 0. `data/seed/assets.csv` and
# `scripts/art/` are where a new picture is added; `node scripts/generate-art.mjs`
# draws it and cross-checks the CSV both ways.
art-check: ## Refuse a pool whose stories render one repeated picture on every page
	cd $(API) && uv run python -m storyforge.warm --check-art

# Re-runs §12's selection over the tags ALREADY on the rows: the tagging pass has
# been paid for and is stored, so this costs $0.00 and makes no API call. It never
# writes `scene_tags`, never reads a questions row and never edits story text.
# Idempotent. 226 screens across 108 of 110 servable stories on its first run.
art-repair: ## Re-pick every servable story's pictures under the current rule. Free, no API call
	cd $(API) && uv run python -m storyforge.warm --reselect-art

warm-plan: ## What `make warm` would generate and roughly what it would cost. No API calls
	cd $(API) && uv run python -m storyforge.warm --dry-run

# §14's documented command line, verbatim. 11 stages x 3 themes x 5 stories is
# §1 item 2's acceptance matrix, so this target IS the §1 item 2 check: it exits
# non-zero when the criterion is not met and when the spend cap is reached, and
# writes reports/eval-{ts}.md either way. Never a silent success.
# Exit codes: 0 pass · 1 criteria not met or run aborted · 2 spend cap ·
# 3 a `--resume` refused because the live config differs from the stored one
# (DEVIATIONS 8.31).
# `--pricing-date` is deliberately NOT passed and defaults to today, so a run on or
# after 2026-09-01 prices at Sonnet 5's standard rate: freezing a date in this file
# would price every future run against a stale sheet. The report prints the date it
# used, which is what makes the difference visible (AUDIT-PHASE7 F10).
# `reports/` is gitignored, so the run prints the `scripts/eval_doc.py` line that
# regenerates the committed docs/EVAL.md from its own JSON (DEVIATIONS 8.39).
# `make eval EVAL_ARGS="--estimate-only"` projects the spend with no API call.
eval: ## Full eval harness run across stages and themes (§14)
	cd $(API) && uv run python -m storyforge.eval run \
		--stages 1..11 --themes all --n 5 --cold $(EVAL_ARGS)

eval-smoke: ## Regression gate: 2 stages x 1 theme x 2 stories (§14)
	cd $(API) && uv run python -m storyforge.eval smoke

codegen: ## Regenerate the OpenAPI TypeScript client (§3)
	@echo "make codegen: not implemented until phase 4" >&2; exit 1

clean: ## Remove build artifacts and caches
	rm -rf $(WEB)/dist $(WEB)/node_modules/.vite
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name .pytest_cache -type d -prune -exec rm -rf {} + 2>/dev/null || true
