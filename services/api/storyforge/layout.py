"""§10's screen, and the only place its dimensions are written down.

§10, verbatim: *"One segment per screen, no scrolling on tablet — if text
overflows, generation length is wrong, not the layout."* and *"Max 7 segments
total."* Those two sentences are a **budget**, and until now nothing in the
generator had ever been told it (DEVIATIONS 19.1).

**What was actually wrong, in one sentence:** `warm._split_segments` put the
first six sentences on the first six screens and *every remaining sentence* on
the seventh. A stage-9 story is 15-25 sentences, so pages 0-5 carried 20-31
characters each and page 6 carried 509-586 — the page with the ending on it. The
clip fell between lines, so nothing looked sliced; a child pressed `Finish` on a
story whose last 276 words they had never seen. Measured over the live pool:
**68 of 763 servable segments overflowed and every one of them was `idx = 6`.**

Three separate defects made that possible, and this module exists because all
three needed the same numbers:

1. **No per-segment rule existed anywhere.** Not in `SYSTEM_RULES` (rule 8 was a
   whole-story word count), not in `build_request_block` (a word band), not in
   the schema (`StorySegment.text` was a bare `str`). The model was never told
   how many screens there are, let alone how big one is. *A rule the model is not
   given cannot be followed.*
2. **Nothing checked.** `passes_gate` asks about decodability, OOV and the
   story-word cap. A story can satisfy all three and still be unreadable because
   half of it is below the fold. *A rule nothing checks is decoration.*
3. **The splitter's tail-merge.** Its comment — "Merge the tail rather than
   truncate — dropping text would silently lose story" — is right that text must
   not be dropped and wrong about where to put it. `paginate` below keeps the
   losslessness and spends the seven screens evenly.

**Why the numbers are MEASURED and not chosen.** `SEGMENT_CHAR_BUDGET` is not a
guess and it is not read off the existing pool either — a bound derived from the
same stories it is meant to judge is circular. It comes from a 465-row synthetic
sweep through the real `ReadingView` in the production bundle (the `fit-check`
harness, `apps/web/fit-check/main.tsx`), at every character length from 100 to
209, over three word-length profiles and 0-3 story-word chips:

    1024x768 @ 40px   box 583px   all-fit to 129 chars   first overflow 130
    768x1024 @ 40px   box 839px   all-fit to 180 chars   first overflow 183
    1024x768 @ 34px   box 583px   all-fit to 153 chars   first overflow 156
    768x1024 @ 34px   box 839px   all-fit to 204 chars   no overflow <= 209

**The binding configuration is 1024x768 landscape at 40px**, and its measured
ceiling is 129 characters. 125 is that number with a ~3% margin, because a
465-row probe cannot enumerate every word-length distribution a real story has.
It is deliberately NOT larger: §11's 40px is the dyslexia affordance and is not
negotiable, §03 floors story text at 28px, and §10 says in its own words which
side gives when they collide.

**And the word ceiling is DERIVED from it, not chosen either.** §6 asks for
120-200 words at stages 8-11. 200 words is ~960 characters against the 875 this
box holds, so §6's ceiling and §10's box are arithmetically incompatible and the
old pool is what that looks like. `layout_word_ceiling()` is the largest word
count 7 screens can hold; `load_vocabulary` clamps `target_max` to it, so the
model is given ONE number and the validator enforces the SAME one. That is a
deviation from §6's literal band at the top stages, taken on §10's own sentence:
*if text overflows, generation length is wrong.*

Everything here is a pure function of a string. There is no import from
`pipeline`, `warm` or `llm` on purpose: all three need these numbers, and a
second copy of "how big is the screen" is exactly the drift that let
`pipeline._call` keep `max_tokens=8000` while `llm/generate.py` had moved to
16000 (DEVIATIONS 8.7/8.25).
"""

from __future__ import annotations

#: §10: "Max 7 segments total." The reading view shows one per screen.
MAX_SEGMENTS = 7

