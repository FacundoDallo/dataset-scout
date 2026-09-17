import json

import pytest
from fake_ncbi import SUMMARIES, FakeNcbiSession

from dataset_scout.harmonize.assay import classify_data_type
from dataset_scout.harmonize.vocabulary import Vocabulary
from dataset_scout.http import HttpError, RecordingClient, ReplayMissError, request_key
from dataset_scout.ncbi import NcbiGeoClient, build_search_term, uid_to_gse
from dataset_scout.studies import assess_scope, check_summary_schema, parse_study

SC_TERMS = frozenset({"single cell", "scrna-seq"})


def client(tmp_path, mode="auto", session=None):
    return RecordingClient(tmp_path / "snap", mode=mode, session=session or FakeNcbiSession(), sleep=lambda s: None)


def test_request_key_ignores_credentials_and_order():
    a = request_key("https://x", {"db": "gds", "term": "a", "api_key": "SECRET"})
    b = request_key("https://x", {"term": "a", "email": "me@x.org", "db": "gds"})
    assert a == b


def test_record_then_replay_without_network(tmp_path):
    ncbi = NcbiGeoClient(client(tmp_path), email="me@example.org", api_key="SECRET")
    found = ncbi.search_series("microglia", retmax=3)
    assert found.count == 8 and len(found.uids) == 3
    offline = NcbiGeoClient(client(tmp_path, mode="replay", session=object()))
    assert offline.search_series("microglia", retmax=3).uids == found.uids
    stored = "".join(p.read_text() for p in (tmp_path / "snap" / "responses").iterdir())
    assert "SECRET" not in stored and "me@example.org" not in stored


def test_replay_fails_loudly_when_a_response_is_missing(tmp_path):
    with pytest.raises(ReplayMissError):
        client(tmp_path, mode="replay").get("https://example.org/never")


def test_transient_errors_are_retried(tmp_path):
    http = client(tmp_path, session=FakeNcbiSession(fail_first=2))
    NcbiGeoClient(http).search_series("x", retmax=1)
    assert http.stats["retries"] == 2


def test_permanent_errors_are_recorded_and_replayed(tmp_path):
    http = client(tmp_path)
    ncbi = NcbiGeoClient(http)
    with pytest.raises(HttpError):
        ncbi.fetch_series_samples("GSE1000008")
    with pytest.raises(HttpError):
        NcbiGeoClient(client(tmp_path, mode="replay", session=object())).fetch_series_samples("GSE1000008")


def test_snapshot_index_lists_every_request(tmp_path):
    http = client(tmp_path)
    NcbiGeoClient(http).search_series("x", retmax=1)
    index = http.write_index().read_text().splitlines()
    assert index[0].startswith("key,") and len(index) == 2


def test_search_term_and_uids():
    term = build_search_term("microglia", "Mus musculus", "2015", None)
    assert term == '(microglia) AND GSE[ETYP] AND "Mus musculus"[Organism] AND ("2015"[Publication Date] : "3000"[Publication Date])'
    assert uid_to_gse("200012345") == "GSE12345"
    assert uid_to_gse("100012345") is None


def test_summaries_are_parsed(tmp_path):
    docs = NcbiGeoClient(client(tmp_path)).summarize_series([d["uid"] for d in SUMMARIES], batch_size=3)
    assert len(docs) == 8 and check_summary_schema(docs) == []
    study = parse_study(docs[0])
    assert study.gse == "GSE1000001" and study.platform == "GPL24247"
    assert study.published.year == 2024 and study.pubmed_ids == ("30000001",)
    assert parse_study(docs[3]).is_superseries


def test_schema_drift_is_reported():
    docs = [{k: v for k, v in SUMMARIES[0].items() if k != "n_samples"}]
    assert check_summary_schema(docs) == ["n_samples"]


def test_keys_are_case_insensitive():
    upper = {k.upper(): v for k, v in SUMMARIES[0].items()}
    assert parse_study(json.loads(json.dumps(upper))).title == SUMMARIES[0]["title"]


def test_data_type_classification():
    seq = "Expression profiling by high throughput sequencing"
    assert classify_data_type(seq, ["bulk"], SC_TERMS).data_type == "bulk RNA-seq"
    assert classify_data_type(seq, ["scRNA-seq of brain"], SC_TERMS).data_type == "single-cell RNA-seq"
    assert classify_data_type(seq, [""], SC_TERMS, {"transcriptomic single cell"}).data_type == "single-cell RNA-seq"
    mixed = classify_data_type(f"{seq}; Genome binding/occupancy profiling by high throughput sequencing", [""], SC_TERMS)
    assert "multi_assay" in mixed.flags
    assert classify_data_type("Expression profiling by array", [""], SC_TERMS).data_type == "microarray"


def test_per_cell_quality_fields_mark_single_cell_data():
    """Plate-based studies list every cell as a sample; their per-cell QC fields give them away."""
    seq = "Expression profiling by high throughput sequencing"
    qc_keys = Vocabulary.load().single_cell_qc_keys
    assert {"ngene", "numi", "percentmito"} <= qc_keys
    cells = classify_data_type(
        seq, [""], SC_TERMS, library_sources={"transcriptomic"}, sample_keys={"nGene", "nUMI", "plate"}, qc_keys=qc_keys
    )
    assert cells.data_type == "single-cell RNA-seq"
    bulk = classify_data_type(
        seq, [""], SC_TERMS, library_sources={"transcriptomic"}, sample_keys={"age", "batch"}, qc_keys=qc_keys
    )
    assert bulk.data_type == "bulk RNA-seq"


def test_scope():
    study = parse_study(SUMMARIES[4])
    study.data_type = "bulk RNA-seq"
    ok, reason = assess_scope(study, ("Mus musculus",), ("bulk RNA-seq",), True)
    assert not ok and reason.startswith("organism out of scope")
