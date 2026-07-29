"""§15 observability — and the one property that matters: tracing is never load-bearing.

The pipeline is run here against three Langfuse configurations:

    absent      no credentials (Phases 0-2, and anyone who clones the repo)
    hostile     a client that raises from every single method
    recording   a fake that captures what would have been sent

All three must produce the same story. Only the third may produce a trace id.
The fake client mirrors the real v4 surface used in `storyforge.obs`
(`start_as_current_observation` as a context manager, `.trace_id`, `.update`,
`.flush`), so a signature change in the SDK shows up as a failure here.
"""

from __future__ import annotations

from datetime import date

import pytest

from storyforge import obs
from storyforge import pipeline as pl
from storyforge.llm.generate import StoryDraft, StorySegment
from storyforge.llm.moderate import ModerationResult
from storyforge.llm.pricing import Usage

GOOD = "Sam sat. The cat sat."  # 100% decodable at stage 1 with "the" as a sight word
BAD = "Sam ran. Pat ran."  # "ran" is not yet taught at stage 1 -> forces a rewrite

GEN_USAGE = Usage(
    model="claude-sonnet-5",
    input_tokens=120,
    output_tokens=60,
    cache_creation_input_tokens=800,
    cache_read_input_tokens=400,
)
MOD_USAGE = Usage(model="claude-haiku-4-5", input_tokens=90, output_tokens=12)


# --- fakes -------------------------------------------------------------------
class _RecordingSpan:
    def __init__(self, log: list, name: str, kwargs: dict):
        self.trace_id = "trace-abc123"
        self.record = {"name": name, "as_type": kwargs.get("as_type"), **kwargs}
        log.append(self.record)

    def update(self, **fields):
        self.record.setdefault("updates", []).append(fields)


class _RecordingCM:
    def __init__(self, log: list, name: str, kwargs: dict):
        self._span = _RecordingSpan(log, name, kwargs)

    def __enter__(self):
        return self._span

    def __exit__(self, *exc):
        self._span.record["closed"] = True
        return False


class RecordingClient:
    """Captures spans instead of shipping them."""

    def __init__(self):
        self.spans: list[dict] = []
        self.flushed = 0

    def start_as_current_observation(self, *, name, **kwargs):
        return _RecordingCM(self.spans, name, kwargs)

    def flush(self):
        self.flushed += 1

    def names(self) -> list[str]:
        return [s["name"] for s in self.spans]

    def span(self, name: str) -> dict:
        return next(s for s in self.spans if s["name"] == name)


class HostileClient:
    """Langfuse having a very bad day: every entry point raises."""

    def start_as_current_observation(self, **kwargs):
        raise RuntimeError("langfuse is down")

    def flush(self):
        raise RuntimeError("langfuse is down")


class _HostileSpan:
    @property
    def trace_id(self):
        raise RuntimeError("no trace id for you")

    def update(self, **fields):
        raise RuntimeError("update exploded")


class _HalfHostileCM:
    def __enter__(self):
        return _HostileSpan()

    def __exit__(self, *exc):
        raise RuntimeError("close exploded")


class HalfHostileClient:
    """Worse than dead: the span opens, then every method on it raises."""

    def start_as_current_observation(self, **kwargs):
        return _HalfHostileCM()

    def flush(self):
        raise RuntimeError("flush exploded")


