"""Moderation node (§6, §13).

§13 requires three independent layers on every child-facing string:

    1. deterministic blocklist   <- runs FIRST, free, cannot be talked out of it
    2. allowlist-constrained generation  (the prompt + the §5 validator)
    3. this moderation pass      <- belt and suspenders

Layer 1 is a pure function and runs before any API call, so a blocked word costs
nothing and cannot be argued with. Layer 3 is a Haiku-class model judging tone
and content the other two layers cannot see — "the man hit the dog" is perfectly
decodable and perfectly blocklist-clean.

§6 is explicit that this runs *even though* the allowlist already constrains
heavily. For a child-facing product the cost of a false negative is not
symmetric with the cost of a false positive.

Layer 3 also carries the **grammar** check (docs/DEVIATIONS.md 4.12). The
validator proves decodability and this node judges safety; nothing looked at
whether the sentence was well-formed English, and live warm-pool output shipped
"He did not came back." — 100% decodable and wrong. It rides here because this
is already a Haiku call on the final text, so the extra judgement is nearly
free, and because the answer belongs in the same gate: an ungrammatical story is
rejected exactly like an unsafe one. The decodability gate is NOT lowered to
accommodate grammar — the warm pool absorbs the rejection.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from storyforge.llm.client import FAST_TIMEOUT_S, fast_model, get_client
from storyforge.llm.pricing import Usage, usage_from_response
from storyforge.precompute import load_blocklist

MODERATION_SYSTEM = """You review short stories written for children aged 5-10 \
who are learning to read.

These are storybook adventures. Dragons breathe fire, caves are dark, and \
characters run and hide. That is the genre working as intended — mild peril is \
expected and must be APPROVED. The test is not "could anything here be scary?" \
but "would this distress a child reading alone at bedtime?"

APPROVE, even though they involve a hazard:
- A dragon that is big, breathes fire, roars, or flies.
- A character who is scared, runs, hides, or gets lost and is then found.
- Dark caves, storms, deep water, high places.
- A problem that gets resolved.
- An OBJECT, a PLACE or a CREATURE described however the story likes: a big rock, a \
dark cave, wide wings, a big dragon, a little rabbit, a small den.

REJECT:
- Anyone or anything is hurt, injured, killed, or dies.
- Cruelty, bullying, or a character deliberately harming another.
- Weapons used against a character.
- A PERSON's body or looks described: that a man, woman, boy, girl, kid or named \
character is fat, thin, big, small, tall or short, or one person's body set beside \
another's. "The man was big." and "Sam is fat." are rejections even though nobody is \
unkind and nothing happens, because a word list this small turns such a sentence \
into a verdict about the child reading it. PEOPLE ONLY — a dragon, an animal, a cave \
or a rock is not a person, so "The big dragon" and "a big rock" stay APPROVED.
- Romance, substances, bodily functions, or bathroom humour.
- Real brands, real people, or real places presented as real.
- A frightening situation left unresolved at the end.

The prose will be extremely simple and repetitive. That is the reading format \
working as intended and is never a reason to reject.

Judge content and tone only. When the only concern is that a storybook hazard \
exists without anyone being harmed, approve.

SEPARATELY, judge whether the story is WELL-FORMED ENGLISH (docs/DEVIATIONS.md \
4.12). The writer works from a tiny word list, so it sometimes grabs a word that \
fits the sounds but breaks the sentence. This is a different question from \
safety: answer it independently in `well_formed`.

This is a question about GRAMMAR ONLY. It is not about whether the sentence makes \
sense, whether the word chosen was the best available one, or whether the \
punctuation is conventional. Before you flag any sentence, run the SUBSTITUTION \
TEST on it: swap every noun in the sentence for some other noun and change \
nothing else — not the verb, not its ending, not its number. If you find yourself \
editing the verb while you swap the noun you are no longer running the test, and \
the answer you get is worthless. If the result is correct English, then the grammar was already \
correct and the only thing wrong was the sense — set well_formed = true and move \
on. Only a sentence that is STILL broken after the swap is a grammar error.

  "Its wings sat."           -> "Its dogs sat."            correct  => true
  "The rocket ran fast."     -> "The dog ran fast."        correct  => true
  "A dragon lived in a can." -> "A dragon lived in a box." correct  => true
  "It had a big lot."        -> "It had a big dog."        correct  => true
  "A dragon sit in a cave."  -> "A cat sit in a box."      broken   => false
  "The man ran back stop."   -> "The dog ran back stop."   broken   => false

