"""Allowed-vocabulary precompute (§5.4).

Runs every candidate word through the validator per stage and writes
``allowed_words``. The output is three things at once: input to the generation
prompt (§6), a browsable admin artifact, and a demo exhibit — "here is every word
a stage-2 reader is permitted to see, machine-derived."

    frequency-en.csv (top ~20k, pinned) ∪ child-vocabulary.txt
      └─ ∩ CMUdict ─────────────────────────► candidates
           └─ blocklist + INFLECTIONS ──────► child-appropriateness (11.1-11.5)
                └─ ∩ common-words-en.txt ───► drops proper nouns (11.9)
                     └─ min_stage(word) ────► first stage that can decode it
                          └─ syllable cap ──► per-stage rows
                               └─ ∩ child vocabulary, stages >= 2 (11.8, 11.11)
                                    └─ staging table ─[one transaction]─► allowed_words

**This module decides which words are OFFERED. It is not the decodability gate.**
Every word here still has to align against the unchanged inventory, and
`test_every_allowed_word_revalidates_as_decodable` is what proves it.

Two spec deviations live in that pipeline, both in `docs/DEVIATIONS.md`:

**The blocklist match is morphology-aware (11.1-11.5).** The filter used to be
``if word in blocked`` against a list of BASE forms, so `fuck` was blocked and
`fucks` was not — 50 variants of blocked entries were in the live table while the
exact-match hit count read 0. `blocked_forms()` expands each entry with its regular
inflections. Substring matching is still refused (`class`, `grass`, `cocktail`);
see `data/blocklist.txt` for the two carve-outs that keep the expansion honest.

**§5.4's corpus was wrong for the audience (11.7-11.11).** "Intersect with SUBTLEX
top ~20k" filters on adult film-subtitle frequency, which proves a word is common
in adult speech and says nothing about whether a five-year-old knows it. `amid`
(rank 7,528) was offered at stage 1, along with `lebron`, `nixon` and `nasa`. Two
vendored lists replace it as the filter: `child-vocabulary.txt` (Dale-Chall
familiar words) for register and `common-words-en.txt` (lowercase Hunspell
headwords) for proper nouns. `frequency-en.csv` is demoted to one of two candidate
pools and the source of `freq_rank`; it is not re-generated or re-pinned.

Two deliberate choices:

**Word-outer, stage-inner.** ``build_lattice`` is ``lru_cache(maxsize=8192)`` but
there are ~19k candidates. Looping stage-outer would evict every lattice between
passes and rebuild each word eleven times. Word-outer builds each lattice once
while it is hot.

**Staging table, then one transaction.** A reader must never observe a partial
allowlist: a truncated list produces duller stories while every metric still
reports success — silent failure, the worst shape for this product. All rows are
computed and staged first; the live table is swapped in a single transaction, so
a crash mid-computation leaves the previous allowlist untouched.
"""

from __future__ import annotations

import asyncio
import csv
import time
from functools import cache
from pathlib import Path

from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, load_lexicon, mastered_ids
from storyforge.decoder.lexicon_util import aligned_syllables
from storyforge.decoder.search import align

DATA = Path(__file__).resolve().parents[3] / "data"

# DEVIATIONS 11.11. Every stage at or above this gets the child-vocabulary register
# filter. It is **1**: the filter applies everywhere, chosen on word quality and not
# tuned to protect a row count.
#
# The knob dates from the TWELVE-stage ladder, where old stage 1 was the one stage
# the right answer cost something: its nine-letter inventory could spell only **78**
# child-vocabulary words, a measured CEILING, against §5.4's "~150-2,500
# words/stage". The remedy was the ladder, not the filter — the user authorised
# retiring that stage (docs/DECISION-stage-ladder.md, DEVIATIONS 12.1-12.2), and
# since the renumber the entry stage is the old stage 2 inventory at **771** words.
# Every stage now clears the floor, so this knob has no stage left to protect;
# raising it would only readmit adult register (`amid`) somewhere. It stays 1.
REGISTER_FILTER_FROM_STAGE = 1

# DEVIATIONS 11.10. Child-vocabulary words that the adult frequency list ranks
# outside the top 20k have no `freq_rank`, and that column decides the §6 prompt
# window (`ORDER BY freq_rank LIMIT 400`). They get a synthetic rank from this
# base: deliberately out of band, so they sort to the tail of the window instead of
# displacing genuinely frequent words, and so the number is visibly synthetic in
# the admin artifact rather than passing as a measurement.
_UNRANKED_RANK_BASE = 100_000

