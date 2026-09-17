"""Small text helpers shared by the harmonizers.

Free-text metadata arrives with inconsistent case, unicode dashes, double
spaces and underscores used as separators. Every comparison in this project
goes through `clean()` first, so "Hippocampus ", "HIPPOCAMPUS" and
"hippocampus" are treated as the same word.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
_SPACES = re.compile(r"\s+")

# Values that mean "nothing was recorded". Kept deliberately small: words such
# as "none" or "control" carry meaning in treatment fields, so those fields do
# their own checks before calling `is_missing`.
MISSING_VALUES = frozenset(
    {
        "",
        "na",
        "n/a",
        "n.a",
        "nan",
        "null",
        "unknown",
        "not available",
        "not applicable",
        "not determined",
        "not specified",
        "not reported",
        "nd",
        "unspecified",
        "missing",
        "-",
        "--",
        "?",
    }
)


def clean(value: object) -> str:
    """Lowercase, normalize unicode, unify dashes and collapse whitespace."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).translate(_DASHES)
    return _SPACES.sub(" ", text).strip().lower()


def clean_words(value: object) -> str:
    """Like `clean`, but also treats underscores as spaces ("Young_rep1" -> "young rep1")."""
    return _SPACES.sub(" ", clean(value).replace("_", " ")).strip()


def is_missing(value: object) -> bool:
    """True when a value is empty or one of the usual 'not recorded' placeholders."""
    return clean(value).strip(" .") in MISSING_VALUES


@lru_cache(maxsize=2048)
def word_pattern(term: str) -> re.Pattern[str]:
    """Regex that finds `term` as a whole word, even if the term has punctuation."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")


def contains_term(text: str, term: str) -> bool:
    """True if the cleaned `term` appears in the cleaned `text` as a whole word."""
    cleaned_term = clean_words(term)
    if not cleaned_term:
        return False
    return word_pattern(cleaned_term).search(clean_words(text)) is not None


def contains_any(text: str, terms: frozenset[str] | set[str] | list[str]) -> bool:
    """True if any of `terms` appears in `text` as a whole word."""
    haystack = clean_words(text)
    return any(word_pattern(t).search(haystack) for t in terms if t)


def first(values: list[str] | None, default: str = "") -> str:
    """First element of a list of strings, or `default` when the list is empty."""
    return values[0] if values else default
