"""Tokenisation (§5.2.1): word chars + apostrophes, lowercased; everything else
becomes a PUNCT token that is ignored in scoring."""

from __future__ import annotations

import re
from typing import NamedTuple

# Curly apostrophes are normalised to ASCII so "don't" and "don’t" tokenise
# identically — otherwise the same story scores differently depending on which
# quote character the model emitted.
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'"})

_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*|[0-9]+|[^\sA-Za-z0-9]+")


class Token(NamedTuple):
    text: str  # normalised, lowercased for words
    raw: str  # as it appeared, for rendering
    is_word: bool
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    normalised = text.translate(_APOSTROPHES)
    tokens: list[Token] = []
    for m in _TOKEN.finditer(normalised):
        raw = m.group()
        # Digits are words for scoring purposes, not punctuation. They will fail
        # CMUdict lookup and land as OOV — deliberately loud, because "3" in a
        # decodable reader is a real problem, not a free pass.
        is_word = raw[0].isalpha() or raw[0].isdigit()
        tokens.append(
            Token(
                text=raw.lower() if is_word else raw,
                raw=raw,
                is_word=is_word,
                start=m.start(),
                end=m.end(),
            )
        )
    return tokens