# DEVIATIONS 11.3. The one blocklist section that is NOT inflected — `los` + es is
# `loses` and `san` + s is `sans`. Matched against the section header in
# `data/blocklist.txt`; `test_the_uninflected_section_header_still_exists` fails if
# the header is renamed, so the carve-out cannot go silent.
FRAGMENT_SECTION = "proper-noun fragments from an adult corpus"

_VOWELS = frozenset("aeiou")


def _doubles_final_consonant(word: str) -> bool:
    """CVC ending, where English doubles before `-ed`/`-ing`: stop -> stopped."""
    return (
        len(word) >= 3
        and word[-1] not in _VOWELS
        and word[-1] not in "wxy"
        and word[-2] in _VOWELS
        and word[-3] not in _VOWELS
    )


def inflect(word: str) -> frozenset[str]:
    """The regular INFLECTIONAL forms of `word`, excluding `word` itself.

    Plural/3rd-person (`-s`, `-es`, `y` -> `ies`, `f` -> `ves`), past (`-ed`) and
    progressive (`-ing`), with final-consonant doubling. Used in both directions by
    one generator (DEVIATIONS 11.2, 11.8): expand-to-REJECT for the blocklist,
    expand-to-ACCEPT for the child-vocabulary list, which is how Dale-Chall's own
    "a listed word's inflections are familiar too" convention is honoured.

    Deliberately generative rather than a stemmer. Matching stays the exact `in`
    test it has always been, so an innocent word can only be rejected if some
    blocked entry generates that exact string — provable by construction instead of
    by trusting a stemmer not to map `butter` onto `butt`.

    DERIVATIONAL suffixes are excluded for that same reason: `-er` on a noun is not
    an inflection and `butt` + `er` is a real word. `-est` is excluded too, which is
    what keeps `hottest` out of a five-year-old's word bank. The handful of
    derivational forms that carry real hazard are explicit lines in
    `data/blocklist.txt` (`killer`, `attacker`, `fuckin`, `freaky`).
    """
    forms: set[str] = set()
    ends_in_consonant_y = word.endswith("y") and len(word) > 1 and word[-2] not in _VOWELS

    if word.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(word + "es")
    elif ends_in_consonant_y:
        forms.add(word[:-1] + "ies")
    elif word.endswith("f"):
        forms.add(word + "s")
        forms.add(word[:-1] + "ves")
    else:
        forms.add(word + "s")

    if word.endswith("e"):
        forms.add(word + "d")
        forms.add(word[:-1] + "ing")
    elif ends_in_consonant_y:
        forms.add(word[:-1] + "ied")
        forms.add(word + "ing")
    else:
        if _doubles_final_consonant(word):
            forms.add(word + word[-1] + "ed")
            forms.add(word + word[-1] + "ing")
        forms.add(word + "ed")
        forms.add(word + "ing")

    return frozenset(forms - {word})


def _expand(words: frozenset[str], min_source_len: int = 1) -> frozenset[str]:
    """`words` plus the inflections of every entry at least `min_source_len` long.

    The length guard exists for `common_forms()` and is measured, not defensive:
    inflecting the 175 one- and two-letter Hunspell headwords generates 452 strings
    reachable no other way, and they are overwhelmingly nonsense — `abing`, `bfed`,
    `cgs`, `cas` (from `ca`). `cas` is not hypothetical; it is in the frequency list
    and decodable at stage 1, so it was being certified as "a common English word".
    Nothing real is lost: `going`, `goes`, `doing`, `does` and `being` are all
    literal Hunspell entries, and `axes` arrives from `ax` on the child list.
    """
    sources = [w for w in words if len(w) >= min_source_len]
    return words.union(*(inflect(w) for w in sources)) if sources else words


