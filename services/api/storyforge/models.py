"""ORM models (§8).

Tables land in the phase that actually exercises them — an empty table passing a
phase gate is a false green. `eval_runs`/`eval_run_items` (§8) arrive with Phase 7,
written by `storyforge.eval run` (DEVIATIONS 7.40).

    users ──< kids
    stages ──< sight_words
       │   └──< allowed_words       (precompute output, §5.4)
       │   └──< kids ──< readings ──< answers >── questions
       │                  └──< gpc_stats >── gpcs
       └──< stories ──< story_segments
    themes ──┘   └──< story_word_bank

    eval_runs ──< eval_run_items >── stories   (§14, story_id nullable)

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

#: §13's "display-name only", as a Postgres CHECK. Kept beside the model so the
#: constraint and the migration that installs it cannot drift; `kids.py` mirrors
#: it in Python so a bad name is a 422 with a reason rather than a 500 from the
#: database. Deliberately a denylist of ASCII rather than an allowlist of
#: letters: `[[:alpha:]]` resolves against the server's locale, so an allowlist
#: would accept `Sofía` on one database and reject it on another.
DISPLAY_NAME_IS_A_FIRST_NAME = r"""
    display_name = btrim(display_name)
    AND char_length(display_name) BETWEEN 1 AND 40
    AND display_name !~ '[[:cntrl:]]'
    AND display_name !~ '[[:digit:]]'
    AND display_name !~ $nm$[!"#$%&()*+,/:;<=>?@^_`{|}~\\\[\]]$nm$
    AND display_name !~ '[[:space:]][[:space:]]'
    AND display_name !~ '[[:space:]][^[:space:]]+[[:space:]]'
"""


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

    A fourth value, `eval`, is written by the §14 harness (DEVIATIONS 7.41).
    Neither §8 value fits a story generated to be *measured*: `warm` would push
    100+ unenriched rows into the servable pool, and `rejected` would be a lie
    about one that passed. It is unreachable by construction rather than by
    convention — `api/stories.py::SERVABLE_STATUSES` is an allowlist that both
    serving paths filter on, and it does not name `eval`.
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

    Exactly two TYPES (§11.3): `tap_word` ("tap the word that says /sh/ip") and
    `mcq2` (two-choice story question) — not exactly two rows. A story may carry
    two `tap_word` rows, because §8's ≥10-observation floor is unreachable inside
    §8's own five stories on one phonics question (DEVIATIONS 11.67). `idx`
    orders them; nothing here is unique per (story, qtype).

    Every OPTION of either type is validated decodable, as is the whole of an
    `mcq2` prompt — the child must read it to answer. The `tap_word` prompt is
    the one child-facing string that is not: it is a fixed human-authored
    instruction, spoken aloud on mount, and §11.3 attaches "validated decodable"
    to options alone (DEVIATIONS 11.60).
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


class User(Base):
    """A Clerk-authenticated adult (§8, §13). The only account in the system.

    Kids deliberately have none — §13's COPPA posture is that a child has no
    auth identity at all — so this table is the whole answer to "who is calling"
    and `kids.parent_id` is the whole answer to "what may they reach".

    `clerk_id` is the identity, not `email`. Clerk's `sub` (`user_…`) is stable
    across an address change, and a parent who updates their email in Clerk must
    not become a different user with no children. The email is stored for the
    §11 dashboard only and is nullable, because a JWT template that does not
    include the claim is a normal Clerk configuration, not an error.

    `role` mirrors `public_metadata.role` (§13) and is refreshed from the token
    on every request — the token is the source of truth, this column is a cache
    for joins and for the admin list. The CHECK constraint is not decoration: it
    is the last thing standing between a typo'd migration or a direct psql
    session and a value that the role comparison would not recognise. A role
    outside the enum can only ever be read back as the least privilege
    (`auth.load_or_create_user`), and now cannot be written at all.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('parent', 'admin')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Clerk's `sub`. Unique and indexed: it is the lookup on every single
    # authenticated request, and the uniqueness is what makes the
    # load-or-create upsert race-free.
    clerk_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="parent", server_default="parent")
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Kid(Base):
    """A reader (§8, §13).

    **The whole COPPA posture is in the column list.** §13 gives kids no account,
    no auth identity, no email, no date of birth, no voice and no free-text
    input — so this table holds a first name, a stage, and nothing else. There is
    deliberately no column a future feature could quietly fill with PII; adding
    one is a §13 decision, not a schema tweak.

    `parent_id` became `fk users` in Phase 6, which is what DEVIATIONS 5.1
    reserved the column for. It stays **nullable** rather than becoming NOT NULL:
    rows created before auth existed have no owner, and the honest reading of
    "owned by nobody" is "visible to nobody" — `auth.owned_kid` filters on
    `parent_id = :caller`, so a NULL never matches any parent and the row is 404
    everywhere. Backfilling those to some arbitrary parent would be worse: it
    would hand one family another family's children.

    `ondelete="CASCADE"`: deleting the parent account deletes the children, and
    `readings`/`answers`/`gpc_stats` already cascade from here. §13 promises a
    minimal record of a child; a delete that left orphaned rows behind would
    make that promise false at exactly the moment it matters.

    `display_name` carries §13 as a CHECK, not only as a Pydantic validator.
    This column is rendered full-screen to a child by the theme picker and has
    never been through any of §13's three layers, so "a first name" has to be
    true of the column rather than of one endpoint — this database already held
    `kid@example.com`, written by some path that was not `POST /api/kids`. No
    digits (phone numbers, house numbers), no punctuation beyond apostrophe,
    hyphen and full stop, at most two words, no control characters, no leading,
    trailing or repeated whitespace. Non-ASCII passes: Sofía and Zoë are first
    names, and `[[:alpha:]]` is locale-dependent, so an allowlist here would
    mean different things on different servers. The API's validator narrows it
    further with Unicode-aware `str.isalpha()`.
    """

    __tablename__ = "kids"
    __table_args__ = (
        Index("ix_kids_parent", "parent_id"),
        CheckConstraint(DISPLAY_NAME_IS_A_FIRST_NAME, name="ck_kids_display_name_is_a_first_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
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


class EvalRun(Base):
    """One `python -m storyforge.eval run` invocation (§8, §14).

    `config` is everything needed to reproduce the run — the stage/theme matrix,
    `n`, the model ids, the pricing date, the gate, the rewrite budget, the spend
    cap, the git commit. §14 calls the CLI canonical and the admin portal a view
    of these tables, so a report that cannot be regenerated from `config` is a
    report nobody can compare against.

    The pricing date is in `config` and not derived at read time on purpose:
    `pricing.price_for` takes an explicit `on` because Sonnet 5's introductory
    rate expires 2026-08-31, so a cost recomputed later against `date.today()`
    would silently disagree with the number that was printed.

    `status` is `running` while the run is in flight and one of `completed` |
    `budget_stopped` | `failed` afterwards. It is written as `running` first so a
    run killed mid-flight leaves a row that says so rather than no row at all.
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running")


class EvalRunItem(Base):
    """One attempted story in a run — §14's per-story metric row.

    `story_id` is NULLABLE, departing from §8's bare `story_id fk` (DEVIATIONS
    7.40). §14 wants a metric row per story the matrix *attempted*, and the
    attempts with no story are the informative ones: a generation that raised
    `APITimeoutError` has a latency, an outcome and a cost, and no text. Writing
    those with a NULL story is what keeps "the harness tried 180 cells and 178
    produced a story" answerable from the database. `ON DELETE SET NULL` for the
    same reason in the other direction — the measurement outlives the artifact.
    """

    __tablename__ = "eval_run_items"
    __table_args__ = (Index("ix_eval_items_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
