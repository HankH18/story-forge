"""ORM models (§8).

Phase 2 scope only. The story/reading/eval tables in §8 land in the phases that
actually exercise them — an empty table that passes a phase gate is a false
green.

    stages ──< sight_words
       │
       └──< allowed_words          (precompute output, §5.4)

    gpcs                            (mirror of the decoder's gpcs.csv)

`gpcs` is seeded by importing the decoder's own loader rather than re-parsing the
CSV, so the database and the validator cannot disagree about the inventory.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
