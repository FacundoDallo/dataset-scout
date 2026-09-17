"""Score how usable each study is for the question in the configuration.

The score is transparent on purpose: four components between 0 and 1,
weighted by the configuration, times 100.

- metadata_completeness: share of samples with age, sex, tissue or cell type,
  and strain or genotype recorded (mean of the four shares)
- design_fit: 1.0 if there are at least `min_per_group` young AND old baseline
  samples, 0.5 if both groups exist but are smaller, 0 otherwise.
  Baseline = not a treated sample and not a non-wild-type genotype, so a study
  comparing young wild-type mice with old transgenic mice is not mistaken for a
  clean aging design. Samples that are single cells are not counted as replicates:
  three hundred cells of one mouse are not three hundred animals.
- target_fit: how much of the study is made of the material the question asks
  about (`target.cell_types` and `target.tissues`). Isolated microglia answer a
  microglia question; whole brain tissue only contains them.
- data_type_fit: from the configuration (e.g. bulk RNA-seq 1.0, microarray 0.6)
- traceability: 0.5 for a linked publication + 0.5 for raw data being referenced

Samples of an organism outside `scope.organisms` are left out of every count:
the human samples of a mixed series do not answer a question about mice.

Weights and thresholds are judgment calls; they live in the YAML so they can
be discussed and tuned with the scientists who will use the ranking.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from .config import ScoutConfig
from .text import clean_words

FLAG_DESCRIPTIONS: dict[str, str] = {
    "no_samples_retrieved": "Sample metadata could not be retrieved",
    "no_age": "No sample reports an age",
    "age_inferred": "Some ages were read from titles or group labels, not from an age field",
    "qualitative_age": "Some ages are only words such as 'young' or 'aged'",
    "age_unit_missing": "Some ages are numbers without a unit",
    "age_unreadable": "Some age values could not be interpreted",
    "sex_not_reported": "No sample reports sex",
    "single_sex": "Only one sex is represented",
    "pooled_samples": "Samples are pools of animals or donors",
    "cell_line": "Uses cell lines",
    "ipsc_or_organoid": "Uses iPSC-derived cells or organoids",
    "off_target_material": "Material is mostly not what the question asks about",
    "single_cell": "Single-cell or single-nucleus data",
    "single_cell_from_text": "Single-cell inferred from the summary text only",
    "under_replicated": "Young and old groups exist but are below the minimum size",
    "groups_are_cells": "Young and old counts come from single cells, not from animals",
    "genotype_mixed": "Includes non-wild-type genotypes",
    "treatment_present": "Includes treated samples",
    "no_publication": "No linked publication",
    "mixed_organisms": "More than one organism",
    "other_organism_samples": "Some samples are from an organism out of scope and were not counted",
    "multi_assay": "Combines several assay types",
    "few_samples": "Fewer than 4 expression samples",
    "non_expression_samples": "Some samples are not expression profiles",
}

# Short labels for tables where the full description does not fit.
FLAG_LABELS: dict[str, str] = {
    "no_samples_retrieved": "metadata unavailable",
    "no_age": "no ages",
    "age_inferred": "ages read from titles",
    "qualitative_age": "ages as words",
    "age_unit_missing": "age units missing",
    "age_unreadable": "unreadable ages",
    "sex_not_reported": "sex not stated",
    "single_sex": "one sex only",
    "pooled_samples": "pooled samples",
    "cell_line": "cell line",
    "ipsc_or_organoid": "iPSC or organoid",
    "off_target_material": "off-target material",
    "single_cell": "single-cell",
    "single_cell_from_text": "single-cell from text",
    "under_replicated": "small groups",
    "groups_are_cells": "groups are cells",
    "genotype_mixed": "mutant genotypes",
    "treatment_present": "treated samples",
    "no_publication": "no linked paper",
    "mixed_organisms": "several organisms",
    "other_organism_samples": "samples of another organism",
    "multi_assay": "several assays",
    "few_samples": "under 4 samples",
    "non_expression_samples": "non-expression samples",
}

QUALITY_COLUMNS = [
    "gse",
    "n_samples",
    "pct_age",
    "pct_sex",
    "pct_tissue_or_cell",
    "pct_strain_or_genotype",
    "metadata_completeness",
    "n_young",
    "n_old",
    "has_age_contrast",
    "age_min_months",
    "age_max_months",
    "sexes",
    "design_fit",
    "target_fit",
    "data_type_fit",
    "traceability",
    "score",
    "tier",
    "flags",
]

TIER_READY = "ready"
TIER_USABLE = "usable with curation"
TIER_LOW = "not recommended"


def _share(mask: pd.Series) -> float:
    return float(mask.mean()) if len(mask) else 0.0


def _baseline_mask(group: pd.DataFrame) -> pd.Series:
    """Samples that are not treated and not a non-wild-type genotype (unknown counts as baseline)."""
    genotype_ok = group["genotype_is_control"].astype("boolean").fillna(True)
    treatment_ok = group["treatment_is_control"].astype("boolean").fillna(True)
    return (genotype_ok & treatment_ok).astype(bool)


def _target_fit(expression: pd.DataFrame, study: pd.Series, config: ScoutConfig) -> float:
    """Mean share of the target material across the samples of a study (0 to 1).

    Isolated target cells are the material the question asks about; cultured cells drift
    away from it; single-cell data of a tissue that contains the target can be filtered
    down to it; whole tissue only dilutes it. A configuration without `target.cell_types`
    does not judge the material, so every sample counts fully.
    """
    targets = set(config.target_cell_types)
    if not targets or not len(expression):
        return 1.0
    value = config.target_fit
    cell_type = expression["cell_type"].fillna("").astype(str).str.lower()
    tissue = expression["tissue"].fillna("").astype(str).str.lower()
    sample_type = expression["sample_type"].fillna("").astype(str)
    is_target = cell_type.isin(targets)
    other_cell_type = (cell_type != "") & ~is_target
    cultured = sample_type == "primary culture"
    artificial = sample_type.isin(["cell line", "iPSC-derived", "organoid"])
    in_target_tissue = tissue.isin(set(config.target_tissues))
    single_cell = "single_cell" in str(study["data_type_flags"])

    fit = pd.Series(value["off_target"], index=expression.index, dtype=float)
    # Whole tissue that contains the target: the signal is there, diluted by every other cell.
    fit[in_target_tissue & ~other_cell_type] = (
        value["single_cell_of_target_tissue"] if single_cell else value["bulk_target_tissue"]
    )
    fit[is_target & cultured] = value["cultured_target_cells"]
    fit[is_target & ~cultured & ~artificial] = value["isolated_target_cells"]
    fit[artificial] = value["off_target"]
    return float(fit.mean())


def _tier(score: float, config: ScoutConfig) -> str:
    if score >= config.ready_threshold:
        return TIER_READY
    if score >= config.usable_threshold:
        return TIER_USABLE
    return TIER_LOW


def score_study(study: pd.Series, group: pd.DataFrame, config: ScoutConfig) -> dict[str, Any]:
    weights = config.weights
    k = config.min_per_group
    flags: list[str] = []
    all_samples = len(group)
    expression = group[group["is_expression"].astype(bool)] if all_samples else group
    if all_samples and len(expression) < all_samples:
        flags.append("non_expression_samples")
    if len(expression) and config.scope_organisms:
        # A question about mice is not answered by the human samples of a mixed series.
        organism_in_scope = expression["organism"].isna() | expression["organism"].isin(config.scope_organisms)
        if not organism_in_scope.all():
            flags.append("other_organism_samples")
            expression = expression[organism_in_scope]
    n = len(expression)

    if n == 0:
        pct_age = pct_sex = pct_bio = pct_background = 0.0
        n_young = n_old = n_young_cells = n_old_cells = 0
        sexes: set[str] = set()
        flags.append("no_samples_retrieved")
        age_min = age_max = None
    else:
        has_age = expression["age_group"].notna() | expression["age_months"].notna()
        has_sex = expression["sex"].isin(["male", "female", "mixed"])
        has_bio = expression["tissue"].notna() | expression["cell_type"].notna() | expression["cell_line"].notna()
        has_background = expression["strain"].notna() | expression["genotype_raw"].notna()
        pct_age, pct_sex = _share(has_age), _share(has_sex)
        pct_bio, pct_background = _share(has_bio), _share(has_background)

        baseline = expression[_baseline_mask(expression)]
        # One cell is not one animal: a plate of 300 cells from one mouse is one replicate.
        animals = baseline[~baseline["is_cell_level"].astype("boolean").fillna(False).astype(bool)]
        n_young = int((animals["age_group"] == "young").sum())
        n_old = int((animals["age_group"] == "old").sum())
        n_young_cells = int((baseline["age_group"] == "young").sum())
        n_old_cells = int((baseline["age_group"] == "old").sum())
        sexes = set(expression["sex"].dropna())
        months = expression["age_months"].dropna()
        age_min = float(months.min()) if len(months) else None
        age_max = float(months.max()) if len(months) else None

        flags_age = expression["age_flags"].fillna("")
        rules = expression["age_rule"].fillna("")
        if pct_age == 0:
            flags.append("no_age")
        if (expression["age_source"] == "fallback").any():
            flags.append("age_inferred")
        if rules.str.contains("qualitative").any():
            flags.append("qualitative_age")
        if flags_age.str.contains("unit_missing").any():
            flags.append("age_unit_missing")
        if (rules == "unparsed").any():
            flags.append("age_unreadable")
        if pct_sex == 0:
            flags.append("sex_not_reported")
        if sexes in ({"male"}, {"female"}):
            flags.append("single_sex")
        if expression["is_pooled"].astype(bool).any():
            flags.append("pooled_samples")
        types = set(expression["sample_type"].dropna())
        if "cell line" in types:
            flags.append("cell_line")
        if types & {"iPSC-derived", "organoid"}:
            flags.append("ipsc_or_organoid")
        if (expression["genotype_is_control"].astype("boolean") == False).any():  # noqa: E712
            flags.append("genotype_mixed")
        if (expression["treatment_is_control"].astype("boolean") == False).any():  # noqa: E712
            flags.append("treatment_present")
        if n < 4:
            flags.append("few_samples")

    if n_young >= k and n_old >= k:
        design_fit = 1.0
    elif n_young_cells >= 1 and n_old_cells >= 1:
        design_fit = 0.5
        counted_cells = n_young_cells > n_young or n_old_cells > n_old
        flags.append("groups_are_cells" if counted_cells else "under_replicated")
    else:
        design_fit = 0.0

    target_fit = _target_fit(expression, study, config) if n else 0.0
    if n and config.target_cell_types and target_fit < 0.5:
        flags.append("off_target_material")

    if "single_cell" in study["data_type_flags"]:
        flags.append("single_cell")
    if "single_cell_from_text" in study["data_type_flags"]:
        flags.append("single_cell_from_text")
    if "multi_assay" in study["data_type_flags"]:
        flags.append("multi_assay")
    if not study["pubmed_ids"]:
        flags.append("no_publication")
    if ";" in str(study["organism"]):
        flags.append("mixed_organisms")

    completeness = (pct_age + pct_sex + pct_bio + pct_background) / 4
    data_type_fit = config.data_type_fit.get(study["data_type"], 0.0)
    traceability = 0.5 * bool(study["pubmed_ids"]) + 0.5 * bool(study["bioproject"] or study["supplementary"])
    score = 100 * (
        weights["metadata_completeness"] * completeness
        + weights["design_fit"] * design_fit
        + weights["target_fit"] * target_fit
        + weights["data_type_fit"] * data_type_fit
        + weights["traceability"] * traceability
    )
    score = round(score, 1)
    ordered_flags = [f for f in FLAG_DESCRIPTIONS if f in set(flags)]
    return {
        "gse": study["gse"],
        "n_samples": n,
        "pct_age": round(pct_age, 4),
        "pct_sex": round(pct_sex, 4),
        "pct_tissue_or_cell": round(pct_bio, 4),
        "pct_strain_or_genotype": round(pct_background, 4),
        "metadata_completeness": round(completeness, 4),
        "n_young": n_young,
        "n_old": n_old,
        "has_age_contrast": n_young >= k and n_old >= k,
        "age_min_months": age_min,
        "age_max_months": age_max,
        "sexes": ", ".join(sorted(sexes)) if sexes else None,
        "design_fit": design_fit,
        "target_fit": round(target_fit, 4),
        "data_type_fit": data_type_fit,
        "traceability": traceability,
        "score": score,
        "tier": _tier(score, config),
        "flags": ";".join(ordered_flags),
    }


def score_studies(
    studies: pd.DataFrame,
    samples: pd.DataFrame,
    links: pd.DataFrame,
    config: ScoutConfig,
) -> pd.DataFrame:
    in_scope = studies[studies["in_scope"]]
    merged = links.merge(samples, on="gsm", how="inner")
    rows = []
    empty = samples.iloc[0:0]
    grouped = {gse: frame for gse, frame in merged.groupby("gse")}
    for _, study in in_scope.iterrows():
        rows.append(score_study(study, grouped.get(study["gse"], empty), config))
    frame = pd.DataFrame(rows, columns=QUALITY_COLUMNS)
    if len(frame):
        frame = frame.sort_values(["score", "n_samples", "gse"], ascending=[False, False, True])
    return frame.reset_index(drop=True)


def _top_counts(values: pd.Series, limit: int) -> list[dict[str, Any]]:
    counts = values.dropna().astype(str).value_counts()
    return [{"value": str(k), "count": int(v)} for k, v in counts.head(limit).items()]


def _pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def summarize_run(
    studies: pd.DataFrame,
    samples: pd.DataFrame,
    links: pd.DataFrame,
    characteristics: pd.DataFrame,
    quality: pd.DataFrame,
    search_count: int,
    retrieved: int,
    config: ScoutConfig,
) -> dict[str, Any]:
    """Numbers and findings for the report. Every number is computed here, never typed by hand."""
    in_scope_ids = set(studies.loc[studies["in_scope"], "gse"])
    scope_gsm = set(links.loc[links["gse"].isin(in_scope_ids), "gsm"])
    scope_samples = samples[samples["gsm"].isin(scope_gsm)]
    expression = scope_samples[scope_samples["is_expression"].astype(bool)]
    n_expr = len(expression)

    n_in_scope = len(in_scope_ids)
    n_with_age = int((quality["pct_age"] > 0).sum()) if len(quality) else 0
    n_contrast = int(quality["has_age_contrast"].sum()) if len(quality) else 0
    n_usable = int((quality["score"] >= config.usable_threshold).sum()) if len(quality) else 0
    n_ready = int((quality["score"] >= config.ready_threshold).sum()) if len(quality) else 0
    n_contrast_usable = (
        int((quality["has_age_contrast"].astype(bool) & (quality["score"] >= config.usable_threshold)).sum())
        if len(quality)
        else 0
    )

    funnel = [
        {"label": "Matched the search", "count": int(search_count)},
        {"label": "Retrieved for review", "count": int(retrieved)},
        {"label": "In scope", "count": n_in_scope},
        {"label": "Report sample ages", "count": n_with_age},
        {"label": f"Young and old, at least {config.min_per_group} each", "count": n_contrast},
        {"label": f"...and score {config.usable_threshold:g} or more", "count": n_contrast_usable},
    ]

    parsed_age = expression["age_group"].notna() | expression["age_months"].notna()
    explicit_age = parsed_age & (expression["age_source"] == "field")
    inferred_age = parsed_age & (expression["age_source"] == "fallback")
    has_sex = expression["sex"].isin(["male", "female", "mixed"])
    completeness = [
        {"label": "Age, from an age field", "share": _share(explicit_age)},
        {"label": "Age, read from titles or labels", "share": _share(inferred_age)},
        {"label": "Sex", "share": _share(has_sex)},
        {
            "label": "Tissue or cell type",
            "share": _share(
                expression["tissue"].notna() | expression["cell_type"].notna() | expression["cell_line"].notna()
            ),
        },
        {
            "label": "Strain or genotype",
            "share": _share(expression["strain"].notna() | expression["genotype_raw"].notna()),
        },
    ]

    scope_chars = characteristics[characteristics["gsm"].isin(scope_gsm)]
    key_clean = scope_chars["key_raw"].dropna().map(clean_words)
    age_keys = scope_chars.loc[scope_chars["mapped_field"] == "age", "key_raw"].dropna()
    age_values = expression["age_raw"].dropna()
    unreadable = expression[expression["age_rule"].isin(["unparsed", "number_without_unit"])]
    unmapped_tissue = expression[expression["tissue_raw"].notna() & expression["tissue"].isna()]
    unmapped_cell = expression[expression["cell_type_raw"].notna() & expression["cell_type"].isna()]

    top_keys = []
    if len(scope_chars):
        mapping = (
            scope_chars.assign(key_clean=scope_chars["key_raw"].map(lambda k: clean_words(k) if k else None))
            .dropna(subset=["key_clean"])
            .groupby("key_clean")["mapped_field"]
            .agg(lambda s: s.dropna().iloc[0] if s.notna().any() else None)
        )
        for key, count in key_clean.value_counts().head(15).items():
            top_keys.append({"value": key, "count": int(count), "field": mapping.get(key)})

    exclusions = Counter(
        str(reason).split(" (")[0] for reason in studies.loc[~studies["in_scope"], "exclusion_reason"].dropna()
    )

    findings: list[str] = []
    if n_in_scope:
        findings.append(
            f"{n_contrast} of {n_in_scope} in-scope studies ({_pct(n_contrast, n_in_scope)}%) include both young "
            f"and old baseline samples with at least {config.min_per_group} per group."
        )
    if n_expr:
        findings.append(
            f"Age can be read for {_pct(int(parsed_age.sum()), n_expr)}% of {n_expr} expression samples; "
            f"it was written as {age_values.nunique()} different strings under "
            f"{age_keys.map(clean_words).nunique()} different field names."
        )
        findings.append(f"{_pct(int((~has_sex).sum()), n_expr)}% of expression samples do not state sex.")
        cell_line_studies = int(quality["flags"].str.contains("cell_line").sum()) if len(quality) else 0
        if cell_line_studies:
            findings.append(
                f"{cell_line_studies} in-scope {'study uses' if cell_line_studies == 1 else 'studies use'} "
                "cell lines rather than primary tissue or cells, which a keyword search alone does not reveal."
            )
    if len(key_clean):
        findings.append(
            f"Submitters used {key_clean.nunique()} distinct field names across {len(scope_gsm)} samples."
        )

    return {
        "search_count": int(search_count),
        "retrieved": int(retrieved),
        "n_studies": int(len(studies)),
        "n_in_scope": n_in_scope,
        "n_with_age": n_with_age,
        "n_contrast": n_contrast,
        "n_usable": n_usable,
        "n_ready": n_ready,
        "n_contrast_usable": n_contrast_usable,
        "n_samples_scope": int(len(scope_samples)),
        "n_expression_samples": int(n_expr),
        "funnel": funnel,
        "completeness": completeness,
        "distinct_keys": int(key_clean.nunique()),
        "distinct_age_keys": int(age_keys.map(clean_words).nunique()),
        "distinct_age_strings": int(age_values.nunique()),
        "top_keys": top_keys,
        "unreadable_ages": _top_counts(unreadable["age_raw"], 12),
        "unmapped_tissues": _top_counts(unmapped_tissue["tissue_raw"], 10),
        "unmapped_cell_types": _top_counts(unmapped_cell["cell_type_raw"], 10),
        "data_types": _top_counts(studies.loc[studies["in_scope"], "data_type"], 10),
        "sample_types": _top_counts(expression["sample_type"], 10),
        "exclusions": [{"value": k, "count": v} for k, v in exclusions.most_common()],
        "findings": findings,
    }