#: Characters one screen holds at the BINDING configuration — 1024x768 landscape
#: at §11's 40px reading size, whose §10 box measures 583px. See the module
#: docstring for the sweep this comes from: 129 chars is the largest length at
#: which every probe fitted, 130 the shortest that overflowed, and 125 is that
#: ceiling with a margin for word-length distributions the probe cannot cover.
#:
#: **Never raise this to make a gate green.** It is a fact about a rendered box,
#: and `make fit-check` measures the same box independently — raising the number
#: here does not move the box, it only stops this file from agreeing with it.
SEGMENT_CHAR_BUDGET = 125

#: What the whole story gets: 7 screens x 125 characters.
STORY_CHAR_BUDGET = MAX_SEGMENTS * SEGMENT_CHAR_BUDGET

#: Characters per word, INCLUDING the space and punctuation that follow it —
#: the conversion between §6's unit (words) and §10's unit (a rendered box).
#: Measured over all 110 servable stories in the live pool: 4.76 overall, 4.41
#: to 4.87 per stage, 5.33 for the single wordiest story. 5.2 is deliberately
#: above the mean: this converts a CEILING, so erring low would hand the model a
#: word count the box cannot actually hold and the validator would refuse
#: drafts the prompt had just asked for.
LAYOUT_CHARS_PER_WORD = 5.2


