# Dataset Scout

**Find, harmonize and score public gene-expression datasets for one research question, with every number traceable.**

Before a discovery team reuses public data, someone has to answer a slow question: *which of these hundreds of GEO studies can actually support my analysis?* Sample metadata is free text. Age shows up as `3 mo`, `12-week-old`, `P60`, `age (months): 20` or just `aged`. Sex is `M`, `male` or missing. A "microglia" study may turn out to be a cell line.

Dataset Scout automates that first pass for one question at a time and shows its work:

1. searches NCBI GEO and records every response, so a run can be replayed offline;
2. harmonizes sample metadata (age in months, age group, sex, tissue, cell type, strain, genotype, treatment), keeping the raw text and the rule behind every value;
3. scores each study for the question with a transparent, configurable formula;
4. runs automated data checks and loads everything into DuckDB with SQL views;
5. publishes a ranked shortlist, CSV exports, an HTML report and a one-page summary;
6. measures its own accuracy against a **blind manual review** of a random sample.

The demo question: *which public mouse datasets can support a young-versus-old comparison of microglial gene expression?*

## Results of the demo run

<!-- FILL AFTER THE LIVE RUN: copy the numbers from outputs/microglia-aging/run_summary.json and validation/microglia-aging/results.json. Do not round up or estimate. -->

| | |
|---|---|
| GEO series matching the search | _to fill_ |
| In scope (mouse, expression data, not a SuperSeries) | _to fill_ |
| With young and old baseline samples, at least 3 per group | _to fill_ |
| Usable (score 60 or more) | _to fill_ |
| Distinct field names used by submitters | _to fill_ |
| Harmonizer agreement with blind manual review (age group, sex, tissue) | _to fill_ |

Open [`outputs/microglia-aging/report.html`](outputs/microglia-aging/report.html) (download it and open it in a browser) or the [one-page summary](outputs/microglia-aging/one_pager.html).

## How it works

```mermaid
flowchart LR
    Q[Question<br/>YAML config] --> S[Search GEO<br/>esearch]
    S --> M[Study summaries<br/>esummary]
    M --> F{Screen<br/>organism, assay,<br/>SuperSeries}
    F -->|worth a look| X[Sample metadata<br/>SOFT, brief view]
    F -->|out of scope| DB
    X --> H[Harmonize<br/>raw + value + rule]
    H --> C[Classify and score]
    C --> V[Automated checks]
    V --> DB[(DuckDB<br/>tables + views)]
    DB --> R[Report, one-pager,<br/>CSV, manifest]
    DB --> B[Blind review sheet]
    B -->|filled by a person| A[Accuracy per field<br/>95% intervals]
    A --> R
    S -.-> SN[(Snapshot of every<br/>NCBI response)]
    X -.-> SN
```

| Stage | What it does | Where |
|---|---|---|
| Extract | NCBI E-utilities and GEO `acc.cgi`, rate-limited, retried, recorded | `http.py`, `ncbi.py` |
| Parse | SOFT text into samples; two-colour reference channels ignored | `soft.py` |
| Harmonize | Rules and a controlled vocabulary (UBERON, CL identifiers) | `harmonize/` |
| Score | Completeness, design fit, data type fit, traceability | `quality.py` |
| Validate | 10 automated checks; blind manual review with Wilson intervals | `checks.py`, `validation.py` |
| Load | Fresh DuckDB file per run, schema plus SQL views | `sql/`, `db.py` |
| Publish | Self-contained HTML report and A4 summary, CSVs, JSON manifest | `report.py`, `templates/` |

Design choices and how this would scale in a client environment are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

Requires Python 3.10 or newer.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"

# Run it live (NCBI asks for a contact e-mail; put it in a .env file, see .env.example)
scout run config/microglia_aging.yaml --max-studies 5    # quick test
scout run config/microglia_aging.yaml                    # full run

