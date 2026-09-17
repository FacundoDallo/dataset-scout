"""A fake NCBI for tests: synthetic answers, same shapes as the real service.

The study summaries below imitate the JSON that E-utilities returns for
db=gds (lower-case keys, numbers as strings in some places, samples as a
list of accession/title pairs). The accessions (GSE1000001..., GSM9000...)
are invented and the metadata is synthetic, written to reproduce problems
seen in real GEO records:

- GSE1000001  clean bulk RNA-seq design (the "ideal" study)
- GSE1000002  microarray; ages written five ways; wild-type and APP/PS1 mice
- GSE1000003  single-cell, only detectable from the library source;
              GEO reports 7 samples but 6 come back (a count mismatch)
- GSE1000004  a SuperSeries (must be excluded)
- GSE1000005  human data (organism out of scope)
- GSE1000006  BV-2 cell line treated with LPS, no ages
- GSE1000007  methylation profiling (data type out of scope, never fetched)
- GSE1000008  in scope, but GEO answers 404 for its samples
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"
SEQ = "Expression profiling by high throughput sequencing"
ARRAY = "Expression profiling by array"


def _summary(number: int, title: str, summary: str, gdstype: str, n_samples: int, **extra) -> dict:
    samples = [{"accession": f"GSM{9000000 + number % 1000 * 100 + i}", "title": ""} for i in range(1, n_samples + 1)]
    document = {
        "uid": str(200000000 + number),
        "accession": f"GSE{number}",
        "gds": "",
        "title": title,
        "summary": summary,
        "gpl": "24247",
        "gse": str(number),
        "taxon": "Mus musculus",
        "entrytype": "GSE",
        "gdstype": gdstype,
        "ptechtype": "",
        "valtype": "",
        "ssinfo": "",
        "subsetinfo": "",
        "pdat": "2024/01/15",
        "suppfile": "TXT",
        "samples": samples,
        "relations": [],
        "extrelations": [],
        "n_samples": n_samples,
        "seriestitle": "",
        "platformtitle": "",
        "platformtaxa": "",
        "samplestaxa": "",
        "pubmedids": ["30000001"],
        "projects": [],
        "ftplink": f"ftp://ftp.ncbi.nlm.nih.gov/geo/series/GSE1000nnn/GSE{number}/",
        "geo2r": "yes",
        "bioproject": f"PRJNA{number}",
    }
    document.update(extra)
    return document


SUMMARIES: list[dict] = [
    _summary(1000001, "Transcriptome of sorted microglia from young and old mice",
             "RNA-seq of FACS-sorted microglia from 3- and 24-month-old mice.", SEQ, 8),
    _summary(1000002, "Hippocampal gene expression in aging APP/PS1 mice",
             "Microarray analysis of hippocampus from wild-type and APP/PS1 mice at several ages.", ARRAY, 8,
             pubmedids=[]),
    _summary(1000003, "Brain myeloid cells across the lifespan",
             "We profiled brain myeloid cells from young and old mice.", SEQ, 7),
    _summary(1000004, "Microglia and aging (SuperSeries)",
             "This SuperSeries is composed of the SubSeries listed below.", SEQ, 20),
    _summary(1000005, "Human microglia from aged donors",
             "Microglia isolated from human post-mortem brain.", SEQ, 12, taxon="Homo sapiens"),
    _summary(1000006, "BV-2 microglial cells stimulated with LPS",
             "RNA-seq of BV-2 cells treated with LPS or vehicle.", SEQ, 4, pubmedids=[], bioproject="",
             suppfile=""),
    _summary(1000007, "DNA methylation of aged microglia",
             "Whole-genome bisulfite sequencing of microglia.", "Methylation profiling by high throughput sequencing", 6),
    _summary(1000008, "Microglia from middle-aged mice",
             "RNA-seq of microglia.", SEQ, 5),
]
SOFT_NOT_FOUND = {"GSE1000008"}


class FakeResponse:
    def __init__(self, status_code: int, text: str, content_type: str = "text/plain") -> None:
        self.status_code = status_code
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": content_type}


class FakeNcbiSession:
    """Answers the three endpoints the pipeline uses; counts the calls."""

    def __init__(self, summaries: list[dict] | None = None, fail_first: int = 0) -> None:
        self.summaries = {d["uid"]: d for d in (SUMMARIES if summaries is None else summaries)}
        self.calls: list[tuple[str, dict]] = []
        self.headers: dict[str, str] = {}
        self.fail_first = fail_first

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        params = dict(params or {})
        self.calls.append((url, params))
        if self.fail_first > 0:
            self.fail_first -= 1
            return FakeResponse(503, "Service Unavailable")
        path = urlparse(url).path
        if path.endswith("esearch.fcgi"):
            uids = list(self.summaries)[: int(params.get("retmax", 20))]
            body = {
                "header": {"type": "esearch", "version": "0.3"},
                "esearchresult": {
                    "count": str(len(self.summaries)),
                    "retmax": str(len(uids)),
                    "retstart": "0",
                    "idlist": uids,
                    "translationset": [],
                    "querytranslation": params.get("term", ""),
                },
            }
            return FakeResponse(200, json.dumps(body), "application/json")
        if path.endswith("esummary.fcgi"):
            ids = [i for i in str(params.get("id", "")).split(",") if i]
            result: dict = {"uids": ids}
            for uid in ids:
                result[uid] = self.summaries.get(uid, {"uid": uid, "error": "cannot get document summary"})
            body = {"header": {"type": "esummary", "version": "0.3"}, "result": result}
            return FakeResponse(200, json.dumps(body), "application/json")
        if path.endswith("acc.cgi"):
            accession = params.get("acc", "")
            file = FIXTURES / f"{accession}.soft"
            if accession in SOFT_NOT_FOUND or not file.exists():
                return FakeResponse(404, "<html><body>Not found</body></html>", "text/html")
            return FakeResponse(200, file.read_text(encoding="utf-8"))
        return FakeResponse(404, "unknown endpoint")


def parse_url(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
