"""ORM models (§8).

Tables land in the phase that actually exercises them — an empty table passing a
phase gate is a false green. `eval_runs`/`eval_run_items` (§8) arrive with Phase 7.

    stages ──< sight_words
       │   └──< allowed_words       (precompute output, §5.4)
       │   └──< kids ──< readings ──< answers >── questions
       │                  └──< gpc_stats >── gpcs
       └──< stories ──< story_segments
    themes ──┘   └──< story_word_bank

    gpcs                            (mirror of the decoder's gpcs.csv)

`gpcs` is seeded by importing the decoder's own loader rather than re-parsing the
CSV, so the database and the validator cannot disagree about the inventory.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from storyforge.db import Base


class Stage(Base):
    __tablename__ = "stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    # §6: story length is stage-scaled.
    target_len_min: Mapped[int] = mapped_column(Integer, default=30)
    target_len_max: Mapped[int] = mapped_column(Integer, default=60)
    # §5.4 drops words over this many syllables from the allowlist.
    max_syllables: Mapped[int] = mapped_column(Integer, default=2)

    sight_words: Mapped[list[SightWord]] = relationship(back_populates="stage")


class Gpc(Base):
    """Mirror of one row of the decoder's gpcs.csv.

    `gpc_id` is the decoder's stable identifier (e.g. "sh|SH"), so reports and
    database rows join on exactly the same key the validator emits.
    """

    __tablename__ = "gpcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gpc_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    grapheme: Mapped[str] = mapped_column(String(8), index=True)
    phonemes: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    position: Mapped[str] = mapped_column(String(8))
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    schwa_ok: Mapped[bool] = mapped_column(default=False)
    example: Mapped[str] = mapped_column(String(32), default="")


class SightWord(Base):
    """Per-stage high-frequency non-decodable words. Cumulative (§4)."""

    __tablename__ = "sight_words"
    __table_args__ = (UniqueConstraint("stage_id", "word", name="uq_sight_stage_word"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    word: Mapped[str] = mapped_column(String(64), index=True)

    stage: Mapped[Stage] = relationship(back_populates="sight_words")


class AllowedWord(Base):
    """Precompute output (§5.4) — every word a reader at this stage may legally see.

    Rebuilt atomically: precompute writes a temp table and swaps it in one
    transaction, so a reader never observes a partial allowlist. A truncated
    allowlist would silently produce duller stories while every metric still
    reported success.
    """

    __tablename__ = "allowed_words"
    __table_args__ = (
        UniqueConstraint("stage_id", "word", name="uq_allowed_stage_word"),
        # The generation prompt takes the top N by frequency for a stage (§6),
        # so that ordering is the hot query.
        Index("ix_allowed_stage_freq", "stage_id", "freq_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    word: Mapped[str] = mapped_column(String(64), index=True)
    syllables: Mapped[int] = mapped_column(Integer)
    # 1 = most frequent. Rank within the vendored frequency list.
    freq_rank: Mapped[int] = mapped_column(Integer)


class Theme(Base):
    """Kid-facing theme (§8).

    `emoji` is a pre-asset fallback only. Style guide §07 permits emoji in
    exactly one place — this column — until real cover art exists.
    """

    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="")


class StoryWord(Base):
    """Theme vocabulary a reader cannot yet decode (§5.5).

    Deliberately NOT required to be decodable — that is the point. Generation
    picks at most three per story; each renders as a picture chip with
    tap-to-hear, is excluded from the decodability denominator, and is reported
    separately (§4).
    """

    __tablename__ = "story_word_bank"
    __table_args__ = (UniqueConstraint("theme_id", "word", name="uq_storyword_theme_word"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    word: Mapped[str] = mapped_column(String(64))
    gloss: Mapped[str] = mapped_column(Text, default="")
    # Earliest stage this word may be offered at.
    min_stage: Mapped[int] = mapped_column(Integer, default=1)


class Story(Base):
    """A generated story (§8).

    `status` follows §7: `warm` = pre-generated and unread, `complete` = served
    to a kid at least once, `rejected` = failed the gate or moderation and is
    kept only as an eval artifact — never served.
    """

    __tablename__ = "stories"
    __table_args__ = (Index("ix_stories_pool", "stage_id", "theme_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="warm")
    title: Mapped[str] = mapped_column(String(128), default="")
    decodability: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    # The distinct story words actually used — drives the picture chips (§4).
    story_words: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # §14 metrics: rewrite depth, per-attempt usage, moderation verdict, cost.
    gen_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Least-recently-served wins when the pool is drawn from (§7).
    last_served_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    segments: Mapped[list[StorySegment]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="StorySegment.idx"
    )


class StorySegment(Base):
    """One screen of a story (§8, §10).

    Branching columns (`parent_segment_id`, `choice_label`) exist now even though
    Phase 3 generates a linear spine — §295's cut line drops branching but keeps
    the schema, so adding them later would be a migration for no reason.
    `asset_id` is a plain nullable int rather than a FK: the assets table lands
    in Phase 4 (docs/DEVIATIONS.md 3.9).
    """

    __tablename__ = "story_segments"
    __table_args__ = (UniqueConstraint("story_id", "idx", name="uq_segment_story_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    parent_segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_segments.id"), nullable=True
    )
    choice_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    scene_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    story: Mapped[Story] = relationship(back_populates="segments")


class Asset(Base):
    """Curated illustration (§12).

    v1 is a hand-curated pool, ~8-12 images per theme, tagged from a CLOSED
    vocabulary. The scene-tagging node picks tags from an enum in the JSON
    schema, so the model cannot invent a tag that matches nothing.
    `alt_text` is mandatory (style guide §06).
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="segment")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    url: Mapped[str] = mapped_column(Text)
    alt_text: Mapped[str] = mapped_column(Text)