Now read your own ARROW back, because the test above cannot catch the one error it \
is made of. If the wrong word and the replacement are BOTH NOUNS, and they are \
DIFFERENT nouns, you have not found a grammar error: swapping a noun for another \
noun is the substitution test's own procedure, so a sentence whose only complaint \
is that the writer picked the wrong noun has already PASSED the test. "The dragon \
did not like the case." -> "The dragon did not like the box." is correct English, \
so "case -> cave" is a claim about SENSE with a grammar arrow drawn on it. Set \
well_formed = true. "A dragon lived in a can." is the same: "can -> cave" is the \
word the writer should have picked, which is never a grammar error. A word that \
sounds like a better fit, or sounds like another word, is word choice. The ONE \
exception is NUMBER: "Sam had three cat." (cat -> cats) and "Sam sat on a rocks." \
(rocks -> rock) are the SAME noun in the wrong form, and those ARE grammar errors.

Then, if the sentence contains one of the helper words "did", "do", "does", \
"can", "will", "may" or "must", run the HELPER TEST on it. After one of those \
helper words the next verb must be the PLAIN form — and the plain form is \
CORRECT. This is true with "not" in between and without it. So the only thing to \
check is whether that verb is a PAST form; if it is the plain form, the sentence \
is right:

  "It did nap."                nap is plain    correct  => true
  "The rocket did not stop."   stop is plain   correct  => true
  "It can hear the wind."      hear is plain   correct  => true
  "The dog does not run."      run is plain    correct  => true
  "She did see the burrow."    see is plain    correct  => true
  "He did not came back."      came is PAST    broken   => false  (came -> come)
  "Sam did went up the hill."  went is PAST    broken   => false  (went -> go)
  "The cats do not ran fast."  ran is PAST     broken   => false  (ran -> run)
  "The dragon can flew up."    flew is PAST    broken   => false  (flew -> fly)

NEVER propose changing a plain form after one of those helper words INTO a past \
form, an "-ed" form or a past participle. "did not stopped", "can heard", \
"did napped" and "did saw" are all wrong English, so a correction that would \
produce one of them proves the sentence was already correct — and asking for \
"saw" after "did", or "could" after "can", is the single most common way to get \
this backwards. Set well_formed = true. The \
HELPER TEST covers those seven words only: "is", "was", "were", "has", "have" \
and "had" take a different form ("was running", "has gone"), so judge those the \
usual way — "He has went home." is still broken (went -> gone).

Never change the HELPER WORD ITSELF. "can -> could", "will -> would", "may -> \
might", "do -> did" and "does -> did" are edits to the TENSE of a helper that was \
already correct, and a correct helper is not a wrong word — so none of them can \
ever be your arrow. "It can hear the wind outside." is well-formed as written, in \
any story, however much past tense surrounds it.

The HELPER TEST is ONLY about the verb standing directly after one of those seven \
words, and it says NOTHING about any other sentence. A sentence with no helper \
word in it is outside the test entirely, so do not carry the words "plain form" \
across to it. In particular the HELPER TEST has no opinion about a subject and its \
verb agreeing: "Its wings help it." contains no helper word, "help" is the correct \
form with the plural subject "wings", and "helps" would be the error. Do not flag \
it.