# --- pipeline stubs ----------------------------------------------------------
def _draft(body: str) -> StoryDraft:
    return StoryDraft(
        title="Sam And The Cat",
        segments=[
            StorySegment(text=part.strip() + ".") for part in body.split(".") if part.strip()
        ],
        story_words_used=[],
    )


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Real validator, real gate, fake API calls. Returns the draft-body queue so
    a test can make the first attempt fail and force a REWRITING span."""
    vocab = pl.Vocabulary(
        stage_id=1,
        stage_name="Stage 1",
        target_min=10,
        target_max=40,
        allowed_words=["sam", "sat", "cat", "mat"],
        sight_words=["the"],
        story_words={},
    )
    bodies = [GOOD]

    async def fake_load(session, stage_id, theme_slug):
        return vocab

    async def fake_call(*_args, **_kwargs):
        body = bodies.pop(0) if len(bodies) > 1 else bodies[0]
        return _draft(body), GEN_USAGE

    async def fake_moderate(body):
        return ModerationResult(
            approved=True, reason="storybook peril only", layer="model", usage=MOD_USAGE
        )

    monkeypatch.setattr(pl, "load_vocabulary", fake_load)
    monkeypatch.setattr(pl, "_call", fake_call)
    monkeypatch.setattr(pl, "moderate", fake_moderate)
    return bodies


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch):
    """Never let a test build a real client and ship spans to the live project."""
    monkeypatch.setattr(obs, "_client", None)


def _use(monkeypatch, client):
    monkeypatch.setattr(obs, "_client", client)


# --- 1. the no-op path: no credentials, no Langfuse, same story --------------
async def test_pipeline_runs_untraced_with_no_credentials(stub_pipeline, monkeypatch):
    """Phases 0-2 had no Langfuse keys. Absent keys is a supported configuration."""

    class NoKeys:
        langfuse_public_key = None
        langfuse_secret_key = None
        langfuse_host = None

    monkeypatch.setattr(obs, "_client", obs._UNSET)
    monkeypatch.setattr(obs, "get_settings", lambda: NoKeys())

    result = await pl.run_pipeline(None, 1, "dragons")

    assert obs.enabled() is False
    assert result.passed is True
    assert result.final_text == GOOD
    assert result.langfuse_trace_id is None


async def test_half_configured_credentials_do_not_enable_tracing(monkeypatch):
    """A public key with no secret must be off, not half-on."""

    class HalfKeys:
        langfuse_public_key = "pk-lf-1"
        langfuse_secret_key = None
        langfuse_host = "https://us.cloud.langfuse.com"

    monkeypatch.setattr(obs, "_client", obs._UNSET)
    monkeypatch.setattr(obs, "get_settings", lambda: HalfKeys())
    assert obs.enabled() is False


def test_flush_without_a_client_is_a_no_op(monkeypatch):
    monkeypatch.setattr(obs, "_client", obs._UNSET)
    obs.flush()  # must not construct a client, must not raise


# --- 2. hostile Langfuse: the story still ships ------------------------------
async def test_pipeline_survives_langfuse_refusing_to_start_spans(
    stub_pipeline, monkeypatch
):
    _use(monkeypatch, HostileClient())

    result = await pl.run_pipeline(None, 1, "dragons")

    assert result.passed is True
    assert result.final_text == GOOD
    assert result.final_report.decodability == 1.0
    assert result.langfuse_trace_id is None
    assert len(result.attempts) == 1


async def test_pipeline_survives_spans_that_explode_mid_flight(stub_pipeline, monkeypatch):
    """The nastier failure: the span opens, so the pipeline is inside a `with`
    block whose every method now raises — including `__exit__`."""
    _use(monkeypatch, HalfHostileClient())

    result = await pl.run_pipeline(None, 1, "dragons")

    assert result.passed is True
    assert result.final_text == GOOD
    assert result.langfuse_trace_id is None


def test_flush_swallows_a_failing_backend(monkeypatch):
    _use(monkeypatch, HostileClient())
    obs.flush()  # must not raise: a CLI must never die on the way out


async def test_pipeline_errors_still_propagate(stub_pipeline, monkeypatch):
    """Swallowing tracing failures must not swallow REAL ones."""
    _use(monkeypatch, RecordingClient())

    async def boom(*_a, **_k):
        raise ValueError("schema parse failed")

    monkeypatch.setattr(pl, "_call", boom)

    with pytest.raises(ValueError, match="schema parse failed"):
        await pl.run_pipeline(None, 1, "dragons")


# --- 3. what actually gets traced (§15) --------------------------------------
async def test_one_trace_per_run_with_a_span_per_node(stub_pipeline, monkeypatch):
    client = RecordingClient()
    _use(monkeypatch, client)

    result = await pl.run_pipeline(None, 1, "dragons")

    assert client.names() == [obs.TRACE_NAME, "GENERATING", "VALIDATING", "MODERATING"]
    assert all(s.get("closed") for s in client.spans)
    assert result.langfuse_trace_id == "trace-abc123"


async def test_a_failed_attempt_produces_a_rewriting_span(stub_pipeline, monkeypatch):
    """§7's REWRITING node is a distinct span, not a second GENERATING."""
    stub_pipeline.insert(0, BAD)  # first attempt fails the gate
    client = RecordingClient()
    _use(monkeypatch, client)

    result = await pl.run_pipeline(None, 1, "dragons")

    assert client.names() == [
        obs.TRACE_NAME,
        "GENERATING",
        "VALIDATING",
        "REWRITING",
        "VALIDATING",
        "MODERATING",
    ]
    assert result.rewrite_depth == 1
    # The failing VALIDATING span must say so — a silent WARNING-less span would
    # hide exactly the runs worth looking at.
    first_validate = client.spans[2]["updates"][0]
    assert first_validate["level"] == "WARNING"
    assert "ran" in first_validate["output"]["violations"]


