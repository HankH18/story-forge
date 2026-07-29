"""Token accounting and cost estimation for the eval harness (§14).

Prices are per million tokens, USD, and are DATA — they change. §14 reports token
cost per node, so a wrong constant here silently corrupts every eval report.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

# Cache economics (same multipliers for every model):
#   read  ~0.1x  base input rate
#   write ~1.25x base input rate for the default 5-minute TTL
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float


# Sonnet 5 carries introductory pricing through 2026-08-31 ($2/$10), reverting to
# $3/$15. Encoded rather than hard-coded so eval reports stay honest across the
# cutover instead of silently under-reporting cost from September onward.
_SONNET_INTRO_ENDS = date(2026, 8, 31)

PRICES: dict[str, ModelPrice] = {
    "claude-sonnet-5": ModelPrice(input_per_mtok=3.00, output_per_mtok=15.00),
    "claude-haiku-4-5": ModelPrice(input_per_mtok=1.00, output_per_mtok=5.00),
    "claude-opus-4-8": ModelPrice(input_per_mtok=5.00, output_per_mtok=25.00),
}

_SONNET_INTRO = ModelPrice(input_per_mtok=2.00, output_per_mtok=10.00)


def price_for(model: str, on: date) -> ModelPrice:
    """Price sheet for a model on a given date.

    `on` is an explicit parameter rather than `date.today()` so the eval harness
    stays a pure function of its inputs — a report can be regenerated later and
    produce the same numbers.
    """
    if model == "claude-sonnet-5" and on <= _SONNET_INTRO_ENDS:
        return _SONNET_INTRO
    if model not in PRICES:
        raise KeyError(f"no price sheet for {model!r} — add it to storyforge.llm.pricing")
    return PRICES[model]


class Usage(BaseModel):
    """Token counts from one API call, plus the derived cost."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_input_tokens(self) -> int:
        """§ prompt-caching: `input_tokens` is the UNCACHED remainder only.

        Reading `input_tokens` alone under-reports prompt size on any cached
        request — the real total includes both cache fields.
        """
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def cost_usd(self, on: date) -> float:
        p = price_for(self.model, on)
        rate = p.input_per_mtok / 1_000_000
        return (
            self.input_tokens * rate
            + self.cache_read_input_tokens * rate * CACHE_READ_MULTIPLIER
            + self.cache_creation_input_tokens * rate * CACHE_WRITE_MULTIPLIER
            + self.output_tokens * (p.output_per_mtok / 1_000_000)
        )


def usage_from_response(model: str, raw) -> Usage:
    """Build a Usage from an SDK response's `.usage`."""
    return Usage(
        model=model,
        input_tokens=raw.input_tokens or 0,
        output_tokens=raw.output_tokens or 0,
        cache_creation_input_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
    )