Judge each sentence ON ITS OWN. Set well_formed = false only when a single \
sentence survives the substitution test still broken AND you can name the one \
wrong word and the word that should replace it:
- Wrong verb form after a helper: "He did not came back."   (came -> come)
- Subject and verb disagree. ONE thing takes the "-s" verb and MORE THAN ONE takes \
the plain verb, so settle which way round the sentence is BEFORE you correct it: \
"A dragon sit in a cave." is broken (sit -> sits, one dragon) and "The cats runs \
fast." is broken (runs -> run, many cats) — but "Its wings help it." is CORRECT, \
because there is more than one wing, and asking for "wings helps" would be an \
agreement error of your own. \
TWO SUBJECTS JOINED BY "and" ARE MORE THAN ONE, however singular each one is by \
itself, so they take the PLAIN verb in exactly the same way: "Sam and the rabbit \
spin.", "The dragon and the man sit." and "The cat and rabbit nap." are all \
CORRECT, and "spin -> spins" or "sit -> sits" would be an agreement error of your \
own — the same mistake as "wings helps", and the commonest way to get this one \
backwards is to look only at the noun nearest the verb. Only a subject that is \
genuinely ONE thing takes "-s", so "Sam and the dog runs up the hill." is still \
broken (runs -> run, two of them).
- A stray or missing word, so the sentence cannot parse: \
"The dragon had a got time."   (a got -> a good)
- A pronoun in the wrong case, even where speech would allow it: \
"The dragon and him ran up the hill."   (him -> he)

Everything below is the decodable-reader format working as intended and is \
NEVER a reason to set well_formed = false:
- Very short sentences, fragments and one-word beats: "Sam sat.", \
"Up, up, up.", "Fun!", "The end."
- The same word, sentence or pattern repeated many times.
- A tiny, plain vocabulary; odd, bald or childish phrasing; a thin or abrupt \
story.
- A sentence opening with "And", "But", "So" or "Then".
- Emphatic "did" or "do" plus a plain verb: "It did nap.", "Sam did stand.", \
"The dragon did like the cave.", "She did see the burrow.", "She did see stars \
every day." Those are correct English, and an emphatic "did" the story did not \
NEED is still correct — "unnecessary emphatic did" is not a grammar error, and \
"did see -> saw" is never the arrow.
- The tense changing from one sentence to the next, or past and present mixed \
across the story. Consistency BETWEEN sentences is style, not grammar. NEVER \
mark a sentence wrong because of the tense used in a DIFFERENT sentence. The \
child has not been taught the "-ed" ending yet, so the past-tense form of a \
verb often does not exist in the word list and the writer MUST use the present. \
"It had big wings. Its wings help it." is well-formed; do not ask for "helped". \
"Sam sat in a rocket. Sam and Bob go up fast." is well-formed too. A modal — \
"can", "will", "may" — is always correct as written; never ask for "could", \
"would" or "might". Check your own note before you use it: if the justification you \
are about to write turns on TENSE at all — "to match past tense", "for \
consistency", "present tense for a current state", "the rest of the story is past" \
or anything like it — then unless the sentence is a PAST form standing directly \
after one of the HELPER TEST's seven words, set well_formed = true. That is the one \
tense error this half of the verdict recognises; every other tense question is \
style, and a note that reaches for a DIFFERENT sentence to justify itself is \
answering about the story, not the sentence. \
(Inside ONE sentence, verbs joined by "and" must still match: "Dan sat and \
admits it." is wrong. But two verbs in the SAME form always match, whatever their \
subjects are and however odd the result sounds: "Sam sat and it sat past the \
moon." is well-formed English — both verbs are "sat".)
- Reported speech that is not backshifted: "He said he will go." is fine.
- A sentence that is grammatical but strange, dull or illogical. Judge the \
grammar, not the sense.
- An impossible or silly thing happening. A rocket that runs, wings that sit, a \
character who lives in a can, something that "had a big lot" — every one of those \
is correct English and must be approved. The word list is tiny and frequently \
holds no better word; using the only available word is the format working, and \
"the writer should have picked a different word" is never a grammar error. This \
includes a plain verb where a more precise one would read better: "The girl said, \
this rabbit got a home." is well-formed English, and swapping "got" for "has" is a \
synonym, not a correction.
- Punctuation and capitalisation, including speech written without quotation \
marks: "The girl said, come here, little rabbit." is well-formed. A missing comma \
or quotation mark is not a wrong WORD, so it can never satisfy the rule above.
- A different preposition or particle, where the sentence still parses: "It went \
back off and ran in the forest." is well-formed English. "off" rather than "out" \
is a collocation preference, exactly like "the bird sat in the tree" versus "on \
the tree" — which one sounds better is word choice, and word choice is never a \
grammar error. (A stray VERB is different and IS an error: "The man ran back \
stop.")