class Question(Base):
    """Comprehension question (§8, §11).

    Exactly two types (§11.3): `tap_word` ("tap the word that says /sh/ip") and
    `mcq2` (two-choice story question). Both render as decodable text, so the
    prompt and options are validated like any other child-facing string.
    """

    __tablename__ = "questions"
    __table_args__ = (Index("ix_questions_story", "story_id", "idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_segments.id", ondelete="CASCADE"), nullable=True
    )
    idx: Mapped[int] = mapped_column(Integer, default=0)
    qtype: Mapped[str] = mapped_column(String(16))
    prompt: Mapped[str] = mapped_column(Text)
    # tap_word: {"options": [...]}; mcq2: {"options": [a, b]}
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    # {"correct": "shed", "index": 0} — graded server-side at
    # POST /api/readings/{id}/answers and NEVER shipped to a client. §8's
    # promotion rule is only meaningful while these counts are unforgeable.
    answer: Mapped[dict] = mapped_column(JSONB, default=dict)


class Kid(Base):
    """A reader (§8, §13).

    **The whole COPPA posture is in the column list.** §13 gives kids no account,
    no auth identity, no email, no date of birth, no voice and no free-text
    input — so this table holds a first name, a stage, and nothing else. There is
    deliberately no column a future feature could quietly fill with PII; adding
    one is a §13 decision, not a schema tweak.

    `parent_id` is a plain nullable int rather than `fk users`, on the DEVIATIONS
    3.9 precedent: `users` lands with Clerk in Phase 6, and a FK to a table that
    does not exist cannot be created. Reserving the column now means Phase 6 adds
    a constraint rather than a column — and the ownership shape §9 requires
    ("parent → own kids only") is already in the schema.
    """

    __tablename__ = "kids"
    __table_args__ = (Index("ix_kids_parent", "parent_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # First name only (§13). Bounded here as well as in the API so a direct
    # writer cannot store an essay.
    display_name: Mapped[str] = mapped_column(String(40))
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Reading(Base):
    """One kid's pass through one story (§8).

    `completed_at` is the idempotency latch for the whole mastery pipeline.
    `POST /api/readings/{id}/complete` sets it with a single conditional UPDATE,
    so exactly one caller can ever observe the transition and `gpc_stats` cannot
    be incremented twice for the same reading (§8: promotion must be honest).

    `path` records branch choices for the dashboard (§10: "Maya chose the cave
    path") as `{"choices": [{"segment_id": n, "choice": "...", "at": iso}]}`.
    Branching is schema-only until §10's generator lands, so the list is normally
    empty — but the endpoint and the storage shape are real.

    `stage_id` is additive to §8 and is the kid's stage **when the reading
    started**, not the story's. §8's promotion rule counts "stories completed at
    current stage"; the §7 fallback ladder deliberately serves *lower*-stage
    stories when a pool is empty, so `stories.stage_id` would undercount, and
    after a promotion there would be no way to tell which readings belonged to
    the stage just left. Without this column the rule is not computable.
    """

    __tablename__ = "readings"
    __table_args__ = (
        # The two hot queries: "this kid's recent readings" (dashboard) and
        # "how many has this kid completed at this stage" (promotion).
        Index("ix_readings_kid_started", "kid_id", "started_at"),
        Index("ix_readings_kid_stage_done", "kid_id", "stage_id", "completed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kid_id: Mapped[int] = mapped_column(ForeignKey("kids.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), index=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"))
    path: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Answer(Base):
    """One graded response (§8).

    §8 stores the *verdict*, not the response. That is both the spec's shape and
    the §13 posture: the minimum record that makes mastery computable is a
    boolean, so nothing more about the child is kept.

    The unique constraint is the anti-forgery guard. Without it a client could
    resubmit a question until it guessed right and inflate `gpc_stats` — the very
    signal §8 promotes on. One row per (reading, question), first answer wins.
    """

    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("reading_id", "question_id", name="uq_answer_reading_question"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reading_id: Mapped[int] = mapped_column(
        ForeignKey("readings.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    correct: Mapped[bool] = mapped_column()
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GpcStat(Base):
    """Per-kid, per-GPC evidence (§8) — the entire mastery model.

    Two integers per grapheme-phoneme correspondence. No latent state, no
    inferred knowledge parameter, nothing a parent cannot read off the row: §8 is
    explicit that this is "interpretable by design — no BKT". The dashboard's
    per-GPC accuracy table is `n_correct / n_seen`, and promotion is a comparison
    against thresholds that are themselves displayed (`storyforge.mastery`).

    `gpc_id` references `gpcs.gpc_id` — the decoder's own stable identifier
    (`"sh|SH"`) — rather than the surrogate `gpcs.id`. `validate_word().alignment`
    emits exactly these strings, so the counters join to the validator on the key
    it actually produces and a row is legible without a join. Same argument as
    seeding `gpcs` from `load_gpcs()`: one identifier, not two that can drift.
    """

    __tablename__ = "gpc_stats"
    __table_args__ = (
        CheckConstraint("n_correct <= n_seen", name="ck_gpc_stats_correct_le_seen"),
        CheckConstraint("n_seen >= 0 AND n_correct >= 0", name="ck_gpc_stats_non_negative"),
        Index("ix_gpc_stats_gpc", "gpc_id"),
    )

    kid_id: Mapped[int] = mapped_column(
        ForeignKey("kids.id", ondelete="CASCADE"), primary_key=True
    )
    gpc_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("gpcs.gpc_id", ondelete="CASCADE"), primary_key=True
    )
    # A GPC *instance* (§8): a word aligning to `d|D` twice counts twice.
    n_seen: Mapped[int] = mapped_column(Integer, default=0)
    n_correct: Mapped[int] = mapped_column(Integer, default=0)
