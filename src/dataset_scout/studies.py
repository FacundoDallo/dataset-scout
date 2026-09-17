"""Study-level records built from NCBI esummary documents.

NCBI documents are dictionaries whose keys can change over time. The parser
lower-cases keys, tolerates missing ones, and `check_summary_schema` reports
any expected key that disappeared (a "schema drift" check), so a silent
format change upstream becomes a visible warning instead of empty columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .ncbi import uid_to_gse

EXPECTED_SUMMARY_KEYS = frozenset(
    {"accession", "title", "summary", "taxon", "gdstype", "n_samples", "pdat", "pubmedids", "samples", "entrytype"}
)
SUPERSERIES_MARKER = "this superseries is composed of"


@dataclass
class Study:
    uid: str
    gse: str
    title: str
    summary: str
    organisms: tuple[str, ...]
    gds_type: str
    platform: str
    n_samples_reported: int
    published: date | None
    pubmed_ids: tuple[str, ...]
    bioproject: str
    supplementary: str
    sample_accessions: tuple[str, ...]
    is_superseries: bool
    entry_type: str
    data_type: str = "not stated"
    data_type_flags: tuple[str, ...] = ()
    in_scope: bool = False
    exclusion_reason: str | None = None
    fetch_error: str | None = None
    extra_flags: list[str] = field(default_factory=list)

    @property
    def geo_url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={self.gse}"

    @property
    def organism(self) -> str:
        return "; ".join(self.organisms)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m", "%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def parse_study(document: dict[str, Any]) -> Study:
    doc = {str(k).lower(): v for k, v in document.items()}
    uid = str(doc.get("uid", ""))
    accession = str(doc.get("accession") or "").strip()
    if not accession.upper().startswith("GSE"):
        accession = uid_to_gse(uid) or accession
    summary = str(doc.get("summary") or "")
    title = str(doc.get("title") or "")
    samples = doc.get("samples") or []
    sample_accessions = tuple(
        str(s.get("accession")) for s in samples if isinstance(s, dict) and s.get("accession")
    )
    platform = str(doc.get("gpl") or "")
    lowered = f"{title} {summary}".lower()
    return Study(
        uid=uid,
        gse=accession.upper(),
        title=title,
        summary=summary,
        organisms=tuple(_as_list(doc.get("taxon"))),
        gds_type=str(doc.get("gdstype") or ""),
        platform=";".join(f"GPL{p}" if p.isdigit() else p for p in _as_list(platform)),
        n_samples_reported=_as_int(doc.get("n_samples")),
        published=_parse_date(doc.get("pdat")),
        pubmed_ids=tuple(_as_list(doc.get("pubmedids"))),
        bioproject=str(doc.get("bioproject") or ""),
        supplementary=str(doc.get("suppfile") or ""),
        sample_accessions=sample_accessions,
        is_superseries=SUPERSERIES_MARKER in lowered or "superseries" in title.lower(),
        entry_type=str(doc.get("entrytype") or ""),
    )


def check_summary_schema(documents: list[dict[str, Any]]) -> list[str]:
    """Expected keys that are missing from every document (likely an upstream change)."""
    if not documents:
        return []
    seen: set[str] = set()
    for document in documents:
        seen.update(str(k).lower() for k in document)
    return sorted(EXPECTED_SUMMARY_KEYS - seen)


def assess_scope(
    study: Study,
    organisms: tuple[str, ...],
    data_types: tuple[str, ...],
    exclude_superseries: bool,
) -> tuple[bool, str | None]:
    if exclude_superseries and study.is_superseries:
        return False, "SuperSeries (its SubSeries are assessed on their own)"
    if not set(study.organisms) & set(organisms):
        found = study.organism or "not stated"
        return False, f"organism out of scope ({found})"
    if study.data_type not in data_types:
        return False, f"data type out of scope ({study.data_type})"
    return True, None
