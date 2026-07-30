"""retire stage 1 and renumber the ladder to eleven stages

Revision ID: f3a91c6d0e24
Revises: e7c19a4b2d51
Create Date: 2026-07-30 12:00:00.000000

docs/DECISION-stage-ladder.md, authorised by the user; DEVIATIONS 12.1-12.8.
Old stage N becomes new stage N-1 for N >= 2; old stages 1 and 2 merge into new
stage 1. Stage inventories are CUMULATIVE, so the cumulative GPC, sight-word and
allowed-word set at new stage K is IDENTICAL to old stage K+1's — the mapping
moves labels, never content, and never the decodability gate (§0).

Why a renumber and not a rebuild: `kids.stage_id` is the serving state. A child
sitting at old stage 5 must land at new stage 4, not jump a level — mis-levelled
serving is the exact class of AUDIT-PHASE6 C1/C2. A child at old stage 1 lands
at new stage 1: their taught inventory is a subset of new stage 1's (the merge),
and there is no lower rung — the accepted, user-authorised cost of retiring the
stage. Stories and readings proved decodable/read at old stage N are judged by
the identical cumulative set at new stage N-1 (old stage-1 rows land at new
stage 1, a superset — the safe direction, STATE invariant 6).

What is renumbered, in dependency order: kids, stories, readings (plain
`stage_id` decrements), `gpcs.stage_id`, `sight_words.stage_id` (each word
appears at exactly one stage, so the merge cannot collide),
`story_word_bank.min_stage`, `allowed_words` (old stage-1 rows deleted — every
one recurs at old stage 2 because the table is cumulative — then the rest
decremented through a temp table so the `(stage_id, word)` unique constraint
never sees a transient collision; the result is byte-identical to what `make
precompute` regenerates under the new ladder), then the `stages` rows
themselves (contents shifted down, row 12 deleted last, after nothing
references it). The new stage rows are HARDCODED rather than read from
`data/seed/stages.csv`: a migration is a historical record and must keep
applying identically after the CSV moves again; `make seed` upserts the same
values afterwards.

One column is RECOMPUTED rather than renumbered: `stories.decodability` for
every story at the merged stage 1. Old stage-N rows (N >= 2) map to an
identical cumulative inventory, so their stored numbers are provably
unchanged; old stage-1 rows are now judged by a superset (old stage 2's GPCs
and sight words), which can only raise decodability — every word the merge
makes newly sight was a violation at old stage 1. The recompute runs the real
validator against the renumbered `sight_words` table, so the stored number
and the live serve-time gate cannot disagree about a story in the pool
(`tests/test_eval.py::test_the_gate_still_agrees_with_what_the_harness_
recorded` is the guard that caught this class). Idempotent: recomputing twice
yields the same values.

What is deliberately NOT renumbered (DEVIATIONS 12.8): `eval_runs` /
`eval_run_items` jsonb. Runs 2 and 114 hold `by_stage` aggregate slices for
both old stages 1 and 2, which collide at new stage 1, and per-stage stat
slices cannot be merged without re-running the aggregation — while renumbering
the items but not the aggregates would make one run's own record disagree with
itself. An eval run is a record: its `config` pins the git commit, which pins
the ladder it measured. Nothing joins those jsonb labels to `stages` by FK.
`gpc_stats` has no stage column (keyed kid x gpc) and follows the renumbered
`gpcs` rows automatically.

Idempotent by guard, not by luck: it runs only when `max(stages.id) = 12`. A
fresh database (no stages yet — `make seed` will write the eleven-row ladder)
and an already-renumbered one (max 11) are both no-ops. Any other shape is a
database this migration does not understand, and it refuses rather than
guesses — the c4f1a2d7be03 rule.

The downgrade is a refusal for the same reason its neighbour's is a no-op:
re-inserting a retired stage and re-raising every child's number is not an
undo, it is a new wrong state, and the merge of old stages 1 and 2 cannot be
un-merged from the data alone.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a91c6d0e24'
down_revision: Union[str, Sequence[str], None] = 'e7c19a4b2d51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The eleven-stage ladder, as `data/seed/stages.csv` defines it at this
#: revision. Spelled out here so the migration keeps applying identically
#: after the CSV changes again (the c4f1a2d7be03 rule: a migration is a
#: historical record).
NEW_STAGES = (
    (1, "Letters & doubles",
     "All the single letters, doubled consonants (ck ss ll ff), and x — "
     "simple short-vowel words", 30, 60, 2),
    (2, "Digraphs", "Two letters one sound: sh ch th ng qu, plus w x y z v j",
     30, 60, 2),
    (3, "Endings", "Consonant clusters and the -s -es -ed -ing endings",
     30, 60, 2),
    (4, "Silent e", "Split digraphs a_e i_e o_e u_e e_e, and silent final e",
     60, 120, 2),
    (5, "Vowel teams", "ai ay ee ea oa ow igh, plus er and y as a vowel",
     60, 120, 2),
    (6, "R-controlled", "ar or ir ur, and the wa pattern", 60, 120, 2),
    (7, "Diphthongs", "oi oy ou ow au aw oo", 60, 120, 2),
    (8, "Soft & silent", "Soft c and g, plus kn wr mb ph", 120, 200, 3),
    (9, "Two syllables",
     "The -le ending; schwa relaxation becomes explicit curriculum",
     120, 200, 3),
    (10, "Suffixes", "tion ture sion", 120, 200, 3),
    (11, "Review", "Cumulative review; syllable cap lifted", 120, 200, 9),
)


def recompute_merged_stage_decodability(bind) -> int:
    """Recompute `stories.decodability` for every story at the merged stage 1.

    Runs the REAL validator — the same `validate_text` the serve-time gate uses
    — against the renumbered `sight_words` table, so the stored number cannot
    disagree with the live gate. Only stage-1 rows need it (see module
    docstring); for rows that were at old stage 2 the recomputation provably
    reproduces the stored value, which is what makes this idempotent and safe
    to run over the whole merged class. Module-level so it can be exercised
    directly by the sabotage checks.
    """
    from storyforge.decoder import mastered_ids, validate_text

    sight = frozenset(
        r[0]
        for r in bind.execute(
            sa.text("SELECT word FROM sight_words WHERE stage_id <= 1")
        )
    )
    rows = bind.execute(
        sa.text(
            "SELECT s.id, s.decodability, s.story_words, "
            "  string_agg(g.body, ' ' ORDER BY g.idx) AS body "
            "FROM stories s JOIN story_segments g ON g.story_id = s.id "
            "WHERE s.stage_id = 1 GROUP BY s.id ORDER BY s.id"
        )
    ).all()
    changed = 0
    mastered = mastered_ids(1)
    for row in rows:
        report = validate_text(row.body, mastered, sight, frozenset(row.story_words or []))
        new_value = round(report.decodability, 4)
        if row.decodability is None or abs(float(row.decodability) - new_value) > 5e-5:
            bind.execute(
                sa.text("UPDATE stories SET decodability = :d WHERE id = :sid"),
                {"d": new_value, "sid": row.id},
            )
            changed += 1
    return changed


def upgrade() -> None:
    bind = op.get_bind()

    top = bind.execute(sa.text("SELECT max(id) FROM stages")).scalar()
    if top is None or top == 11:
        return  # fresh database, or already renumbered — nothing to do
    if top != 12:
        raise RuntimeError(
            f"stages table tops out at {top}, which is neither the twelve-stage "
            "ladder this migration renumbers nor the eleven-stage one it "
            "produces. Refusing to guess what this database is."
        )

    # 1. Serving rows with a plain stage_id column. GREATEST folds old stages 1
    #    and 2 onto new stage 1; everything else decrements. No unique
    #    constraint involves stage_id on these tables (sight_words has one, but
    #    each word appears at exactly one stage, so no two rows can collide).
    for statement in (
        "UPDATE kids SET stage_id = GREATEST(stage_id - 1, 1)",
        "UPDATE stories SET stage_id = GREATEST(stage_id - 1, 1)",
        "UPDATE readings SET stage_id = GREATEST(stage_id - 1, 1)",
        "UPDATE gpcs SET stage_id = GREATEST(stage_id - 1, 1)",
        "UPDATE sight_words SET stage_id = GREATEST(stage_id - 1, 1)",
        "UPDATE story_word_bank SET min_stage = GREATEST(min_stage - 1, 1)",
    ):
        bind.execute(sa.text(statement))

    # 2. allowed_words is cumulative, so every old stage-1 row recurs at old
    #    stage 2 — assert that before deleting rather than trust the comment.
    orphans = bind.execute(
        sa.text(
            "SELECT count(*) FROM allowed_words a1 WHERE a1.stage_id = 1 "
            "AND NOT EXISTS (SELECT 1 FROM allowed_words a2 "
            "WHERE a2.stage_id = 2 AND a2.word = a1.word)"
        )
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} allowed_words rows exist at stage 1 but not at stage 2 "
            "— the table is not cumulative, so this renumber would delete "
            "vocabulary. Run `make precompute` against the OLD ladder first, "
            "or investigate."
        )
    bind.execute(sa.text("DELETE FROM allowed_words WHERE stage_id = 1"))
    # Decrement through a temp table: a single in-place UPDATE could trip the
    # (stage_id, word) unique constraint on a transient collision mid-statement.
    bind.execute(
        sa.text(
            "CREATE TEMP TABLE allowed_renumber ON COMMIT DROP AS "
            "SELECT stage_id - 1 AS stage_id, word, syllables, freq_rank "
            "FROM allowed_words"
        )
    )
    bind.execute(sa.text("DELETE FROM allowed_words"))
    bind.execute(
        sa.text(
            "INSERT INTO allowed_words (stage_id, word, syllables, freq_rank) "
            "SELECT stage_id, word, syllables, freq_rank FROM allowed_renumber"
        )
    )

    # 3. The stages rows themselves: contents shift down (ids never move, so
    #    child FKs stay valid throughout), then row 12 goes.
    for sid, name, description, len_min, len_max, max_syll in NEW_STAGES:
        bind.execute(
            sa.text(
                "UPDATE stages SET name = :name, description = :description, "
                "target_len_min = :len_min, target_len_max = :len_max, "
                "max_syllables = :max_syll WHERE id = :sid"
            ),
            {
                "sid": sid, "name": name, "description": description,
                "len_min": len_min, "len_max": len_max, "max_syll": max_syll,
            },
        )
    referrers = bind.execute(
        sa.text(
            "SELECT "
            "  (SELECT count(*) FROM kids WHERE stage_id = 12) + "
            "  (SELECT count(*) FROM stories WHERE stage_id = 12) + "
            "  (SELECT count(*) FROM readings WHERE stage_id = 12) + "
            "  (SELECT count(*) FROM gpcs WHERE stage_id = 12) + "
            "  (SELECT count(*) FROM sight_words WHERE stage_id = 12) + "
            "  (SELECT count(*) FROM allowed_words WHERE stage_id = 12)"
        )
    ).scalar_one()
    if referrers:
        raise RuntimeError(
            f"{referrers} rows still reference stage 12 after the renumber — "
            "refusing to delete the stage row out from under them."
        )
    bind.execute(sa.text("DELETE FROM stages WHERE id = 12"))

    # 4. The one recomputation (module docstring): merged-stage stories are now
    #    judged by a superset inventory, so their stored decodability moves.
    recomputed = recompute_merged_stage_decodability(bind)
    if recomputed:
        print(f"recomputed decodability for {recomputed} merged-stage stories")

    print(
        "renumbered the ladder to eleven stages: old stage N -> N-1, "
        "old stages 1 and 2 merged into stage 1. Eval history keeps its "
        "recorded labels (see this migration's docstring)."
    )


def downgrade() -> None:
    raise RuntimeError(
        "the stage-ladder renumber cannot be downgraded: old stages 1 and 2 "
        "were merged, and un-merging them from the data alone would have to "
        "guess which children and stories belonged to a stage that no longer "
        "exists. Restore from a backup taken before f3a91c6d0e24 instead."
    )
