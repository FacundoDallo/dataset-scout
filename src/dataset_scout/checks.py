"""Automated data checks: the "validate" step of the pipeline.

Each check answers one question about the build and returns pass, warn or
fail, with a sentence explaining why. This mirrors QC in a regulated lab:
a result is only as trustworthy as the controls that ran with it.

- fail: the output cannot be trusted (for example, a score outside 0-100
  or a sample linked to a study that does not exist). `scout run` exits
  with an error code so CI catches it.
- warn: the output is usable, but a reviewer should know (for example,
  GEO reported 12 samples for a study and only 10 were retrieved).
- pass: nothing to report.

Checks never change the data. They only describe it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"
STATUS_ORDER = {FAIL: 0, WARN: 1, PASS: 2}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["check_name"] = row.pop("name")
        return row


def _examples(values: list[str], limit: int = 5) -> str:
    shown = ", ".join(values[:limit])
    return f"{shown} and {len(values) - limit} more" if len(values) > limit else shown


def check_search(search_count: int, retrieved: int, max_studies: int) -> CheckResult:
    if search_count == 0:
        return CheckResult("search_returned_studies", FAIL, "The GEO search returned no series. Check query.terms.")
    if search_count > retrieved and retrieved >= max_studies:
        return CheckResult(
            "search_returned_studies",
            WARN,
            f"{search_count} series matched but only {retrieved} were retrieved (limits.max_studies = "
            f"{max_studies}). Raise the limit to assess all of them.",
        )
    return CheckResult("search_returned_studies", PASS, f"{search_count} series matched; {retrieved} retrieved.")


def check_summary_schema(missing_keys: list[str]) -> CheckResult:
    if missing_keys:
        return CheckResult(
            "summary_schema",
            WARN,
            "NCBI study summaries no longer contain: "
            + ", ".join(missing_keys)
            + ". The upstream format may have changed; related columns will be empty.",
        )
    return CheckResult("summary_schema", PASS, "All expected fields are present in the NCBI study summaries.")


def check_unique_studies(duplicates: list[str]) -> CheckResult:
    if duplicates:
        return CheckResult(
            "unique_study_ids",
            WARN,
            f"{len(duplicates)} series appeared more than once in the search and were kept once: "
            f"{_examples(duplicates)}.",
        )
    return CheckResult("unique_study_ids", PASS, "Every series appears once.")


def check_fetch_errors(errors: pd.DataFrame) -> CheckResult:
    if len(errors):
        failed = sorted(set(errors["gse"].dropna().astype(str)))
        return CheckResult(
            "sample_metadata_retrieved",
            WARN,
            f"Sample metadata could not be retrieved for {len(failed)} series: {_examples(failed)}. "
            "They stay in the ranking with a low score and the flag 'no_samples_retrieved'.",
        )
    return CheckResult("sample_metadata_retrieved", PASS, "Sample metadata was retrieved for every screened series.")


def check_sample_counts(studies: pd.DataFrame, links: pd.DataFrame) -> CheckResult:
    """GEO's reported sample count should match the samples actually parsed."""
    fetched = studies[studies["samples_fetched"] & studies["fetch_error"].isna()]
    if not len(fetched):
        return CheckResult("sample_counts_match", PASS, "No series had sample metadata to compare.")
    parsed = links.groupby("gse")["gsm"].nunique() if len(links) else pd.Series(dtype="int64")
    mismatches = []
    for _, row in fetched.iterrows():
        reported = int(row["n_samples_reported"] or 0)
        got = int(parsed.get(row["gse"], 0))
        if reported and got != reported:
            mismatches.append(f"{row['gse']} ({got} of {reported})")
    if mismatches:
        return CheckResult(
            "sample_counts_match",
            WARN,
            f"{len(mismatches)} series returned a different number of samples than GEO reports: "
            f"{_examples(mismatches)}.",
        )
    return CheckResult(
        "sample_counts_match", PASS, f"Sample counts match GEO for all {len(fetched)} retrieved series."
    )