If you cannot quote the broken sentence and give the exact replacement word, \
the story is well-formed. A replacement is one WORD swapped for another word in \
the same sentence — never a rewrite of the sentence, a better-sounding synonym, \
or a different thing for the story to say.

`grammar_note` must contain an ARROW and a REASON: quote the sentence, write \
`wrong_word -> right_word`, then say in three or four words WHY the first word is \
wrong. Quoting a sentence with no arrow after it is not a grammar finding, and an \
arrow with no reason is one you have not checked — write the reason and then read \
it back, because a reason about tense or about another sentence means the answer is \
well_formed = true. If you cannot write both, write exactly: none.

`grammar_note` is about a WRONG WORD and nothing else. If the note you are writing \
mentions the story's ending, whether a problem was resolved, safety, or what the \
story should have said, that is a CONTENT judgement: put it in `reason` and set \
well_formed = true. A note that says the grammar is correct and still reports \
well_formed = false is a contradiction — the answer in that case is true.

When in doubt, well_formed = true: a wrongly rejected story is thrown away and \
the child gets nothing in its place."""


#: What `moderate()` prefixes a reason with when the story was judged SAFE and
#: failed only on grammar. Named rather than inlined because it is the one thing
#: that distinguishes "rejected on content" from "rejected on grammar" after the
#: two halves are folded into a single `approved` — see `warm.run_grammar_backfill`.
UNGRAMMATICAL_PREFIX = "ungrammatical: "


#: A `grammar_note` of "none" means "no defect found" — the judge writes the note
#: BEFORE the boolean (see `ModerationVerdict`), so it needs a way to say "nothing".
_NO_GRAMMAR_DEFECT = {"none", "none.", "", "n/a"}


# --- the SENSE claim, checked mechanically (docs/DEVIATIONS.md 11.95) ---------
# A wrong-noun claim is the one grammar finding the SUBSTITUTION TEST cannot catch,
# because the test's own procedure is to swap the noun: swapping `case` for `box`
# in "The dragon did not like the case." gives correct English, so the test
# exonerates the sentence while the fix the judge is about to propose — `case ->
# cave` — is itself a noun swap. The prompt now says so, and this is the same
# property checked in code, off the note the judge has already written.
#
# 8.79 declined to enforce the note format in code and was right on its own terms:
# "the failure mode of getting it wrong is approving a story nobody vouched for".
# So every clause below narrows, never widens, and anything unrecognised leaves the
# rejection standing.

#: Words that put the following word in a NOUN SLOT. Used against the story body,
#: not against the note, because the note's quoting is the judge's to vary.
_NOUN_SLOT_MARKERS = frozenset(
    {
        "a",
        "an",
        "the",
        "its",
        "his",
        "her",
        "their",
        "our",
        "my",
        "your",
        "this",
        "that",
        "these",
        "those",
        "some",
        "one",
        "two",
        "three",
        "no",
    }
)

#: The closed noun list the sense check runs on. Deliberately INCOMPLETE and
#: deliberately unambiguous: concrete nouns of the decodable register that cannot
#: also be a verb, an adjective or a helper word in a child's story. A word that is
#: not here is not judged a noun, so the check does not fire and the rejection
#: stands — which is the direction STATE invariant 4 requires.
#:
#: Excluded on purpose, and a test asserts it: every word that has ever stood on
#: the wrong side of one of this judge's arrows in docs/DEVIATIONS.md (`came`,
#: `runs`, `sit`, `help`, `can`, `got`, `use`, `live`, `stop`), every helper word,
#: and every noun that doubles as a verb here (`nap`, `camp`, `run`, `spin`,
#: `look`, `play`, `rest`, `plan`, `name`, `place`, `light`, `water`, `fish`,
#: `park`, `time`, `lot`, `thing`).
_SENSE_CLAIM_NOUNS = frozenset(
    """
    astronaut bag bed bird boat book box boy branch bug burrow bus cap car case cat
    cave chair child children city cup dad den desk dog door dragon duck egg
    elephant fire flag food forest fox friend frog garden gift giraffe girl gold
    grass ground hat hen hill hole home horse house ice kid king kite knight lake
    lamp land leaf leg log man map mat men mom moon morning mother mouth mud net
    night nose pan panda path pen pig pit planet pond pot queen rabbit river road
    rock rocket roof room sand sea ship shop sister sky snack star stone storm
    street sun tail tent town toy tree truck wife wind window wing woman women wood
    yard
    """.split()
)

#: Both sides of the arrow, once the judge's punctuation is off it.
_WORD_RE = re.compile(r"[A-Za-z']+")
_QUOTED_RE = re.compile(r'"([^"]*)"')


def _noun_stem(word: str) -> str:
    """`rocks` -> `rock`, `boxes` -> `box`, `babies` -> `baby`; anything else as-is.

    Only ever strips a suffix when the result is itself a listed noun, so `case`
    stays `case` (not `cas`) and an unlisted word is returned untouched.
    """
    w = word.lower()
    if w in _SENSE_CLAIM_NOUNS:
        return w
    if w.endswith("ies") and w[:-3] + "y" in _SENSE_CLAIM_NOUNS:
        return w[:-3] + "y"
    if w.endswith("es") and w[:-2] in _SENSE_CLAIM_NOUNS:
        return w[:-2]
    if w.endswith("s") and w[:-1] in _SENSE_CLAIM_NOUNS:
        return w[:-1]
    return w


def _is_listed_noun(word: str) -> bool:
    return _noun_stem(word) in _SENSE_CLAIM_NOUNS


def _stands_in_a_noun_slot(word: str, body: str) -> bool:
    """Does `word` actually occupy a noun position somewhere in the story?

    The guard that stops this check dismissing a real STRAY WORD. "The man ran
    back stop." has no determiner in front of `stop`, so a `stop -> …` arrow can
    never be read as a wrong-noun claim however noun-shaped the two words are.
    One adjective is allowed in between, for "a big rock".
    """
    tokens = [t.lower() for t in _WORD_RE.findall(body)]
    target = word.lower()
    return any(
        tokens[i] == target
        and (
            tokens[i - 1] in _NOUN_SLOT_MARKERS or (i >= 2 and tokens[i - 2] in _NOUN_SLOT_MARKERS)
        )
        for i in range(1, len(tokens))
    )


def _arrow_pairs(note: str) -> list[tuple[str, str] | None]:
    """Every `wrong -> right` the note proposes, as word pairs.

    The judge writes the arrow between two WORDS ("case -> cave") or between two
    whole SENTENCES (`"…the case." -> "…the cave."`) — both shapes are live in the
    pool. A sentence pair is reduced to the single word that differs; anything that
    cannot be reduced to exactly one differing word yields `None`, which makes the
    whole note un-dismissable.
    """
    pairs: list[tuple[str, str] | None] = []
    for arrow in re.finditer(r"->", note):
        left, right = note[: arrow.start()], note[arrow.end() :]
        nxt = right.find("->")
        if nxt != -1:
            right = right[:nxt]

        # Prefer material OUTSIDE the quotes ("…case." case -> cave): the judge
        # quotes the sentence for context and then names the word.
        left_quotes, right_quotes = _QUOTED_RE.findall(left), _QUOTED_RE.findall(right)
        left_tail = left[left.rindex('"') + 1 :] if '"' in left else left
        right_head = right[: right.index('"')] if '"' in right else right
        lhs = _WORD_RE.findall(left_tail) or (
            _WORD_RE.findall(left_quotes[-1]) if left_quotes else []
        )
        rhs = _WORD_RE.findall(right_head) or (
            _WORD_RE.findall(right_quotes[0]) if right_quotes else []
        )

        if len(lhs) == 1 and rhs:
            # "case -> cave, wrong noun": the reason 8.79 requires runs on after
            # the replacement word, so only the first word of it is the proposal.
            pairs.append((lhs[0], rhs[0]))
            continue
        if len(lhs) > 1 and len(lhs) == len(rhs):
            differing = [(a, b) for a, b in zip(lhs, rhs, strict=True) if a.lower() != b.lower()]
            pairs.append(differing[0] if len(differing) == 1 else None)
            continue
        # A word on one side and a sentence on the other, or two sentences of
        # different lengths: not reducible, so not dismissable.
        pairs.append(None)
    return pairs


def is_sense_claim(note: str, body: str) -> bool:
    """True when EVERY arrow in `note` swaps one noun for a DIFFERENT noun.

    Four narrowing conditions, all of which must hold (11.95):
      - at least one arrow, and every arrow reducible to a word pair;
      - both words listed in `_SENSE_CLAIM_NOUNS`;
      - not the same noun in a different NUMBER — `rocks -> rock` is a real
        grammar error and stays one;
      - the wrong word stands in a noun slot in the body.
    """
    pairs = _arrow_pairs(note)
    if not pairs:
        return False
    for pair in pairs:
        if pair is None:
            return False
        wrong, right = pair
        if not (_is_listed_noun(wrong) and _is_listed_noun(right)):
            return False
        if _noun_stem(wrong) == _noun_stem(right):
            return False
        if not _stands_in_a_noun_slot(wrong, body):
            return False
    return True


class ModerationVerdict(BaseModel):
    # DEVIATIONS 9.50: `reason` is declared BEFORE `approved`, for the reason 8.77
    # already proved on the grammar half two fields down. Structured output is
    # emitted in schema order, so with the safety boolean first the judge committed
    # to it and then reasoned — and the rationalisation is legible in its own words:
    # "A dog hit a rock next to the cat, WHICH COULD BE INTERPRETED AS the dog
    # hitting the cat". Measured, field order the only variable, 3 runs each: story
    # 9136 (a trip-and-recover with an explicit happy ending) goes from REJECTED 3/3
    # to approved 3/3 and 9190 from REJECTED 3/3 to approved 2/3, while
    # `reject/unsafe-cruelty`, `reject/ungrammatical-verb-form` and the layer-1
    # gun/shot/dead case are unmoved. Two of run 114's eight rejections were
    # reproducible false positives. Reason first = derivation, boolean = conclusion.
    reason: str = Field(
        description=(
            "Fill this in FIRST, before approved. One sentence. Name what IN THE "
            "STORY decides it, quoting the words. If nothing in the story matches a "
            "REJECT bullet, say that. A reason that needs 'could be interpreted as', "
            "'suggests' or 'might' is not a finding."
        )
    )
    approved: bool = Field(
        description=(
            "The CONCLUSION of reason, not an independent judgement: true unless "
            "reason names something the story actually says that matches a REJECT "
            "bullet. True only if the story is safe for ages 5-10."
        )
    )
    # DEVIATIONS 8.77: `grammar_note` is declared BEFORE `well_formed`, and the order
    # is load-bearing. Structured output is emitted in schema order, so with the
    # boolean first the judge committed to it and then reasoned in the note — three
    # runs produced notes that ended "Re-evaluating: well-formed is true." beside
    # `well_formed=false`. The prompt's own gate ("if you cannot name the
    # replacement word, the story is well-formed") is unenforceable unless the note
    # exists before the boolean does. Note first = derivation, boolean = conclusion.
    grammar_note: str = Field(
        description=(
            "Fill this in FIRST, before well_formed. Quote the one sentence you "
            "believe is broken, then write the correction as an ARROW — "
            "wrong_word -> right_word — then WHY, in three or four words. If the "
            "substitution test and the helper test both come out correct, or you "
            "cannot write both the arrow and the reason, write exactly: none. "
            "Under twenty-five words."
        )
    )
    well_formed: bool = Field(
        description=(
            "The CONCLUSION of grammar_note, not an independent judgement: false only "
            "if grammar_note names a wrong word and its replacement, true if "
            "grammar_note is 'none'. Plain, short and repetitive prose is well-formed."
        )
    )


class ModerationResult(BaseModel):
    approved: bool
    reason: str
    layer: str  # "blocklist" | "model"
    blocked_words: list[str] = Field(default_factory=list)
    usage: Usage | None = None
    # §13's three layers cover safety; nothing covered GRAMMAR until now
    # (docs/DEVIATIONS.md 4.12). `well_formed` is folded into `approved` by
    # `moderate()`, so an ungrammatical story is rejected exactly like an unsafe
    # one and the warm pool absorbs it. Defaults describe "no grammar defect
    # found", which is the honest reading of the blocklist short-circuit — that
    # story died at layer 1 and was never judged for grammar.
    well_formed: bool = True
    grammar_note: str = ""
    #: The judge said "not well-formed" and its own arrow proved otherwise — one
    #: noun swapped for a DIFFERENT noun, which is a claim about SENSE (11.95).
    #: Recorded rather than silent: a check that overturns the judge has to be
    #: countable, or nobody can tell how often it fires or whether it is right.
    sense_claim_overturned: bool = False


def blocklist_check(body: str) -> list[str]:
    """Layer 1 — deterministic, exact-match, free (§6: "runs first, free").

    Tokenised the same way the validator tokenises, so "kill." and "Kill" are
    caught. Exact match only, per docs/DEVIATIONS.md 2.4.
    """
    from storyforge.decoder.tokenizer import tokenize

    blocked = load_blocklist()
    return sorted({t.text for t in tokenize(body) if t.is_word and t.text in blocked})


# --- a PERSON's body, found deterministically (docs/DEVIATIONS.md 11.93) ------
# The free half of the appearance check, and the only half that can sweep a
# BACKLOG. `SYSTEM_RULES` rule 7 stopped new stories describing a person's body
# (8.29) and layer 3 could not see one at all until 11.92, so the stories written
# before the rule were never judged for it and re-running detection over them
# finds nothing that detection did not have a bullet for. This scan does, costs no
# API call, and gives the same answer every time — which is what makes it a gate
# rather than a one-off pass. `scripts/sweep_person_body.py` drives it.

#: The words rule 7 names, plus the near synonyms a story would reach for instead.
_BODY_ADJECTIVES = frozenset(
    """
    fat thin big small tall short huge tiny large skinny chubby plump heavy slim
    round ugly pretty bald
    """.split()
)

#: A PERSON. Deliberately does NOT contain `family` — the only false hit in the
#: live pool was "she had a big family", which is about NUMBER, not a body — and
#: deliberately does not contain any creature: rule 7 permits a big dragon, and so
#: does 11.92's bullet.
_PERSON_NOUNS = frozenset(
    """
    man men woman women boy boys girl girls kid kids child children mom mum mother
    dad father baby babies lady guy guys person people sister brother wife husband
    aunt uncle son daughter twin king queen knight astronaut teacher friend friends
    he she him her his
    """.split()
)

#: Linking verbs, and the words that may stand between one and its adjective.
_COPULAS = frozenset(
    """
    is was are were am be been looks looked look feels felt feel gets got seems seemed grew
    """.split()
)
_COPULA_FILLER = frozenset("not so very too really quite still also now just more most".split())

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def person_body_hits(body: str, names: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Every sentence in `body` that describes a PERSON's body or looks.

    Two shapes, because those are the two the model writes:
      attributive  "Sam met a big man."       — the adjective sits on the noun
      copular      "The man was big."         — the adjective sits after "was"
    `names` carries the story's proper nouns ("Sam is fat."); the caller derives
    them, because one story rarely shows a name in a position that identifies it
    as one and a whole pool always does.

    Returns (sentence, what matched) pairs so a report can quote the story rather
    than assert about it. An adjective on a creature, a place or an object matches
    nothing here — that carve-out is the load-bearing half (11.92).
    """
    hits: list[tuple[str, str]] = []
    for sentence in _SENTENCE_SPLIT.split(body):
        words = _WORD_RE.findall(sentence)
        low = [w.lower() for w in words]
        people = {
            i
            for i, w in enumerate(low)
            if w in _PERSON_NOUNS or words[i] in names or words[i].rstrip("'s") in names
        }
        if not people:
            continue
        for i, word in enumerate(low):
            if word not in _BODY_ADJECTIVES:
                continue
            # "a big man", "the tall, thin girl"
            head = next(
                (
                    j
                    for j in range(i + 1, min(i + 4, len(low)))
                    if j in people
                    and all(low[k] in _BODY_ADJECTIVES or low[k] == "and" for k in range(i + 1, j))
                ),
                None,
            )
            if head is not None:
                hits.append((sentence.strip(), f"{word} {low[head]}"))
                continue
            # "The man was big.", "Sam is not fat."
            for p in sorted(people, reverse=True):
                if p >= i:
                    continue
                between = low[p + 1 : i]
                if any(b in _COPULAS for b in between) and all(
                    b in _COPULAS or b in _COPULA_FILLER or b in _BODY_ADJECTIVES for b in between
                ):
                    hits.append((sentence.strip(), f"{low[p]} … {word}"))
                    break
    return hits