# Replay a run offline from the responses it recorded in snapshots/
scout run config/microglia_aging.yaml --mode replay
```

The demo run's recorded responses are not in this repository: they are large and include submitters' contact details.

Other commands:

```bash
scout config config/microglia_aging.yaml          # show resolved settings and the GEO query
scout sql config/microglia_aging.yaml "SELECT gse, score, tier FROM v_usable_studies"
scout review-sheet config/microglia_aging.yaml    # draw 100 samples for blind manual review
scout validate config/microglia_aging.yaml        # compare the filled sheet with the program
scout report config/microglia_aging.yaml          # rebuild the HTML files
pytest                                            # 131 tests, no internet needed
```

With Docker:

```bash
docker build -t dataset-scout .
docker run --rm -v "${PWD}/snapshots:/app/snapshots" dataset-scout run config/microglia_aging.yaml --mode replay
```

A new question is a new YAML file: copy `config/microglia_aging.yaml`, change the query, scope, age thresholds and weights.

## Outputs

| File | Content |
|---|---|
| `report.html` | Screening cascade, shortlist, why studies fell out, metadata completeness, curator inbox, accuracy, checks, method, lineage |
| `one_pager.html` | The same story on one A4 page (print to PDF from the browser) |
| `studies_ranked.csv` | In-scope studies with score, tier, group sizes and flags |
| `samples_harmonized.csv` | One row per sample: raw text, harmonized value and rule for each field |
| `needs_review.csv` | Values the harmonizer refused to guess |
| `data_checks.csv` | Results of the automated checks |
| `run_summary.json` | Every number in the report plus lineage: config fingerprint, snapshot, versions |
| `dataset_scout.duckdb` | All tables and views (rebuilt on each run, not committed) |

SQL views: `v_study_overview`, `v_usable_studies`, `v_age_contrast_studies`, `v_field_completeness`, `v_needs_review`, `v_in_scope_samples`.

## Scoring

Each in-scope study gets four components between 0 and 1; the score is their weighted sum times 100. Weights and thresholds are in the YAML file.

| Component | Default weight | Definition |
|---|---|---|
| Metadata completeness | 40% | Mean share of samples with age, sex, tissue or cell type, strain or genotype |
| Design fit | 30% | 1 if at least 3 young and 3 old *baseline* samples; 0.5 if both groups exist but are smaller |
| Data type fit | 15% | Bulk RNA-seq 1.0, single-cell 0.8, microarray 0.6 |
| Traceability | 15% | 0.5 for a linked paper, 0.5 for referenced raw data |

Baseline means untreated and wild type, so "young wild-type versus old transgenic" is not mistaken for an aging design. Mouse age groups: young 1.5 to 6 months, old 18 months or more.

## Principles

- **Never guess silently.** A bare `20` is not converted unless the field name gives the unit. Unreadable values go to a curator inbox instead of being filled in. Ages read from titles are flagged as inferred.
- **Everything is traceable.** Raw text, harmonized value and rule sit side by side; each run records its configuration fingerprint and every NCBI response.
- **Measure the tool, not just the data.** Blind manual review: the reviewer does not see the program's answers, to avoid anchoring.
- **Idempotent runs.** The database is rebuilt from scratch; the same snapshot and configuration give the same tables.

## Limits

- Only metadata is assessed; expression values, batch effects and sequencing quality are not.
- Rule-based harmonization favours precision over coverage.
- Ontology identifiers were entered by hand and should be confirmed with the EBI Ontology Lookup Service.
- GEO's search decides what is found; studies described with other words are missed.

## Related work

[MetaSRA](https://metasra.biostat.wisc.edu/) and [refine.bio](https://www.refine.bio/) already normalize public sample metadata at scale, and [GEOparse](https://github.com/guma44/GEOparse), [GEOquery](https://bioconductor.org/packages/GEOquery/) and [geofetch](https://github.com/pepkit/geofetch) download GEO records. Dataset Scout does not replace them. It is a question-first triage step: transparent scoring for one analysis, automated checks, and a measured error rate.

## About

Built by Facundo Dallo (biologist, FCEyN-UBA) as a portfolio project, with Claude Code as a pair programmer. The question, scientific decisions, manual validation and review of every file are mine. Public data only. MIT licence.
