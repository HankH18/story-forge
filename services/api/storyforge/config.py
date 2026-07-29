"""Application settings.

All secrets and model IDs live in the environment, never in code (§3: "Model IDs
in env, never hardcoded"). Phase-3+ keys are optional here so Phase 0/1 boot
without them.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Host port 5433 per docs/DEVIATIONS.md 0.1.
    database_url: str = "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge"

    # §13 risk mitigation: renders a fixed parent session when Clerk is
    # unavailable. Must never be true in a deployed environment.
    demo_bypass_auth: bool = False

    # Phase 3+. Absent at Phase 0/1 — /api/health reports these as pending
    # rather than failing.
    anthropic_api_key: str | None = None
    anthropic_model_generate: str | None = None
    anthropic_model_fast: str | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    # Langfuse's own docs and console use LANGFUSE_HOST in some places and
    # LANGFUSE_BASE_URL in others. Accept either so a copy-paste from the
    # console does not silently disable tracing.
    langfuse_host: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    clerk_jwks_url: str | None = None
    clerk_secret_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