async def test_llm_spans_carry_tokens_and_cost(stub_pipeline, monkeypatch):
    """§15: token counts and cost on the span, not just in the logs."""
    client = RecordingClient()
    _use(monkeypatch, client)

    await pl.run_pipeline(None, 1, "dragons")

    gen = client.span("GENERATING")
    assert gen["as_type"] == "generation"  # only generations carry usage in Langfuse
    assert gen["model"] == pl.generation_model()
    update = gen["updates"][0]
    assert update["usage_details"] == obs.usage_details(GEN_USAGE)
    assert update["cost_details"]["total"] == pytest.approx(GEN_USAGE.cost_usd(date.today()))

    mod = client.span("MODERATING")
    assert mod["updates"][0]["model"] == "claude-haiku-4-5"
    assert mod["updates"][0]["usage_details"]["output"] == 12


async def test_validating_span_is_not_a_generation(stub_pipeline, monkeypatch):
    """The validator makes no API call; typing it as a generation would invent
    a $0 model call in every cost breakdown."""
    client = RecordingClient()
    _use(monkeypatch, client)

    await pl.run_pipeline(None, 1, "dragons")

    val = client.span("VALIDATING")
    assert val["as_type"] == "span"
    assert val["updates"][0]["output"]["decodability"] == 1.0
    assert val["updates"][0]["output"]["passes_gate"] is True


async def test_trace_carries_the_run_summary(stub_pipeline, monkeypatch):
    client = RecordingClient()
    _use(monkeypatch, client)

    await pl.run_pipeline(None, 1, "dragons")

    root = client.span(obs.TRACE_NAME)
    assert root["input"]["stage_id"] == 1 and root["input"]["theme"] == "dragons"
    out = root["updates"][0]["output"]
    assert out["passed"] is True
    assert out["decodability"] == 1.0
    assert out["cost_usd"] > 0
    assert out["moderation"] == {"approved": True, "layer": "model"}


async def test_budget_exhausted_is_flagged_on_the_trace(stub_pipeline, monkeypatch):
    """§7 budget_exhausted: not an exception, so it must be visible some other way."""
    client = RecordingClient()
    _use(monkeypatch, client)

    async def always_bad(*_a, **_k):
        return _draft(BAD), GEN_USAGE

    monkeypatch.setattr(pl, "_call", always_bad)

    result = await pl.run_pipeline(None, 1, "dragons")

    assert result.passed is False
    root_update = client.span(obs.TRACE_NAME)["updates"][0]
    assert root_update["level"] == "WARNING"
    assert root_update["status_message"] == "budget_exhausted"
    assert client.names().count("REWRITING") == pl.MAX_REWRITES


# --- 4. token/cost mapping (pure) --------------------------------------------
def test_usage_details_report_cache_traffic_separately():
    """Folding cache reads into `input` would hide the §6 prompt-cache saving and
    overstate the token price by 10x on the cached half."""
    d = obs.usage_details(GEN_USAGE)
    assert d == {
        "input": 120,
        "output": 60,
        "cache_read_input_tokens": 400,
        "cache_creation_input_tokens": 800,
        "total": 1380,
    }
    assert d["total"] == GEN_USAGE.total_input_tokens + GEN_USAGE.output_tokens


def test_cost_details_split_agrees_with_the_eval_harness_number():
    on = date(2026, 7, 29)
    c = obs.cost_details(GEN_USAGE, on)
    assert c["total"] == pytest.approx(GEN_USAGE.cost_usd(on))
    assert c["input"] + c["output"] == pytest.approx(c["total"])
    assert c["output"] == pytest.approx(60 * 10.00 / 1_000_000)  # Sonnet 5 intro rate


def test_an_unpriced_model_loses_the_cost_not_the_span(monkeypatch):
    """A missing price sheet must degrade to "no cost on this span", never to a
    failed generation."""
    client = RecordingClient()
    _use(monkeypatch, client)
    with obs.generation("GENERATING") as s:
        s.update(output="x", usage=Usage(model="claude-from-the-future", input_tokens=5))
    update = client.span("GENERATING")["updates"][0]
    assert update["output"] == "x"
    assert "usage_details" in update
    assert "cost_details" not in update


# --- 5. the handle contract --------------------------------------------------
def test_null_span_has_the_same_shape_as_a_live_one(monkeypatch):
    """Callers must never branch on whether tracing is on."""
    monkeypatch.setattr(obs, "_client", None)
    with obs.span("VALIDATING", input={"a": 1}) as s:
        assert s.trace_id is None
        s.update(output={"b": 2}, usage=GEN_USAGE, level="WARNING")  # no-ops, no raise