def _read_word_list(name: str) -> list[str]:
    """One word per line, `#` comments and blanks dropped, lowercased.

    ONE WORD PER LINE is load-bearing across every list in `data/`: a
    space-separated rewrite of the blocklist once loaded 22 entries instead of 240
    and the only symptom was a row count moving the wrong way (DEVIATIONS 6.15).
    """
    lines = (DATA / name).read_text(encoding="utf-8").splitlines()
    return [ln.strip().lower() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def load_blocklist() -> frozenset[str]:
    """The LITERAL blocklist entries (§5.4). Contract unchanged.

    Still one word per line, still the flat set of what is written in the file.
    `llm/moderate.py` (§13 layer 1) and `llm/generate.py` both read this and both
    must keep matching literals: a layer-1 hit SHORT-CIRCUITS `moderate()`, so an
    entry there does not narrow vocabulary, it stops layer 3 from ever applying the
    hazard-vs-harm judgement §13 was calibrated for. 8.14 measured that costing 2 of
    3 rejections in a 180-cell matrix and 8.73 measured 3 of 9 in a 60-story run.
    The morphology-aware set is `blocked_forms()`, and only precompute uses it —
    not offering a word and killing a finished story are different costs (11.6).
    """
    return frozenset(_read_word_list("blocklist.txt"))


def load_blocklist_sections() -> dict[str, tuple[str, ...]]:
    """Blocklist entries grouped by their `# --- section ---` header.

    Only needed so `FRAGMENT_SECTION` can be excluded from inflection (11.3).
    """
    sections: dict[str, list[str]] = {}
    current = ""
    for raw in (DATA / "blocklist.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("# ---"):
            current = line.strip("# -").strip()
            sections.setdefault(current, [])
        elif line and not line.startswith("#"):
            sections.setdefault(current, []).append(line.lower())
    return {name: tuple(words) for name, words in sections.items()}


@cache
def blocked_forms() -> frozenset[str]:
    """Blocklist entries PLUS their inflections — what precompute filters on.

    DEVIATIONS 11.1-11.5. `data/blocklist.txt` holds base forms only, and the old
    exact-match filter let `fucks`, `dicks`, `asses`, `murders`, `killings`,
    `corpses`, `punching`, `attacking`, `addicts` and 41 more through while
    reporting zero blocklist hits.
    """
    blocked: set[str] = set()
    for section, words in load_blocklist_sections().items():
        blocked.update(words)
        if section != FRAGMENT_SECTION:
            blocked.update(*(inflect(w) for w in words))
    return frozenset(blocked)


@cache
def child_forms() -> frozenset[str]:
    """Dale-Chall familiar words plus their inflections (DEVIATIONS 11.8).

    The register filter: words known to >= 80% of fourth-graders, which is the
    question §5.4's adult-frequency corpus never asked.
    """
    return _expand(frozenset(_read_word_list("child-vocabulary.txt")))


@cache
def common_forms() -> frozenset[str]:
    """Lowercase Hunspell headwords plus inflections — the proper-noun filter.

    DEVIATIONS 11.9. `frequency-en.csv` is lowercased, so `Nixon` and `nixon` are
    indistinguishable in it; a Hunspell `.dic` keeps the case and an entry that
    appears only capitalised is a name. Applied at EVERY stage, including the ones
    the register filter does not reach, so raising `REGISTER_FILTER_FROM_STAGE`
    later cannot let `nasa` and `tampa` back in.

    Unioned with the child list, because a word Dale-Chall vouches for is by
    definition a common word: without this, a gap in one vendored list would
    silently delete vocabulary the other one certifies.
    """
    return _expand(frozenset(_read_word_list("common-words-en.txt")), 3) | child_forms()


def load_frequency() -> list[tuple[int, str]]:
    """(rank, word) from the vendored, SHA-pinned frequency list."""
    with (DATA / "frequency-en.csv").open(encoding="utf-8") as fh:
        return [(int(r["rank"]), r["word"]) for r in csv.DictReader(fh)]


def candidate_pool() -> list[tuple[int, str]]:
    """(rank, word) over the frequency list UNION the child vocabulary (11.10).

    §5.4's "intersect with SUBTLEX top ~20k" is wrong in both directions. 316
    Dale-Chall words — `acorn`, `beehive`, `blackbird`, `bandage`, `bumblebee` —
    are absent from `frequency-en.csv` entirely, because adult film subtitles rank
    them outside the top 20k. A children's reading app wants them.
    """
    ranked = load_frequency()
    known = {word for _, word in ranked}
    extra = sorted(child_forms() - known)
    return ranked + [(_UNRANKED_RANK_BASE + i, word) for i, word in enumerate(extra)]


def compute_rows(max_syllables: dict[int, int]) -> list[dict]:
    """Pure computation — no database. Returns allowed_words rows.

    Separated from persistence so the filtering logic is testable without
    Postgres, and so a slow database can never be mistaken for a slow validator.
    """
    lexicon = load_lexicon()
    blocked = blocked_forms()
    common = common_forms()
    child = child_forms()
    rows: list[dict] = []

    for rank, word in candidate_pool():
        if word in blocked:
            continue  # 11.1-11.5: entries AND their inflections
        if word not in common:
            continue  # 11.9: a proper noun (`nixon`) or not a word at all (`lebron`)
        prons = lexicon.get(word)
        if not prons:
            continue  # OOV against the pinned dictionary — conservative, per §18

        # min_stage: first stage whose mastered set admits a complete alignment.
        # One lattice per word answers all eleven stages (the lru_cache is why
        # this loop is word-outer).
        first = next(
            (s for s in range(1, MAX_STAGE + 1) if align(word, prons, mastered_ids(s))[0]),
            None,
        )
        if first is None:
            continue  # the inventory cannot spell it at any stage

        for stage in range(first, MAX_STAGE + 1):
            # DEVIATIONS 21.2. Counted PER STAGE and only over the pronunciations
            # that align there — the cap is about the reading the child produces,
            # and `min_syllables(prons)` was the fewest syllables in any CMUdict
            # entry for the word, including the ones the aligner could not use.
            # `every` was a 2-syllable word under a cap of 2 at stage 5 while the
            # only alignment available there is three syllables long. Never None
            # inside this loop: `stage >= first` and alignability is monotone in
            # the mastered set, so at least one pronunciation aligns.
            syllables = aligned_syllables(word, prons, mastered_ids(stage))
            if syllables is None or syllables > max_syllables.get(stage, 2):
                continue
            # 11.8/11.11: the register filter is per-STAGE, because stage 1 cannot
            # afford it. `amid` is decodable from stage 1 up, so it is dropped from
            # every stage this reaches and survives only where it does not.
            if stage >= REGISTER_FILTER_FROM_STAGE and word not in child:
                continue
            rows.append(
                {"stage_id": stage, "word": word, "syllables": syllables, "freq_rank": rank}
            )

    return rows


async def persist(session, rows: list[dict]) -> None:
    """Stage every row, then swap in one transaction (see module docstring)."""
    await session.execute(
        text(
            "CREATE TEMP TABLE allowed_staging ("
            "stage_id int, word varchar(64), syllables int, freq_rank int"
            ") ON COMMIT DROP"
        )
    )
    if rows:
        await session.execute(
            text(
                "INSERT INTO allowed_staging (stage_id, word, syllables, freq_rank) "
                "VALUES (:stage_id, :word, :syllables, :freq_rank)"
            ),
            rows,
        )
    # Single transaction: readers see the whole old allowlist or the whole new
    # one. Never a partial.
    await session.execute(text("DELETE FROM allowed_words"))
    await session.execute(
        text(
            "INSERT INTO allowed_words (stage_id, word, syllables, freq_rank) "
            "SELECT stage_id, word, syllables, freq_rank FROM allowed_staging"
        )
    )


async def run() -> dict[int, int]:
    async with SessionLocal() as session:
        stages = (
            await session.execute(text("SELECT id, max_syllables FROM stages ORDER BY id"))
        ).all()
        if not stages:
            raise SystemExit("no stages in the database — run `make seed` first")
        max_syllables = {row[0]: row[1] for row in stages}

        started = time.perf_counter()
        rows = compute_rows(max_syllables)
        computed = time.perf_counter() - started

        await persist(session, rows)
        await session.commit()

    counts: dict[int, int] = {}
    for row in rows:
        counts[row["stage_id"]] = counts.get(row["stage_id"], 0) + 1
    counts["_seconds"] = round(computed, 2)  # type: ignore[index]
    return counts


def main() -> None:
    counts = asyncio.run(run())
    seconds = counts.pop("_seconds")  # type: ignore[arg-type]
    print(f"{'stage':>5} {'words':>8}")
    for stage in sorted(counts):
        print(f"{stage:>5} {counts[stage]:>8,}")
    total = sum(counts.values())
    print(f"\n{total:,} rows across {len(counts)} stages in {seconds}s (§16 budget: 5 min)")
    # The lists the filters came from, counted rather than assumed: 6.15's own bug
    # was a row count moving the wrong way while a space-separated rewrite loaded 22
    # entries instead of 240, and the only way to catch that is to print the count.
    print(
        f"blocklist: {len(load_blocklist()):,} entries -> {len(blocked_forms()):,} forms "
        f"with inflections (11.1-11.5)"
    )
    print(
        f"child vocabulary: {len(_read_word_list('child-vocabulary.txt')):,} words -> "
        f"{len(child_forms()):,} forms; register filter applies from stage "
        f"{REGISTER_FILTER_FROM_STAGE} (11.8, 11.11)"
    )
    # §18: a stage with almost no vocabulary starves generation. Say so loudly
    # rather than letting it surface later as "the stories got boring". Since the
    # renumber (DEVIATIONS 12.1-12.2) EVERY stage must clear this: the entry stage
    # starts at 771 words, so a thin stage is never expected and renumbering again
    # would not fix one — the filter itself would need rethinking (11.11).
    thin = [s for s, n in counts.items() if n < 150]
    if thin:
        print(f"WARNING: stages {thin} have under 150 words — generation may starve (§18)")


if __name__ == "__main__":
    main()
