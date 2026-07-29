"""Langfuse tracing (§15) — one trace per pipeline run, one span per node.

    story_pipeline                     <- trace (the whole run)
      ├─ GENERATING   generation       input=prompt  output=draft  tokens+cost
      ├─ VALIDATING   span             input=draft   output=report
      ├─ REWRITING    generation       input=violations  output=draft  tokens+cost
      ├─ VALIDATING   span
      └─ MODERATING   generation       input=story  output=verdict  tokens+cost

**Tracing is observability, never product behaviour.** A child waiting on a story
does not care that a telemetry backend is down, so every call into this module is
wrapped: if Langfuse is unconfigured, unreachable, or throws, the caller gets an
inert handle and the pipeline runs exactly as it did before Phase 3.5. That is
enforced by tests/test_obs.py, which runs the pipeline against a client that
raises from every method.

Two failure modes this module exists to avoid:

1. **Hard dependency.** Phases 0-2 shipped with no Langfuse credentials, and the
   repo must still work for anyone who clones it without keys. Absent keys is a
   supported configuration (a no-op), not an error.
2. **Silently lost traces.** The SDK batches exports on a background thread, so a
   short-lived CLI (`make warm`, `make eval-smoke`) can exit with spans still
   queued and appear to work while recording nothing. `flush()` is registered
   with `atexit` the moment a client is built, and CLIs call it explicitly.
"""

from __future__ import annotations

import atexit
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from storyforge.config import get_settings
from storyforge.llm.pricing import Usage, price_for

logger = logging.getLogger("storyforge.obs")

# §7 node names, used verbatim as span names so a trace reads like the state machine.
TRACE_NAME = "story_pipeline"

# Sentinel: "not resolved yet". `None` means "resolved, and tracing is off".
_UNSET: Any = object()
_client: Any = _UNSET


# --- client lifecycle --------------------------------------------------------
def _resolve_client() -> Any:
    """Build the Langfuse client once, or decide (once) that tracing is off."""
    global _client
    if _client is not _UNSET:
        return _client

    _client = None  # pessimistic: any failure below leaves tracing disabled
    settings = get_settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info("Langfuse keys absent — tracing disabled (pipeline unaffected).")
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or None,
        )
    except Exception:  # noqa: BLE001 — tracing must never break generation
        logger.warning("Langfuse init failed — continuing untraced.", exc_info=True)
        return None

    _client = client
    # Exports are batched on a background thread. Without this, a CLI that
    # finishes in two seconds exits with its spans still in the queue.
    atexit.register(flush)
    logger.info("Langfuse tracing enabled (%s).", settings.langfuse_host or "default host")
    return client


def enabled() -> bool:
    """True when spans will actually be recorded."""
    return _resolve_client() is not None


def reset() -> None:
    """Forget the resolved client. For tests and for config changes at runtime."""
    global _client
    _client = _UNSET


def flush() -> None:
    """Block until queued spans have been exported.

    Short-lived processes MUST call this (or exit normally, so the `atexit` hook
    fires) or traces are silently dropped. Never called on the request path — the
    server relies on the SDK's background batching instead.
    """
    client = _client
    if client is _UNSET or client is None:
        return
    try:
        client.flush()
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse flush failed — traces may be incomplete.", exc_info=True)


# --- token / cost mapping ----------------------------------------------------
def usage_details(usage: Usage) -> dict[str, int]:
    """§15: token counts on the span, in Langfuse's `usage_details` shape.

    Cache reads and writes are reported as their own keys rather than folded into
    `input`, because they are priced differently (0.1x / 1.25x) and the whole
    point of the split prompt (§6) is to make that saving visible.
    """
    return {
        "input": usage.input_tokens,
        "output": usage.output_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "total": usage.total_input_tokens + usage.output_tokens,
    }


def cost_details(usage: Usage, on: date) -> dict[str, float]:
    """§15: cost on the span. `total` is `Usage.cost_usd` — the same number the
    eval harness reports — and the input share is derived from it by subtraction
    so the two can never drift apart."""
    total = usage.cost_usd(on)
    output = usage.output_tokens * price_for(usage.model, on).output_per_mtok / 1_000_000
    return {
        "input": round(total - output, 8),
        "output": round(output, 8),
        "total": round(total, 8),
    }


# --- span handle -------------------------------------------------------------
class Span:
    """A node handle. Every method swallows its own failures, by design.

    When tracing is off (or broke), `_raw` is None and every method is a no-op
    that returns the same types as the live path.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: Any = None) -> None:
        self._raw = raw

    @property
    def trace_id(self) -> str | None:
        """The id to persist on `stories.langfuse_trace_id` (§15). None when untraced."""
        if self._raw is None:
            return None
        try:
            return self._raw.trace_id
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse: trace id unavailable.", exc_info=True)
            return None

    def update(
        self,
        *,
        usage: Usage | None = None,
        on: date | None = None,
        **fields: Any,
    ) -> None:
        """Attach output/metadata, and — given a `Usage` — token counts and cost."""
        if self._raw is None:
            return
        try:
            if usage is not None:
                fields.setdefault("model", usage.model)
                fields["usage_details"] = usage_details(usage)
                try:
                    fields["cost_details"] = cost_details(usage, on or date.today())
                except Exception:  # noqa: BLE001 — an unpriced model loses cost, not the span
                    logger.debug("Langfuse: cost unavailable for %s.", usage.model, exc_info=True)
            self._raw.update(**fields)
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse: span update failed.", exc_info=True)


_NULL = Span()


@contextmanager
def observation(name: str, *, as_type: str = "span", **kwargs: Any) -> Iterator[Span]:
    """Open a Langfuse observation, nested under whatever is already current.

    Yields a `Span` in every case — including "Langfuse is down" — so callers
    never branch on whether tracing is on. Exceptions raised by the *body*
    propagate untouched (and are recorded on the span); exceptions raised by
    Langfuse itself are logged and dropped.
    """
    client = _resolve_client()
    if client is None:
        yield _NULL
        return

    try:
        cm = client.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        raw = cm.__enter__()
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse: span %r failed to start — continuing untraced.", name)
        logger.debug("Langfuse start failure detail:", exc_info=True)
        yield _NULL
        return

    try:
        yield Span(raw)
    except BaseException as exc:
        _close(cm, exc)
        raise
    _close(cm, None)


def span(name: str, **kwargs: Any):
    """A non-LLM node (VALIDATING, and the run as a whole)."""
    return observation(name, as_type="span", **kwargs)


def generation(name: str, **kwargs: Any):
    """An LLM node. Only generation-type observations carry tokens and cost."""
    return observation(name, as_type="generation", **kwargs)


def _close(cm: Any, exc: BaseException | None) -> None:
    try:
        if exc is None:
            cm.__exit__(None, None, None)
        else:
            cm.__exit__(type(exc), exc, exc.__traceback__)
    except Exception:  # noqa: BLE001
        logger.debug("Langfuse: span close failed.", exc_info=True)
