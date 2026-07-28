"""Path search over the alignment lattice (§5.2.5).

Semantics, verbatim from the spec: a word is DECODABLE iff **any** pronunciation
admits **any** complete alignment whose GPCs are all mastered. Otherwise, if a
complete alignment exists using unmastered GPCs, it is a VIOLATION reporting the
*minimal* missing set.
"""

from __future__ import annotations

from itertools import combinations

from storyforge.decoder.lattice import Lattice, build_lattice
from storyforge.decoder.types import GPC, Step

# Bounds for the minimal-missing-set search. Beyond these the answer is still
# correct as a *missing set* but no longer proven minimal, and the report says so
# via `missing_minimal=False` rather than quietly implying a proof.
MAX_CANDIDATES = 14
MAX_SUBSET_SIZE = 3


def _walk(lattice: Lattice, n_letters: int, n_phones: int, allowed: frozenset[str] | None):
    """Depth-first walk from (0,0) to (n_letters, n_phones).

    Returns the first complete path found, or None. Steps are visited in lattice
    order (which `resources.load_gpcs` sorts), so the result is deterministic —
    §5.7b depends on there being no set/dict iteration order in this path.
    """
    target = (n_letters, n_phones)
    seen: set[tuple[int, int]] = set()

    def dfs(node: tuple[int, int], path: list[Step]) -> list[Step] | None:
        if node == target:
            return list(path)
        if node in seen:
            return None
        seen.add(node)
        i, j = node
        for step in lattice.get(node, ()):
            if allowed is not None and any(g.id not in allowed for g in step.gpcs):
                continue
            path.append(step)
            found = dfs((i + len(step.letters), j + len(step.phonemes)), path)
            path.pop()
            if found is not None:
                return found
        return None

    return dfs((0, 0), [])


def align(
    word: str,
    prons: tuple[tuple[str, ...], ...],
    allowed: frozenset[str] | None,
) -> tuple[list[Step] | None, int]:
    """First complete alignment across all pronunciations, and how many
    pronunciations admit one (used for `heteronym_flag`)."""
    best: list[Step] | None = None
    n_ok = 0
    for pron in prons:
        lattice = build_lattice(word, pron)
        path = _walk(lattice, len(word), len(pron), allowed)
        if path is not None:
            n_ok += 1
            if best is None:
                best = path
    return best, n_ok


def _candidate_gpcs(
    word: str, prons: tuple[tuple[str, ...], ...], mastered: frozenset[str]
) -> list[GPC]:
    """Unmastered GPCs that actually appear somewhere in this word's lattices."""
    found: dict[str, GPC] = {}
    for pron in prons:
        for steps in build_lattice(word, pron).values():
            for step in steps:
                for g in step.gpcs:
                    if g.id not in mastered:
                        found[g.id] = g
    return [found[k] for k in sorted(found)]


def minimal_missing(
    word: str,
    prons: tuple[tuple[str, ...], ...],
    mastered: frozenset[str],
) -> tuple[list[str], bool, bool]:
    """Smallest set of unmastered GPCs that would make the word decodable.

    Returns ``(missing_ids, is_minimal, alignable)``.

    Iterative deepening over subsets: the first subset size that unlocks a
    complete path is by construction minimal. Candidates are only the unmastered
    GPCs present in this word's lattice, which keeps the search tiny in practice.
    """
    candidates = _candidate_gpcs(word, prons, mastered)

    # Does *any* complete path exist, ignoring mastery entirely? If not, the
    # inventory simply cannot spell this word (e.g. "beautiful", "of").
    if align(word, prons, None)[0] is None:
        return [], True, False

    if len(candidates) > MAX_CANDIDATES:
        # Too wide to prove minimality within budget. Report the unmastered GPCs
        # on one working path — correct, but flagged as not proven minimal.
        path, _ = align(word, prons, None)
        used = sorted({g.id for s in path or () for g in s.gpcs if g.id not in mastered})
        return used, False, True

    for size in range(1, min(len(candidates), MAX_SUBSET_SIZE) + 1):
        for subset in combinations(candidates, size):
            allowed = mastered | {g.id for g in subset}
            if align(word, prons, allowed)[0] is not None:
                return sorted(g.id for g in subset), True, True

    path, _ = align(word, prons, None)
    used = sorted({g.id for s in path or () for g in s.gpcs if g.id not in mastered})
    return used, False, True


def count_alignments(
    word: str, prons: tuple[tuple[str, ...], ...], allowed: frozenset[str], limit: int = 2
) -> int:
    """Count complete alignments up to ``limit`` — enough to set `ambiguity_flag`
    without enumerating an exponential path set."""
    total = 0
    for pron in prons:
        lattice = build_lattice(word, pron)
        total += _count_one(lattice, (len(word), len(pron)), allowed, limit - total)
        if total >= limit:
            break
    return total


def _count_one(
    lattice: Lattice, target: tuple[int, int], allowed: frozenset[str], limit: int
) -> int:
    """Count complete paths in one lattice, stopping at ``limit``.

    Defined at module scope rather than nested in the loop: a closure over the
    loop's `lattice`/`target` captures the variable, not its value, which is the
    kind of thing that works until someone makes the recursion lazy.
    """
    found = 0

    def dfs(node: tuple[int, int]) -> None:
        nonlocal found
        if found >= limit:
            return
        if node == target:
            found += 1
            return
        i, j = node
        for step in lattice.get(node, ()):
            if any(g.id not in allowed for g in step.gpcs):
                continue
            dfs((i + len(step.letters), j + len(step.phonemes)))
            if found >= limit:
                return

    if limit > 0:
        dfs((0, 0))
    return found
