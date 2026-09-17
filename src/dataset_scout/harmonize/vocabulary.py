"""Match free text against a controlled vocabulary.

The vocabulary lives in a YAML file (see resources/vocabulary.yaml) so that a
scientist can add synonyms without touching Python.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ..text import clean_words, contains_any, word_pattern

DEFAULT_VOCABULARY = "vocabulary.yaml"
# "Trem2+/+" is a wild-type allele; "Trem2+/+; 5xFAD" is not an unmodified mouse.
_WILD_TYPE_ALLELE = re.compile(r"[a-z0-9][a-z0-9-]*\s*\+/\+")


@dataclass(frozen=True)
class Term:
    canonical: str
    category: str
    matched: str
    ontology: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class TermMatcher:
    """Finds the most specific vocabulary term mentioned in a piece of text."""

    def __init__(self, entries: dict[str, Any] | None, category: str) -> None:
        self.category = category
        pairs: list[tuple[str, str, dict[str, Any]]] = []
        for canonical, spec in (entries or {}).items():
            spec = dict(spec or {})
            canonical = str(canonical)
            synonyms = {clean_words(canonical)}
            synonyms.update(clean_words(s) for s in spec.get("synonyms", []) or [])
            for synonym in synonyms:
                if synonym:
                    pairs.append((synonym, canonical, spec))
        # Longest synonym first: "prefrontal cortex" must win over "cortex".
        pairs.sort(key=lambda item: (-len(item[0]), item[0]))
        self._patterns = [(word_pattern(s), s, c, spec) for s, c, spec in pairs]
        self.canonical_terms = sorted({c for _, c, _ in pairs})
        self.synonyms = frozenset(s for s, _, _ in pairs)

    def find(self, text: object) -> Term | None:
        haystack = clean_words(text)
        if not haystack:
            return None
        for pattern, synonym, canonical, spec in self._patterns:
            if pattern.search(haystack):
                extra = {k: v for k, v in spec.items() if k not in {"synonyms", "ontology"}}
                return Term(canonical, self.category, synonym, spec.get("ontology"), extra)
        return None


def _term_set(values: list[str] | None) -> frozenset[str]:
    return frozenset(clean_words(v) for v in values or [] if clean_words(v))


def _plain(text: str) -> str:
    """Drop accents and read hyphens as spaces: 'naïve' -> 'naive', 'not-treated' -> 'not treated'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace("-", " ")


class Vocabulary:
    def __init__(self, data: dict[str, Any], source: str = "<dict>") -> None:
        self.source = source
        self.tissues = TermMatcher(data.get("tissue"), "tissue")
        self.cell_types = TermMatcher(data.get("cell_type"), "cell_type")
        self.cell_lines = TermMatcher(data.get("cell_line"), "cell_line")
        self.strains = TermMatcher(data.get("strain"), "strain")
        self.control_genotypes = _term_set(data.get("control_genotype_terms"))
        self.control_treatments = frozenset(_plain(t) for t in _term_set(data.get("control_treatment_terms")))
        self.isolation_terms = _term_set(data.get("isolation_terms"))
        self.ipsc_terms = _term_set(data.get("ipsc_terms"))
        self.organoid_terms = _term_set(data.get("organoid_terms"))
        self.culture_terms = _term_set(data.get("culture_terms"))
        self.pooled_terms = _term_set(data.get("pooled_terms"))
        self.single_cell_terms = _term_set(data.get("single_cell_terms"))
        self.single_cell_qc_keys = _term_set(data.get("single_cell_qc_keys"))

    @classmethod
    def load(cls, path: str | Path | None = None) -> Vocabulary:
        if path is None:
            text = resources.files("dataset_scout.resources").joinpath(DEFAULT_VOCABULARY).read_text(
                encoding="utf-8"
            )
            source = f"package:{DEFAULT_VOCABULARY}"
        else:
            text = Path(path).read_text(encoding="utf-8")
            source = str(path)
        return cls(yaml.safe_load(text) or {}, source=source)

    def is_control_genotype(self, value: object) -> bool | None:
        text = clean_words(value).strip(" .")
        if not text:
            return None
        # "wild-type", "wild type" and "wildtype" are the same word for this purpose.
        spaced = text.replace("-", " ")
        if text in self.control_genotypes or contains_any(spaced, {"wild type", "wildtype", "wt"}):
            return True
        # A value that is only a background strain ("C57BL/6J") or a wild-type allele ("Trem2+/+")
        # describes an unmodified mouse; anything added to it ("C57BL/6-ApoeKO") does not.
        return text in self.strains.synonyms or _WILD_TYPE_ALLELE.fullmatch(text) is not None

    def is_control_treatment(self, value: object) -> bool | None:
        text = _plain(clean_words(value)).strip(" .")
        if not text:
            return None
        # "None/naïve" joins two control words; "Vehicle/LPS" names a treatment.
        parts = [part.strip() for part in re.split(r"[/,;]", text) if part.strip()]
        return bool(parts) and all(part in self.control_treatments for part in parts)

    def mentions(self, text: object, terms: frozenset[str]) -> bool:
        return contains_any(str(text or ""), terms)
