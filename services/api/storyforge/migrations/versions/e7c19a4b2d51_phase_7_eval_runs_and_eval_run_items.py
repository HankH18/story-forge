"""phase 7 eval_runs and eval_run_items

Revision ID: e7c19a4b2d51
Revises: c4f1a2d7be03
Create Date: 2026-07-29 21:40:00.000000

§8 specifies both tables and `models.py` says in its own docstring that they
"arrive with Phase 7" — an empty table passing a phase gate is a false green, so
they were deliberately not created until something wrote to them.
`storyforge.eval run` (§14) is that something (DEVIATIONS 7.40).

Two departures from §8's one-line sketch, both deliberate:

- `eval_run_items.story_id` is NULLABLE with `ON DELETE SET NULL`. §14 wants a
  metric row for every story the matrix *attempted*, and the attempts that
  produce no story row are the informative ones: a generation that raised
  `APITimeoutError` has a latency, an outcome and a cost, and no text. A NOT NULL
  FK would force the harness to either fabricate a story row for a failure or
  drop the measurement.
- `eval_runs.status` gets a CHECK. Unlike `stories.status` (which predates this
  migration and has no constraint) this column has exactly one writer and a
  closed set of values, and the whole point of the `running` state is that a run
  killed mid-flight leaves a row saying so — a typo'd status would quietly make
  a dead run look finished.

`ON DELETE CASCADE` from `eval_runs` to its items: an eval run is one artifact.
Deleting the run and leaving its 180 metric rows behind would be the same
orphaning bug 7.20 dealt with in `kids`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e7c19a4b2d51'
down_revision: Union[str, Sequence[str], None] = 'c4f1a2d7be03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="running",
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'budget_stopped', 'failed')",
            name="ck_eval_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "eval_run_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["eval_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_run_items_run_id", "eval_run_items", ["run_id"])
    op.create_index("ix_eval_run_items_story_id", "eval_run_items", ["story_id"])
    op.create_index("ix_eval_items_run", "eval_run_items", ["run_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_eval_items_run", table_name="eval_run_items")
    op.drop_index("ix_eval_run_items_story_id", table_name="eval_run_items")
    op.drop_index("ix_eval_run_items_run_id", table_name="eval_run_items")
    op.drop_table("eval_run_items")
    op.drop_table("eval_runs")