def check_links(links: pd.DataFrame, studies: pd.DataFrame, samples: pd.DataFrame) -> CheckResult:
    unknown_studies = sorted(set(links["gse"]) - set(studies["gse"])) if len(links) else []
    unknown_samples = sorted(set(links["gsm"]) - set(samples["gsm"])) if len(links) else []
    if unknown_studies or unknown_samples:
        parts = []
        if unknown_studies:
            parts.append(f"{len(unknown_studies)} links point to unknown series ({_examples(unknown_studies)})")
        if unknown_samples:
            parts.append(f"{len(unknown_samples)} links point to unknown samples ({_examples(unknown_samples)})")
        return CheckResult("links_are_consistent", FAIL, "; ".join(parts) + ".")
    return CheckResult("links_are_consistent", PASS, f"All {len(links)} series-sample links resolve.")


def check_conflicting_samples(conflicts: list[str]) -> CheckResult:
    if conflicts:
        return CheckResult(
            "sample_records_consistent",
            WARN,
            f"{len(conflicts)} samples were described differently by two series; the first description "
            f"was kept: {_examples(conflicts)}.",
        )
    return CheckResult(
        "sample_records_consistent", PASS, "Samples shared between series are described identically."
    )


def check_scores(quality: pd.DataFrame) -> CheckResult:
    if not len(quality):
        return CheckResult("scores_in_range", WARN, "No in-scope series were scored.")
    bad = quality[(quality["score"] < 0) | (quality["score"] > 100) | quality["score"].isna()]
    if len(bad):
        return CheckResult(
            "scores_in_range", FAIL, f"{len(bad)} scores fall outside 0-100: {_examples(list(bad['gse']))}."
        )
    return CheckResult("scores_in_range", PASS, f"All {len(quality)} scores are between 0 and 100.")


def check_age_parsing(expression: pd.DataFrame) -> CheckResult:
    written = expression[expression["age_raw"].notna() & (expression["age_rule"] != "missing")]
    if not len(written):
        return CheckResult("ages_readable", WARN, "No in-scope sample reports an age.")
    unreadable = written["age_rule"].isin(["unparsed", "number_without_unit"])
    share = float(unreadable.mean())
    detail = (
        f"{int(unreadable.sum())} of {len(written)} written ages ({share:.0%}) could not be converted "
        "to months; they are listed in the curator inbox."
    )
    return CheckResult("ages_readable", WARN if share > 0.10 else PASS, detail)


def check_vocabulary(expression: pd.DataFrame) -> CheckResult:
    written = expression[expression["tissue_raw"].notna() | expression["cell_type_raw"].notna()]
    if not len(written):
        return CheckResult("vocabulary_coverage", WARN, "No in-scope sample reports a tissue or cell type.")
    mapped = written["tissue"].notna() | written["cell_type"].notna() | written["cell_line"].notna()
    share = float(mapped.mean())
    detail = (
        f"{share:.0%} of samples with a written tissue or cell type map to the controlled vocabulary "
        f"({int(mapped.sum())} of {len(written)})."
    )
    return CheckResult("vocabulary_coverage", WARN if share < 0.80 else PASS, detail)


def run_checks(
    *,
    search_count: int,
    retrieved: int,
    max_studies: int,
    missing_summary_keys: list[str],
    duplicate_studies: list[str],
    conflicting_samples: list[str],
    studies: pd.DataFrame,
    samples: pd.DataFrame,
    links: pd.DataFrame,
    fetch_errors: pd.DataFrame,
    quality: pd.DataFrame,
) -> list[CheckResult]:
    in_scope = set(studies.loc[studies["in_scope"], "gse"])
    scope_gsm = set(links.loc[links["gse"].isin(in_scope), "gsm"]) if len(links) else set()
    expression = samples[samples["gsm"].isin(scope_gsm) & samples["is_expression"].astype(bool)]
    results = [
        check_search(search_count, retrieved, max_studies),
        check_summary_schema(missing_summary_keys),
        check_unique_studies(duplicate_studies),
        check_fetch_errors(fetch_errors),
        check_sample_counts(studies, links),
        check_links(links, studies, samples),
        check_conflicting_samples(conflicting_samples),
        check_scores(quality),
        check_age_parsing(expression),
        check_vocabulary(expression),
    ]
    return results


def checks_frame(results: list[CheckResult]) -> pd.DataFrame:
    return pd.DataFrame([r.as_row() for r in results], columns=["check_name", "status", "detail"])


def worst_status(results: list[CheckResult]) -> str:
    if not results:
        return PASS
    return min((r.status for r in results), key=lambda s: STATUS_ORDER[s])
