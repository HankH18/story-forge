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
        migrate seed precompute warm eval eval-smoke codegen clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
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

test-web: ## Typecheck the web app
	cd $(WEB) && bun run typecheck

lint: ## Lint and format-check the API
	cd $(API) && uv run ruff check . && uv run ruff format --check .

migrate: db ## Apply database migrations
	cd $(API) && uv run alembic upgrade head

seed: migrate ## Load stages, GPCs and sight words (§4, Appendix A)
	cd $(API) && uv run python -m storyforge.seed

precompute: ## Build the allowed_words table (§5.4)
	cd $(API) && uv run python -m storyforge.precompute

warm: ## Pre-generate the warm story pool (§7)
	@echo "make warm: not implemented until phase 3" >&2; exit 1

eval: ## Full eval harness run across stages and themes (§14)
	@echo "make eval: not implemented until phase 7" >&2; exit 1

eval-smoke: ## Regression gate: 2 stages x 1 theme x 2 stories (§14)
	cd $(API) && uv run python -m storyforge.eval smoke

codegen: ## Regenerate the OpenAPI TypeScript client (§3)
	@echo "make codegen: not implemented until phase 4" >&2; exit 1

clean: ## Remove build artifacts and caches
	rm -rf $(WEB)/dist $(WEB)/node_modules/.vite
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name .pytest_cache -type d -prune -exec rm -rf {} + 2>/dev/null || true
