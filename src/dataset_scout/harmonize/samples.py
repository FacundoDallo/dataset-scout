"""Harmonize one GEO sample into a flat, traceable record.

For every standard field the output keeps three things side by side:
the raw text as submitted, the harmonized value, and the rule that produced
it. That is what lets a reviewer audit any number in the report.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..config import AgeThresholds
from ..soft import SoftSample
from ..text import clean_words, is_missing
from .age import AgeResult, assign_age_group, find_age_in_text, parse_age
from .assay import is_expression_sample
from .fields import classify_key
from .sex import SexResult, find_sex_in_text, parse_sex
from .vocabulary import Term, Vocabulary

# The columns of a harmonized sample, in order. The database schema, the CSV
# export and the tests all rely on this list (a "schema contract").
SAMPLE_COLUMNS: tuple[str, ...] = (
    "gsm",
    "title",
    "source_name",
    "organism",
    "molecule",
    "library_strategy",
    "library_source",
    "is_expression",
    "age_raw",
    "age_value",
    "age_unit",
    "age_months",
    "age_stage",
    "age_group",
    "age_rule",
    "age_source",
    "age_flags",
    "sex_raw",
    "sex",
    "sex_rule",
    "sex_source",
    "tissue_raw",
    "tissue",
    "tissue_ontology",
    "cell_type_raw",
    "cell_type",
    "cell_type_ontology",
    "cell_line",
    "sample_type",
    "strain_raw",
    "strain",
    "genotype_raw",
    "genotype_is_control",
    "treatment_raw",
    "treatment_is_control",
    "is_pooled",
    "series_ids",
)


@dataclass(frozen=True)
class HarmonizeOptions:
    age_fallback: bool = True
    sex_fallback: bool = True


def _join(values: list[str]) -> str:
    return " | ".join(v for v in values if v)


def _pick_term(values: list[str], *matchers) -> Term | None:
    for value in values:
        for matcher in matchers:
            term = matcher.find(value)
            if term is not None:
                return term
    return None


_UNIT_KEY = re.compile(r"\bunits?\b")
_UNIT_VALUE = re.compile(r"^(years?|months?|weeks?|days?)$")


def _is_unit_field(key: str | None, value: str) -> bool:
    return bool(key and _UNIT_KEY.search(clean_words(key)) and _UNIT_VALUE.match(clean_words(value)))


def _with_unit_fields(entries: list[tuple[str | None, str]]) -> list[tuple[str | None, str]]:
    """'age: 16' plus 'age_unit: months' -> 'age (months): 16': the submitter wrote the unit apart."""
    units = [clean_words(value) for key, value in entries if _is_unit_field(key, value)]
    if not units:
        return entries
    return [(f"{key} ({units[0]})", value) for key, value in entries if not _is_unit_field(key, value)]


def _resolve_age(
    fields: dict[str, list[tuple[str | None, str]]],
    organism: str,
    fallback_texts: list[tuple[str, str]],
    use_fallback: bool,
    other_texts: list[str] | None = None,
) -> tuple[AgeResult | None, str | None]:
    explicit = _with_unit_fields(fields.get("age", []))
    written = [(k, v) for k, v in explicit if not is_missing(v)]
    first_result: AgeResult | None = None
    for key, value in written:
        result = parse_age(value, key=key, organism=organism)
        if result.parsed:
            return result, "field"
        first_result = first_result or result
    if first_result is not None:
        # The submitter wrote an age we cannot read: report it, do not guess.
        return first_result, "field"
    if use_fallback:
        for source, text in fallback_texts:
            result = find_age_in_text(text, source)
            if result is not None:
                return result, "fallback"
        # Fields the harmonizer does not read ("time", "duration") sometimes hold the age.
        # Only an explicit "6 months old" is taken from them, never a bare word such as "young".
        for text in other_texts or []:
            result = find_age_in_text(text, "characteristic")
            if result is not None and result.months is not None:
                return result, "fallback"
    if explicit:
        key, value = explicit[0]
        return parse_age(value, key=key, organism=organism), "field"
    return None, None


def _resolve_sex(
    fields: dict[str, list[tuple[str | None, str]]],
    fallback_texts: list[tuple[str, str]],
    use_fallback: bool,
) -> tuple[SexResult | None, str | None]:
    explicit = fields.get("sex", [])
    written = [v for _, v in explicit if not is_missing(v)]
    first_result: SexResult | None = None
    for value in written:
        result = parse_sex(value)
        if result.sex is not None:
            return result, "field"
        first_result = first_result or result
    if first_result is not None:
        return first_result, "field"
    if use_fallback:
        for source, text in fallback_texts:
            result = find_sex_in_text(text, source)
            if result is not None:
                return result, "fallback"
    if explicit:
        return parse_sex(explicit[0][1]), "field"
    return None, None


def _sample_type(
    cell_line: Term | None,
    tissue: Term | None,
    cell_type: Term | None,
    all_text: str,
    vocab: Vocabulary,
    other_text: str = "",
) -> str:
    if cell_line is not None:
        return "cell line"
    if vocab.mentions(all_text, vocab.ipsc_terms):
        return "iPSC-derived"
    if vocab.mentions(all_text, vocab.organoid_terms):
        return "organoid"
    # How cells were obtained is often written in fields the harmonizer does not read,
    # such as "selection marker: CD11b+" or "time: 5 days in vitro".
    method_text = f"{all_text} {other_text}"
    if vocab.mentions(method_text, vocab.culture_terms) and cell_type is not None:
        return "primary culture"
    if vocab.mentions(method_text, vocab.isolation_terms):
        return "isolated cells"
    if cell_type is not None:
        return "isolated cells"
    if tissue is not None:
        return "tissue"
    return "not stated"


def harmonize_sample(
    sample: SoftSample,
    vocab: Vocabulary,
    thresholds_by_organism: dict[str, AgeThresholds],
    options: HarmonizeOptions | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (harmonized row, list of raw characteristic rows)."""
    options = options or HarmonizeOptions()
    title = sample.get("title")
    source_name = sample.get("source_name_ch1")
    organism = sample.get("organism_ch1")
    molecule = sample.get("molecule_ch1")
    library_strategy = sample.get("library_strategy")
    library_source = sample.get("library_source")

    fields: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    other_values: list[str] = []
    characteristic_rows: list[dict[str, Any]] = []
    for item in sample.characteristics:
        mapped = classify_key(item.key)
        characteristic_rows.append(
            {
                "gsm": sample.accession,
                "channel": item.channel,
                "position": item.position,
                "key_raw": item.key,
                "value_raw": item.value,
                "mapped_field": mapped,
            }
        )
        # Two-colour arrays describe a reference sample in channel 2; only
        # channel 1 describes the sample itself.
        if item.channel != 1:
            continue
        if mapped:
            fields[mapped].append((item.key, item.value))
        else:
            other_values.append(item.value)

    fallback_texts: list[tuple[str, str]] = [("group", v) for _, v in fields.get("group", [])]
    fallback_texts += [("source_name", source_name), ("title", title)]

    age, age_source = _resolve_age(fields, organism, fallback_texts, options.age_fallback, other_values)
    thresholds = thresholds_by_organism.get(organism)
    age_group = assign_age_group(age, thresholds) if age is not None else None

    sex, sex_source = _resolve_sex(fields, [("source_name", source_name), ("title", title)], options.sex_fallback)

    def written_values(name: str) -> list[str]:
        return [v for _, v in fields.get(name, []) if not is_missing(v)]

    tissue_values = written_values("tissue")
    cell_type_values = written_values("cell_type")
    cell_line_values = written_values("cell_line")
    free_text = [source_name, title]

    tissue = _pick_term(tissue_values + cell_type_values, vocab.tissues) or _pick_term(free_text, vocab.tissues)
    cell_type = _pick_term(cell_type_values + tissue_values, vocab.cell_types) or _pick_term(
        free_text, vocab.cell_types
    )
    all_values = [v for values in fields.values() for _, v in values]
    cell_line = _pick_term(cell_line_values, vocab.cell_lines) or _pick_term(free_text + all_values, vocab.cell_lines)
    if cell_line is not None and cell_type is None and cell_line.extra.get("lineage"):
        lineage = vocab.cell_types.find(cell_line.extra["lineage"])
        cell_type = lineage

    all_text = " ".join([source_name, title, *all_values])
    sample_type = _sample_type(cell_line, tissue, cell_type, all_text, vocab, " ".join(other_values))

    strain_values = written_values("strain")
    strain_term = _pick_term(strain_values, vocab.strains) or _pick_term(
        written_values("genotype"), vocab.strains
    )
    genotype_raw = _join(written_values("genotype"))
    treatment_raw = _join(written_values("treatment"))

    row: dict[str, Any] = {
        "gsm": sample.accession,
        "title": title,
        "source_name": source_name,
        "organism": organism,
        "molecule": molecule,
        "library_strategy": library_strategy,
        "library_source": library_source,
        "is_expression": is_expression_sample(library_strategy, molecule),
        "age_raw": age.raw if age else None,
        "age_value": age.value if age else None,
        "age_unit": age.unit if age else None,
        "age_months": age.months if age else None,
        "age_stage": age.stage if age else None,
        "age_group": age_group,
        "age_rule": age.rule if age else None,
        "age_source": age_source,
        "age_flags": ";".join(age.flags) if age else None,
        "sex_raw": sex.raw if sex else None,
        "sex": sex.sex if sex else None,
        "sex_rule": sex.rule if sex else None,
        "sex_source": sex_source,
        "tissue_raw": _join(tissue_values) or None,
        "tissue": tissue.canonical if tissue else None,
        "tissue_ontology": tissue.ontology if tissue else None,
        "cell_type_raw": _join(cell_type_values) or None,
        "cell_type": cell_type.canonical if cell_type else None,
        "cell_type_ontology": cell_type.ontology if cell_type else None,
        "cell_line": cell_line.canonical if cell_line else None,
        "sample_type": sample_type,
        "strain_raw": _join(strain_values) or None,
        "strain": strain_term.canonical if strain_term else (clean_words(strain_values[0]) if strain_values else None),
        "genotype_raw": genotype_raw or None,
        "genotype_is_control": vocab.is_control_genotype(genotype_raw) if genotype_raw else None,
        "treatment_raw": treatment_raw or None,
        "treatment_is_control": vocab.is_control_treatment(treatment_raw) if treatment_raw else None,
        "is_pooled": vocab.mentions(all_text, vocab.pooled_terms) or bool(sex and "pooled" in sex.flags),
        "series_ids": ";".join(sample.get_all("series_id")),
    }
    return row, characteristic_rows