def proper_names(bodies: list[str]) -> frozenset[str]:
    """Capitalised words that are used mid-sentence somewhere in the corpus.

    A name is what a name does: "Sam" is a name because some story says "A cat sat
    on Sam." Derived from the whole pool rather than from one story, because
    "Sam is fat." on its own shows the word only where any word is capitalised.
    """
    names: set[str] = set()
    for body in bodies:
        for sentence in _SENTENCE_SPLIT.split(body):
            for word in _WORD_RE.findall(sentence)[1:]:
                if word[:1].isupper() and word.lower() not in _PERSON_NOUNS:
                    names.add(word)
    return frozenset(names)


async def moderate(body: str) -> ModerationResult:
    """Run both layers. Short-circuits on the blocklist so a rejected story
    never costs an API call."""
    hits = blocklist_check(body)
    if hits:
        return ModerationResult(
            approved=False,
            reason=f"blocklisted words present: {', '.join(hits)}",
            layer="blocklist",
            blocked_words=hits,
        )

    client = get_client().with_options(timeout=FAST_TIMEOUT_S)
    model = fast_model()
    response = await client.messages.parse(
        model=model,
        max_tokens=1000,
        system=MODERATION_SYSTEM,
        messages=[{"role": "user", "content": f"Story:\n\n{body}"}],
        output_format=ModerationVerdict,
    )
    verdict = response.parsed_output
    if verdict is None:
        # §7: a schema-parse failure is an api_fail. Fail CLOSED — an
        # unparseable moderation result must never read as approval, and that
        # applies to the grammar half of the verdict too.
        return ModerationResult(
            approved=False,
            reason=f"moderation response unparseable (stop_reason={response.stop_reason})",
            layer="model",
            usage=usage_from_response(model, response.usage),
            well_formed=False,
        )

    # An ungrammatical story is rejected the same way an unsafe one is. Folding
    # it in here rather than asking the model to also flip `approved` keeps the
    # two judgements independent in the prompt (so grammar cannot leak into the
    # safety call) while the gate stays a single deterministic boolean.
    # `grammar_note` is now the judge's working rather than a defect record (8.77),
    # so "none" is the no-defect sentinel and a well-formed story keeps no note at
    # all — `warm.persist` and the §14 report both treat a non-empty note as
    # evidence of a defect, and that contract is older than this field's reordering.
    note = verdict.grammar_note.strip()

    # DEVIATIONS 11.95. The judge is asked to read its own arrow back; this reads
    # it back too, deterministically, and only ever in the one direction the arrow
    # itself settles — both sides a noun, and different nouns, so the substitution
    # test cannot have been run. Anything else leaves `well_formed` exactly as the
    # judge left it.
    well_formed = verdict.well_formed
    sense_claim = not well_formed and is_sense_claim(note, body)
    if sense_claim:
        well_formed = True

    if note.lower() in _NO_GRAMMAR_DEFECT or well_formed:
        note = ""

    reason = verdict.reason
    if verdict.approved and not well_formed:
        # Still fails CLOSED on a vague note (STATE invariant 4): a judge that says
        # "not well-formed" and cannot name the word is a rejection, not an approval.
        reason = f"{UNGRAMMATICAL_PREFIX}{note or 'sentence is not well-formed English'}"

    return ModerationResult(
        approved=verdict.approved and well_formed,
        reason=reason,
        layer="model",
        usage=usage_from_response(model, response.usage),
        well_formed=well_formed,
        grammar_note=note,
        sense_claim_overturned=sense_claim,
    )
