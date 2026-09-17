"""Normalize sex annotations to male / female / mixed.

Explicit fields ("sex", "gender") are trusted. A fallback can read the
words "male" or "female" from titles or source names, but those results are
flagged as inferred so they can be excluded or checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..text import clean_words, is_missing

_MALE_TOKENS = frozenset({"male", "males", "m", "man", "men", "boy", "boys", "xy", "\u2642"})
_FEMALE_TOKENS = frozenset({"female", "females", "f", "woman", "women", "girl", "girls", "xx", "\u2640"})
_MIXED_TOKENS = frozenset(
    {"mixed", "both", "both sexes", "mixed sex", "m/f", "f/m", "m+f", "f+m", "m and f", "f and m"}
)

_MALE_WORD = re.compile(r"\b(male|males|man|men)\b")
_FEMALE_WORD = re.compile(r"\b(female|females|woman|women)\b")
_POOLED = re.compile(r"\bpool(ed|s)?\b")


@dataclass(frozen=True)
class SexResult:
    raw: str
    sex: str | None
    rule: str
    flags: tuple[str, ...] = ()


def parse_sex(value: object) -> SexResult:
    raw = "" if value is None else str(value).strip()
    text = clean_words(raw).strip(" .")
    if is_missing(text):
        return SexResult(raw, None, "missing", ("missing",))
    if text in _MIXED_TOKENS:
        return SexResult(raw, "mixed", "explicit_mixed")
    if text in _MALE_TOKENS:
        return SexResult(raw, "male", "explicit_token")
    if text in _FEMALE_TOKENS:
        return SexResult(raw, "female", "explicit_token")

    has_male = bool(_MALE_WORD.search(text))
    has_female = bool(_FEMALE_WORD.search(text))
    flags = ("pooled",) if _POOLED.search(text) else ()
    if has_male and has_female:
        return SexResult(raw, "mixed", "explicit_both_words", flags)
    if has_female:
        return SexResult(raw, "female", "explicit_word", flags)
    if has_male:
        return SexResult(raw, "male", "explicit_word", flags)
    return SexResult(raw, None, "unparsed", ("unparsed",))


def find_sex_in_text(text: object, source: str) -> SexResult | None:
    """Fallback: read 'male'/'female' from free text such as a sample title."""
    raw = "" if text is None else str(text).strip()
    cleaned = clean_words(raw)
    has_male = bool(_MALE_WORD.search(cleaned))
    has_female = bool(_FEMALE_WORD.search(cleaned))
    if has_male and has_female:
        return SexResult(raw, "mixed", f"text_both_words:{source}", ("inferred",))
    if has_female:
        return SexResult(raw, "female", f"text_word:{source}", ("inferred",))
    if has_male:
        return SexResult(raw, "male", f"text_word:{source}", ("inferred",))
    return None
