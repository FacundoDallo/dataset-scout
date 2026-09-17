# Working on Dataset Scout

## Who you are working with

Facundo is a biologist learning data engineering by watching you work. Talk to him in Rioplatense Spanish.
Before each command, say in one line what it does and why. Never skip a step because it seems obvious.
He approves every change: propose, explain, then act. Code, comments and docs stay in English.

## What this project is

A Python pipeline that searches NCBI GEO for one research question (defined in a YAML file under `config/`),
harmonizes free-text sample metadata, scores each study, runs automated checks, loads DuckDB and writes an
HTML report. Read `README.md` and `docs/ARCHITECTURE.md` first.

The code was written without internet access and tested only against real GEO fixtures and a fake NCBI
(`tests/fake_ncbi.py`). **The first live run is the real test.** Expect surprises in real metadata.

## Environment (Windows)

- Virtual environment in `.venv`. In Git Bash: `source .venv/Scripts/activate`.
  In PowerShell: `.venv\Scripts\Activate.ps1` (if blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`).
- Install: `python -m pip install -e ".[dev]"`
- Checks: `ruff check src tests` and `pytest` (108 tests, all offline). Both must pass before any commit.
- NCBI identity goes in `.env` (copy `.env.example`). Never print or commit `.env`.

## Commands

```
scout config config/microglia_aging.yaml
scout run config/microglia_aging.yaml --max-studies 5      # smoke test
scout run config/microglia_aging.yaml                      # full live run (records snapshots)
scout run config/microglia_aging.yaml --mode replay        # offline, must reproduce the same numbers
scout review-sheet config/microglia_aging.yaml
scout validate config/microglia_aging.yaml
scout report config/microglia_aging.yaml
scout sql config/microglia_aging.yaml "SELECT ..."
```

## Rules

1. **Never invent or round numbers** in the README, report or messages. Every figure comes from
   `outputs/<run>/run_summary.json` or `validation/<run>/results.json`.
2. **Never edit or regenerate `validation/*/review_sheet.xlsx`** once it has values. It is Facundo's manual work.
   Never fill it in yourself, and never show him the program's answers for those samples before he finishes.
3. **Tune first, measure once.** Improve rules and vocabulary using `needs_review.csv` and the report's field-name
   table *before* the blind review. If rules change after the review, report the original agreement, and measure
   again on a fresh sheet (`scout review-sheet ... --seed <new> --out validation/microglia-aging/review_sheet_2.xlsx`)
   rather than tuning on the reviewed samples.
4. Every rule change gets a test in `tests/` that fails before the change and passes after.
5. Prefer leaving a value empty over guessing. Ambiguous synonyms stay out of the vocabulary.
6. Do not add dependencies without asking. Do not change scoring weights without Facundo's decision.
7. Keep commits small, with messages that say why.

## Session plan

1. Setup: venv, install, `pytest`, `ruff`. Create `.env`.
2. `scout config`, then the smoke test with `--max-studies 5`. Open the raw snapshot of one esummary response and
   one SOFT response with Facundo and compare them with what the parser expects (`studies.py`, `soft.py`).
   Fix any mismatch with a test.
3. Full live run. Open `report.html`. Walk through the curator inbox and the "most common field names" table.
   Propose vocabulary or rule improvements; implement the ones he approves, with tests; re-run with `--mode auto`
   (uses the snapshot, so no new downloads).
4. Check the snapshot size (`du -sh snapshots`). If it is under 50 MB it will be committed.
5. `scout review-sheet`. Facundo reviews the samples himself (60 to 90 minutes). Then `scout validate`.
6. Fill the "Results of the demo run" table in `README.md` from the JSON files. Update `report.project_url` in the
   config after the GitHub repository exists, then `scout report`.
7. Git: `git init`, first commit, create the public GitHub repository (Facundo does it in the browser),
   `git remote add origin ...`, `git push -u origin main`. Confirm the Actions run is green on Linux and Windows.
8. Replay check: `scout run config/microglia_aging.yaml --mode replay` must print the same numbers.