def layout_word_ceiling() -> int:
    """The most words 7 screens can hold. 168 today.

    A function rather than a constant so the derivation is visible at the call
    site: this number has no independent existence, it is `STORY_CHAR_BUDGET`
    in §6's unit, and changing either input must move it.
    """
    return int(STORY_CHAR_BUDGET // LAYOUT_CHARS_PER_WORD)


def split_sentences(body: str) -> list[str]:
    """One entry per sentence-ish chunk. Byte-for-byte the old splitter's first half.

    Sentences are the natural page break for a decodable reader, and this half of
    `warm._split_segments` was never the defect — the tail-merge below it was. It
    is unchanged, including its known limitation: it breaks inside quoted
    dialogue (DEVIATIONS 4.13), which is lossless and cosmetic.
    """
    marked = body.replace("! ", "|").replace("? ", "|").replace(". ", "|")
    parts = [s.strip() for s in marked.split("|")]
    return [p if p.endswith((".", "!", "?")) else p + "." for p in parts if p]


def _pack(parts: list[str], cap: int) -> list[str]:
    """Greedily fill screens to `cap` characters, never splitting a sentence.

    `if not cur` is the escape hatch that makes this total: a single sentence
    longer than `cap` gets its own screen rather than an empty one before it, so
    the function always returns a partition and never loops. That case is what
    `fit_refusal` reports; it is not this function's job to refuse.
    """
    pages: list[str] = []
    cur = ""
    for part in parts:
        candidate = part if not cur else f"{cur} {part}"
        if not cur or len(candidate) <= cap:
            cur = candidate
        else:
            pages.append(cur)
            cur = part
    if cur:
        pages.append(cur)
    return pages


def paginate(body: str, max_segments: int = MAX_SEGMENTS) -> list[str]:
    """Split `body` over `max_segments` screens, SHORTEST-LONGEST-PAGE first.

    The replacement for the tail-merge. Same two promises as before — one
    sentence per screen where that fits, and no text is ever dropped — plus the
    two that were missing: **the fullest screen is as empty as it can be**, and
    **every screen available is used**.

    Two steps, and the second one is not decoration:

    1. **Binary search on the page cap**, with a greedy feasibility check. That
       is the exact optimum for partitioning a sequence into at most k contiguous
       parts minimising the largest part: feasibility is monotone in the cap (a
       larger cap never needs more pages) and always holds at the top of the
       range, so the search terminates on the smallest cap that fits.
    2. **Fill the screens the search left empty.** The optimum cap can be
       reached with fewer pages than are available — three live stories packed
       into five or six screens where seven existed — and there is never a reason
       to leave one blank: splitting the widest screen again only lowers the
       maximum, which is better for a beginning reader and for the fit gate.

       This step is also what makes the function's output a pure function of
       (text, screen count) rather than of how the search happened to land, and
       that is what `test_grammar.py::test_story_body_round_trips_through_the_
       segment_split` needs: the repair path has to hold a story's existing
       segment count fixed, so if `paginate` under-filled, a repaired story and a
       freshly-split one would disagree about the same text. They cannot now.

    **A story of `max_segments` sentences or fewer is returned one per screen,
    unchanged.** That is already the smallest possible longest page — merging any
    two only makes one bigger — and it means this function is byte-identical to
    the old one for every story short enough never to have had the bug. Measured:
    stages 1-3 do not move at all.
    """
    parts = split_sentences(body)
    if len(parts) <= max_segments:
        return parts
    lo = max(len(p) for p in parts)
    hi = len(" ".join(parts))
    while lo < hi:
        mid = (lo + hi) // 2
        if len(_pack(parts, mid)) <= max_segments:
            hi = mid
        else:
            lo = mid + 1
    return _fill(_pack(parts, lo), max_segments)


def _fill(pages: list[str], want: int) -> list[str]:
    """Split the widest screen until there are `want` of them, or none can split.

    Widest FIRST, but falling through to the next one when it will not split.
    Two findings live in this shape, both from sabotage rather than from reading
    it back:

    * the first version took the single widest screen and gave up when it turned
      out to be one long sentence — which is exactly the shape that makes the
      search under-fill in the first place, so it bailed on precisely the cases
      it exists for;
    * the second version selected only from splittable screens, which is correct
      but not *structurally* terminating: any future edit reintroducing an
      unsplittable choice would spin forever rather than fail. The loop advances
      only on a real increase and stops otherwise, so it terminates whatever the
      selection does.
    """
    while len(pages) < want:
        before = len(pages)
        for widest in sorted(range(len(pages)), key=lambda i: -len(pages[i])):
            halves = paginate(pages[widest], max_segments=2)
            if len(halves) > 1:
                pages[widest : widest + 1] = halves
                break
        if len(pages) == before:
            break  # every screen is one sentence; the quota is genuinely unreachable
    return pages


def fit_refusal(body: str, budget: int = SEGMENT_CHAR_BUDGET) -> str | None:
    """Why this story cannot be shown on `MAX_SEGMENTS` screens, or `None` if it can.

    A sentence rather than a boolean, for the reason `story_word_refusal` is one:
    it is read straight into the rewrite prompt, and "too long" tells a model
    nothing it can act on. The message names the ceiling, the overflow and the
    size of the cut.

    **Emptiness is NOT refused here.** A body with no words paginates to zero
    screens and would trivially "fit"; refusing it is `passes_gate`'s job and it
    already does (STATE invariant 1), with a different remedy — write the story,
    not shorten it. Two gates saying the same thing in different words is how a
    rewrite prompt ends up with a false premise (DEVIATIONS 9.40).
    """
    pages = paginate(body)
    if not pages:
        return None
    worst = max(len(p) for p in pages)
    if worst <= budget:
        return None
    over = sum(1 for p in pages if len(p) > budget)
    return (
        f"The story does not fit the screen. It is shown {MAX_SEGMENTS} screens at a "
        f"time and one screen holds about {budget} characters — roughly "
        f"{budget // int(LAYOUT_CHARS_PER_WORD)} words, or two to three short "
        f"sentences. Split as evenly as it can be, {over} of {len(pages)} screens "
        f"still run over, the fullest at {worst} characters. The whole story has to "
        f"fit in {STORY_CHAR_BUDGET} characters and yours is {len(body)}: cut about "
        f"{max(0, len(body) - STORY_CHAR_BUDGET)} characters by telling the same "
        f"story in fewer words, never by leaving the ending out."
    )
