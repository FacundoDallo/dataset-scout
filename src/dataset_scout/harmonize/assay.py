"""Classify what kind of data a GEO series contains.

GEO's study type ("gdsType") says whether a series is expression profiling
by sequencing or by array, but not whether the sequencing was bulk or
single-cell. Single-cell studies are detected from their title, summary,
sample library descriptions and per-cell quality-control fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..text import clean_words, contains_any

EXPRESSION_SEQ = "expression profiling by high throughput sequencing"
EXPRESSION_ARRAY = "expression profiling by array"


@dataclass(frozen=True)
class DataTypeResult:
    data_type: str
    flags: tuple[str, ...]
    components: tuple[str, ...]


def gds_components(gds_type: str) -> tuple[str, ...]:
    parts = [clean_words(p) for p in str(gds_type or "").replace(",", ";").split(";")]
    return tuple(sorted({p for p in parts if p}))


def classify_data_type(
    gds_type: str,
    texts: list[str],
    single_cell_terms: frozenset[str],
    library_sources: set[str] | None = None,
    sample_keys: set[str] | None = None,
    qc_keys: frozenset[str] = frozenset(),
) -> DataTypeResult:
    components = gds_components(gds_type)
    flags: list[str] = []
    has_seq = EXPRESSION_SEQ in components
    has_array = EXPRESSION_ARRAY in components
    other = [c for c in components if c not in {EXPRESSION_SEQ, EXPRESSION_ARRAY}]

    sources = {clean_words(s) for s in (library_sources or set())}
    # Plate-based studies list every cell as a sample and report per-cell fields such as nGene or nUMI,
    # even when their library source only says "transcriptomic".
    per_cell_fields = bool({clean_words(k) for k in (sample_keys or set())} & qc_keys)
    from_library_source = any("single cell" in s for s in sources)
    single_cell = from_library_source or per_cell_fields or contains_any(" ".join(texts), single_cell_terms)

    if other and (has_seq or has_array):
        flags.append("multi_assay")

    if has_seq:
        data_type = "single-cell RNA-seq" if single_cell else "bulk RNA-seq"
        if has_array:
            flags.append("sequencing_and_array")
    elif has_array:
        data_type = "microarray"
    elif any("methylation" in c for c in components):
        data_type = "methylation profiling"
    elif any("genome binding" in c or "chromatin" in c or "occupancy" in c for c in components):
        data_type = "chromatin profiling"
    elif any("non-coding rna" in c for c in components):
        data_type = "non-coding RNA profiling"
    elif components:
        data_type = "other"
    else:
        data_type = "not stated"

    if single_cell:
        flags.append("single_cell")
        if not from_library_source and not per_cell_fields:
            # Prose in the title or summary is the only evidence: a study that merely
            # compares itself with published single-cell data reads the same way.
            flags.append("single_cell_from_text")
    return DataTypeResult(data_type=data_type, flags=tuple(flags), components=components)


def is_expression_sample(library_strategy: str, molecule: str) -> bool:
    """RNA-seq samples, or array samples hybridized with RNA."""
    strategy = clean_words(library_strategy)
    if strategy:
        return strategy in {"rna-seq", "ssrna-seq", "mrna-seq"}
    return "rna" in clean_words(molecule)
