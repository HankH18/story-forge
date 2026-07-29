"""Anthropic client factory (§3).

Model IDs come from the environment, never hard-coded (§3). The SDK already
retries 429 and 5xx with exponential backoff — `max_retries` tunes it rather than
us hand-rolling a retry loop (§7's retry policy sits ABOVE this, counting whole
pipeline-node attempts, not HTTP attempts).
"""

from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic

from storyforge.config import get_settings

# §7: generation node timeout 30s, other nodes 15s. Set per-call via
# `.with_options(timeout=...)` so one slow node can't stall the pipeline.
GENERATION_TIMEOUT_S = 30.0
FAST_TIMEOUT_S = 15.0


@lru_cache(maxsize=1)
def get_client() -> AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — copy services/api/.env.example to .env "
            "and fill it in. Generation cannot run without it."
        )
    return AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        # HTTP-level retries only. The pipeline's own retry budget (§7:
        # api_fail, retry <= 2 with exponential backoff) wraps this.
        max_retries=2,
    )


def generation_model() -> str:
    """Sonnet-class model for generation and rewrite (§3)."""
    return get_settings().anthropic_model_generate or "claude-sonnet-5"


def fast_model() -> str:
    """Haiku-class model for tagging, moderation and question generation (§3)."""
    return get_settings().anthropic_model_fast or "claude-haiku-4-5"
