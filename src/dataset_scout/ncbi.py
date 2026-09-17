"""Talk to NCBI: search GEO series and download their metadata.

Three public endpoints are used, all read-only and free:

1. E-utilities ``esearch`` (db=gds): which GEO series match a query.
2. E-utilities ``esummary`` (db=gds): study-level metadata for those series.
3. GEO ``acc.cgi`` in text mode: sample-level metadata (SOFT format) for a series.
   ``targ=gsm`` with a GSE accession returns every sample of that series; this is
   the same endpoint the open-source tool geofetch uses.

NCBI asks clients to stay under 3 requests per second without an API key
(10 with one) and to identify themselves with ``tool`` and ``email``.
The RecordingClient enforces the rate limit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .http import RecordingClient

log = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
GEO_QUERY_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
GSE_UID_OFFSET = 200_000_000


class NcbiError(RuntimeError):
    """NCBI answered, but the answer is not what we expected."""


@dataclass(frozen=True)
class SearchResult:
    term: str
    count: int
    uids: list[str]
    query_translation: str


def build_search_term(
    terms: str,
    organism: str | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
) -> str:
    """Compose the GEO DataSets query: the user's words, limited to Series (GSE) entries."""
    parts = [f"({terms})", "GSE[ETYP]"]
    if organism:
        parts.append(f'"{organism}"[Organism]')
    if published_from or published_to:
        start = published_from or "1900"
        end = published_to or "3000"
        parts.append(f'("{start}"[Publication Date] : "{end}"[Publication Date])')
    return " AND ".join(parts)


def uid_to_gse(uid: str | int) -> str | None:
    """GEO DataSets gives series a numeric UID of 200000000 + the GSE number."""
    try:
        number = int(uid)
    except (TypeError, ValueError):
        return None
    if GSE_UID_OFFSET < number < 300_000_000:
        return f"GSE{number - GSE_UID_OFFSET}"
    return None


class NcbiGeoClient:
    def __init__(
        self,
        http: RecordingClient,
        email: str | None = None,
        api_key: str | None = None,
        tool: str = "dataset-scout",
    ) -> None:
        self.http = http
        self.identity: dict[str, str] = {"tool": tool}
        if email:
            self.identity["email"] = email
        if api_key:
            self.identity["api_key"] = api_key
            self.http.min_interval = min(self.http.min_interval, 0.11)

    def search_series(self, term: str, retmax: int) -> SearchResult:
        params = {"db": "gds", "term": term, "retmax": retmax, "retmode": "json", **self.identity}
        response = self.http.get(f"{EUTILS_BASE}/esearch.fcgi", params)
        try:
            payload = json.loads(response.text)
            result = payload["esearchresult"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise NcbiError(f"Unexpected esearch answer: {response.text[:300]}") from exc
        if "ERROR" in result:
            raise NcbiError(f"NCBI rejected the query: {result['ERROR']}")
        return SearchResult(
            term=term,
            count=int(result.get("count", 0)),
            uids=[str(uid) for uid in result.get("idlist", [])],
            query_translation=str(result.get("querytranslation", "")),
        )

    def summarize_series(self, uids: list[str], batch_size: int = 100) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for start in range(0, len(uids), batch_size):
            batch = uids[start : start + batch_size]
            params = {"db": "gds", "id": ",".join(batch), "retmode": "json", **self.identity}
            response = self.http.get(f"{EUTILS_BASE}/esummary.fcgi", params)
            try:
                result = json.loads(response.text)["result"]
            except (json.JSONDecodeError, KeyError) as exc:
                raise NcbiError(f"Unexpected esummary answer: {response.text[:300]}") from exc
            for uid in result.get("uids", []):
                document = result.get(str(uid))
                if not isinstance(document, dict):
                    continue
                if "error" in document:
                    log.warning("  esummary could not describe UID %s: %s", uid, document["error"])
                    continue
                documents.append(document)
        return documents

    def fetch_series_samples(self, gse: str, view: str = "brief") -> str:
        params = {"acc": gse, "targ": "gsm", "form": "text", "view": view}
        return self.http.get(GEO_QUERY_URL, params).text
