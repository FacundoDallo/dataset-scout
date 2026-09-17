# Architecture and design decisions

## Data model

```
runs ── run_settings ── data_checks ── validation_results      (about the build)

studies 1 ──< study_samples >── 1 samples 1 ──< sample_characteristics
   │
   └── 1:1 study_quality                                         (about the biology)
```

- A sample can belong to several series (SuperSeries and SubSeries share samples), so `study_samples` is a link table.
- `sample_characteristics` keeps every submitted key/value pair, untouched, with its channel and position. Harmonized fields in `samples` can always be traced back to it.
- `samples` stores, for each field, the raw text, the value and the rule (`age_raw`, `age_months`, `age_rule`, `age_source`, ...).

## Why these tools

| Choice | Reason | In a client environment |
|---|---|---|
| Python + pandas | Readable transformations, strong ecosystem | Same code on Databricks, or PySpark for scale |
| DuckDB | Analytical SQL in one file, no server | Delta tables on Databricks, Snowflake or Postgres |
| YAML configuration | Scientists can review assumptions without code | Versioned configs per study question or client |
| Record and replay | Reproducible runs, offline tests, audit trail | Raw zone in object storage (S3/ADLS) with immutable versions |
| Jinja2 HTML | Self-contained report, prints to PDF | Dashboard (Power BI, Streamlit) on top of the same views |
| pytest + GitHub Actions | Every change is tested on Windows and Linux | Same pattern with the client's CI |
| Docker | Same environment everywhere | Container images run by Nextflow, Airflow or Databricks jobs |

## Extract

- NCBI E-utilities `esearch` and `esummary` on the `gds` database, then GEO `acc.cgi` with `targ=gsm&view=brief&form=text`, which returns the metadata of every sample in a series without data tables.
- Series UIDs in GEO DataSets are 200000000 plus the GSE number.
- The client keeps under 3 requests per second (10 with an API key), retries 429 and 5xx with exponential backoff, and identifies itself with `tool` and `email`.
- Every response, including permanent failures such as 404, is stored with its URL, time and SHA-256. `--mode replay` never touches the network, so a replayed run fails and succeeds in the same places as the original.
- Study summaries are checked for schema drift: if NCBI drops a field, the run warns instead of producing empty columns silently.

## Transform

- Studies are screened before their samples are downloaded (organism, assay, SuperSeries). Bulk and single-cell sequencing are only told apart after reading the samples: their library source, or per-cell quality fields such as `nGene` and `nUMI`, which plate-based studies report for every cell.
- Keys are mapped to standard fields by ordered patterns (`age group` is age before it is group; `cell type` before `tissue`). A key named only `stage` is not read as age, because it also holds treatment stages.
- Ages: explicit units, ranges (midpoint, flagged), embryonic and postnatal days, unit from the field name or from a separate unit field (`age_unit: months`), years assumed only for humans (flagged), qualitative words. A written but unreadable age is reported, never replaced by a guess.
- Titles and source names are a fallback for age and sex only when no field exists. Other unmapped fields are searched only for an explicit age such as `6 months old`. Age words next to serum, plasma or a transfer (`young serum infusion`) are not read as the animal's age. Fallback values are flagged as inferred.
- A genotype that is only a background strain (`C57BL/6J`) or a wild-type allele (`Trem2+/+`) counts as wild type. A treatment made only of control words (`None/naïve`, `CTR`) counts as untreated.
- How cells were obtained (`selection marker: CD11b+`, `time: 5 days in vitro`) is also read from unmapped fields to set the sample type.
- Vocabulary matching is whole-word and longest-synonym-first. Ambiguous abbreviations are left out: a miss is visible in the curator inbox, a wrong match is not.
- Channel 2 of two-colour arrays describes the reference sample and is ignored.

## Validate

Automated checks run on every build and are stored in `data_checks`:

| Check | Fails or warns when |
|---|---|
| search_returned_studies | nothing matched (fail); more matched than the retrieval limit (warn) |
| summary_schema | expected NCBI fields disappeared |
| unique_study_ids | a series appeared twice |
| sample_metadata_retrieved | a series' samples could not be downloaded |
| sample_counts_match | GEO's sample count differs from what was parsed |
| links_are_consistent | a link points to an unknown study or sample (fail) |
| sample_records_consistent | two series describe the same sample differently |
| scores_in_range | a score is outside 0-100 (fail) |
| ages_readable | more than 10% of written ages could not be converted |
| vocabulary_coverage | less than 80% of written tissues or cell types map to the vocabulary |

`scout run` exits with code 1 when any check fails, so CI stops.

The blind manual review (`validation.py`) measures what automated checks cannot: whether the values are right. Outcomes are split into wrong, missed and invented, because they have different fixes (a parser bug, a missing synonym, an over-eager rule).

## Tests

- Unit tests for every harmonizer rule, including the edge cases above.
- Real GEO records as fixtures (CRLF line endings, a data table, a two-colour array).
- A fake NCBI with eight synthetic studies, each planting one real-world problem, drives end-to-end tests: scope decisions, ranking, checks, curator inbox, replay equality, HTML escaping of submitter text, and a reconciliation test where SQL views and the Python summary must agree.

## Scaling this up

For a portfolio of questions or a client's internal data:

1. **Storage:** land raw responses in object storage (bronze), harmonized tables as Delta (silver), scored views per question (gold).
2. **Orchestration:** one job per question on a schedule (Databricks Workflows or Airflow), with the snapshot as the raw layer; Nextflow if heavier processing of expression data follows.
3. **Harmonization:** keep the rules as the precise first pass, add ontology lookups (EBI OLS) and a reviewed LLM-assisted step for the long tail in the curator inbox, measured with the same blind review.
4. **Governance:** FAIR identifiers, lineage from the manifest, access control, and GxP-style change control for the vocabulary and scoring weights.
5. **Beyond aging:** new design types (disease versus control, treated versus vehicle) reuse the same pipeline with a different design-fit function.
