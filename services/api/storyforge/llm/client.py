"""Anthropic client factory and the per-node timeout budgets (§3, §7).

Model IDs come from the environment, never hard-coded (§3).

**Retries.** `max_retries=2` is the whole of §7's `api_fail(retry<=2, exp
backoff)`, and it is the SDK's, not ours (DEVIATIONS 8.3). An earlier comment here
claimed "§7's retry policy sits ABOVE this, counting whole pipeline-node
attempts"; no such loop has ever existed, and adding one on top of the timeouts
below would bound a dead cell at three-times-three attempts. The one retry budget
is stated here so nobody double-counts it.

**Timeouts.** The consequence of `max_retries=2` is that a *timeout is multiplied
by three*, and that a latency read from the outside of an SDK call is the sum of up
to three attempts rather than one generation. That is how §7's 30s produced 11
`api_fail` cells at exactly 91s in the §14 run: 3 x 30s plus the SDK's own backoff
(DEVIATIONS 8.0).
"""

from __future__ import annotations

import math
from functools import lru_cache

import httpx
from anthropic import AsyncAnthropic

from storyforge.config import get_settings

#: §7 says "per-node: timeout (generation 30s, others 15s)". The non-generation
#: nodes keep 15s: they are Haiku calls with small, bounded outputs, and §14
#: measured MODERATING at a p95 of 2.87s.
FAST_TIMEOUT_S = 15.0

#: The slowest sustained output rate measured on this model, over 94 real calls
#: (20 uncensored probes + 74 single-attempt eval generations, DEVIATIONS 8.2).
#: p50 is 94-98 tok/s; this is the floor, used deliberately.
SLOWEST_MEASURED_TOK_PER_S = 68.0

#: The SDK's own default, kept explicit. A float passed to `with_options(timeout=)`
#: reaches httpx as connect == read == write == pool, so `timeout=30.0` was also
#: waiting 30s to open a socket. An unreachable host should fail fast enough that
#: the retry budget can still be spent usefully (DEVIATIONS 8.1).
CONNECT_TIMEOUT_S = 5.0


def timeout_for_tokens(max_output_tokens: int) -> float:
    """A token ceiling expressed as wall clock, rounded up to 10s.

    This is the whole sizing rule, and it exists as a function because the timeout
    is DERIVED (DEVIATIONS 8.7): generation latency is output tokens over a
    throughput that barely moves (r = +0.997 against tokens; only +0.50 against
    stage), so the longest a generation can honestly run is its own token ceiling
    at the slowest rate anyone has measured. Move `max_tokens` and this number has
    to move with it — `tests/test_llm.py` fails until it does.
    """
    return float(math.ceil(max_output_tokens / SLOWEST_MEASURED_TOK_PER_S / 10) * 10)


#: §7 says 30s. That was below the latency of a *healthy* generation — a 30-word
#: stage-1 story measured 24.60s — and cost the §14 run 11 whole cells
#: (DEVIATIONS 8.1). This is `timeout_for_tokens(MAX_OUTPUT_TOKENS)` for the
#: 16000-token ceiling `pipeline._call` uses; it is not imported from
#: `llm.generate` because that module imports this one.
#:
#: A generation still running at this point has hit `max_tokens`, which returns
#: `stop_reason == "max_tokens"` and is already an api_fail by another route — so
#: the clock and the token ceiling now fail the same request for the same reason
#: instead of disagreeing about it.
#:
#: **This is a batch-job budget.** `max_retries=2` triples it, so a cell that
#: stalls three times costs ~12 minutes. That is affordable only because
#: generation is not on the child's request path: `api/stories.py` never calls
#: `run_pipeline` and has no background refill, so every serving path is a
#: warm-pool read. Putting generation on a request path means revisiting this.
GENERATION_TIMEOUT_S = 240.0


def generation_timeout(max_output_tokens: int | None = None) -> httpx.Timeout:
    """The GENERATING / REWRITING node's timeout, as httpx sees it.

    `ANTHROPIC_GENERATION_TIMEOUT_S` overrides the read budget (DEVIATIONS 8.6) —
    an operational number in the environment like the model IDs beside it, and the
    only way to make the real SDK raise a real `APITimeoutError` on demand, which
    is what proves `warm.py` survives one.
    """
    override = get_settings().anthropic_generation_timeout_s
    if override:
        seconds = override
    elif max_output_tokens is None:
        seconds = GENERATION_TIMEOUT_S
    else:
        seconds = timeout_for_tokens(max_output_tokens)
    return httpx.Timeout(seconds, connect=CONNECT_TIMEOUT_S)


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
        # This IS §7's `api_fail(retry<=2, exp backoff)` — the only retry budget
        # in the system, not a lower layer under a second one (DEVIATIONS 8.3).
        # It multiplies every per-call timeout by three; see `generation_timeout`.
        max_retries=2,
    )


def generation_model() -> str:
    """Sonnet-class model for generation and rewrite (§3)."""
    return get_settings().anthropic_model_generate or "claude-sonnet-5"


def fast_model() -> str:
    """Haiku-class model for tagging, moderation and question generation (§3)."""
    return get_settings().anthropic_model_fast or "claude-haiku-4-5"
