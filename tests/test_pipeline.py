"""End-to-end: the whole pipeline against the fake NCBI."""

import json

import pytest

from dataset_scout.db import query
from dataset_scout.pipeline import run_pipeline


def ranked(result):
    return query(result.artifacts.database, "SELECT * FROM v_study_overview WHERE in_scope ORDER BY score DESC")


def test_scope_decisions(recorded):
    result, session = recorded
    studies = query(result.artifacts.database, "SELECT gse, in_scope, exclusion_reason FROM studies ORDER BY gse")
    reasons = dict(zip(studies["gse"], studies["exclusion_reason"], strict=True))
    assert reasons["GSE1000004"].startswith("SuperSeries")
    assert reasons["GSE1000005"].startswith("organism out of scope")
    assert reasons["GSE1000007"].startswith("data type out of scope")
    assert studies["in_scope"].sum() == 5
    fetched = {params.get("acc") for url, params in session.calls if url.endswith("acc.cgi")}
    assert fetched == {"GSE1000001", "GSE1000002", "GSE1000003", "GSE1000006", "GSE1000008"}


def test_ranking_matches_the_design_of_each_study(recorded):
    result, _ = recorded
    table = ranked(result).set_index("gse")
    assert list(table.index[:3]) == ["GSE1000001", "GSE1000003", "GSE1000002"]
    assert table.loc["GSE1000001", "score"] == 100 and table.loc["GSE1000001", "tier"] == "ready"
    assert table.loc["GSE1000003", "data_type"] == "single-cell RNA-seq"
    # APP/PS1 mice are not baseline: only one wild-type old sample remains.
    assert (table.loc["GSE1000002", "n_young"], table.loc["GSE1000002", "n_old"]) == (3, 1)
    assert "under_replicated" in table.loc["GSE1000002", "flags"]
    assert "cell_line" in table.loc["GSE1000006", "flags"]
    assert "no_samples_retrieved" in table.loc["GSE1000008", "flags"]


def test_checks_catch_the_planted_problems(recorded):
    result, _ = recorded
    status = {c.name: c.status for c in result.checks}
    assert status["sample_metadata_retrieved"] == "warn"
    assert status["sample_counts_match"] == "warn"
    assert status["links_are_consistent"] == "pass"
    assert not result.failed


def test_cascade_only_narrows(recorded):
    result, _ = recorded
    counts = [step["count"] for step in result.summary["funnel"]]
    assert counts == sorted(counts, reverse=True)
    assert counts == [8, 8, 5, 3, 2, 2]


def test_sql_views_agree_with_the_python_summary(recorded):
    """Two independent computations of the same numbers must match."""
    result, _ = recorded
    python_side = {item["label"]: item["share"] for item in result.summary["completeness"]}
    sql = query(
        result.artifacts.database,
        """
        SELECT AVG(CASE WHEN sex IS NOT NULL THEN 1 ELSE 0 END) AS sex,
               AVG(CASE WHEN strain IS NOT NULL OR genotype_raw IS NOT NULL THEN 1 ELSE 0 END) AS background
        FROM v_in_scope_samples WHERE is_expression
        """,
    ).iloc[0]
    assert sql["sex"] == pytest.approx(python_side["Sex"])
    assert sql["background"] == pytest.approx(python_side["Strain or genotype"])
    usable = query(result.artifacts.database, "SELECT COUNT(*) AS n FROM v_usable_studies")["n"][0]
    assert usable == result.summary["n_usable"]


def test_curator_inbox_lists_what_was_not_guessed(recorded):
    result, _ = recorded
    inbox = query(result.artifacts.database, "SELECT field, raw_value FROM v_needs_review")
    assert ("age", "twenty months") in set(map(tuple, inbox.to_numpy()))
    assert "CD45int CD11b+ myeloid cells" in set(inbox["raw_value"])


def test_outputs_and_lineage(recorded, config):
    result, _ = recorded
    out = result.artifacts.output_dir
    for name in ("report.html", "one_pager.html", "run_summary.json", "run.log", "studies_ranked.csv",
                 "samples_harmonized.csv", "needs_review.csv", "data_checks.csv"):
        assert (out / name).exists(), name
    manifest = json.loads((out / "run_summary.json").read_text())
    assert manifest["lineage"]["config_sha256"] == config.sha256
    assert manifest["lineage"]["http"]["downloaded"] == 7
    assert (config.snapshot_dir / "snapshot_index.csv").exists()


def test_replay_reproduces_the_run_offline(recorded, config):
    first, _ = recorded
    second = run_pipeline(config, mode="replay")
    assert second.summary == first.summary
    assert second.http_stats["downloaded"] == 0
    a = query(first.artifacts.database, "SELECT * FROM samples ORDER BY gsm")
    b = query(second.artifacts.database, "SELECT * FROM samples ORDER BY gsm")
    assert a.equals(b)


def test_report_escapes_text_from_geo(config):
    from fake_ncbi import SUMMARIES, FakeNcbiSession

    hostile = [dict(SUMMARIES[0], title="<script>alert('x')</script> microglia")]
    result = run_pipeline(config, mode="auto", http_session=FakeNcbiSession(hostile), sleep=lambda s: None)
    html = result.artifacts.report.read_text(encoding="utf-8")
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_empty_search_fails_the_checks(config):
    from fake_ncbi import FakeNcbiSession

    result = run_pipeline(config, mode="auto", http_session=FakeNcbiSession([]), sleep=lambda s: None)
    assert result.failed
    assert "returned no studies" in result.artifacts.report.read_text(encoding="utf-8")
