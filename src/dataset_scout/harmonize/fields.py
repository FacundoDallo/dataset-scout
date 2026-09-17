"""Decide which standard field a free-text characteristic key belongs to.

Submitters invent their own keys: "Sex", "gender", "sex (m/f)", "Age",
"age (months)", "age_at_sacrifice", "tissue/cell type", "brain region"...
The rules below map them to a small set of standard fields. The order
matters: "age group" is an age field before it is a group field, and
"cell type" is checked before "tissue".
"""

from __future__ import annotations

import re

from ..text import clean_words

FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    # A key named only "stage" is not age: in GEO it also holds treatment or disease stages.
    "age": re.compile(r"\bages?\b|\bdevelopmental stage\b|\bdev stage\b|\blife stage\b"),
    "sex": re.compile(r"\b(sex|gender)\b"),
    "cell_line": re.compile(r"\bcell ?lines?\b"),
    "cell_type": re.compile(
        r"\bcell ?types?\b|\bcell population\b|\bcell subsets?\b|\bcell subtypes?\b|^cells?$|\bsorted population\b"
    ),
    "tissue": re.compile(r"\b(tissues?|organs?|brain regions?|regions?|anatomical (site|region|location|entity))\b"),
    "strain": re.compile(r"\b(strains?|genetic background|background strain|background)\b"),
    "genotype": re.compile(
        r"\b(genotypes?|genetic modification|variation|transgenes?|mouse line|mouse model|knock-?out( status)?)\b"
    ),
    "treatment": re.compile(
        r"\b(treatments?|treated with|agents?|drugs?|compounds?|stimulation|stimulus|diets?"
        r"|intervention|exposure|injection)\b"
    ),
    "group": re.compile(r"\b(conditions?|groups?|cohorts?)\b"),
}

FIELD_ORDER: tuple[str, ...] = (
    "age",
    "sex",
    "cell_line",
    "cell_type",
    "tissue",
    "strain",
    "genotype",
    "treatment",
    "group",
)


def classify_key(key: str | None) -> str | None:
    """'Age (months)' -> 'age', 'gender' -> 'sex', 'brain region' -> 'tissue', 'passage' -> None."""
    if not key:
        return None
    text = clean_words(key)
    for field_name in FIELD_ORDER:
        if FIELD_PATTERNS[field_name].search(text):
            return field_name
    return None
