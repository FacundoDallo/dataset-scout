"""Command line interface: the `scout` command.

    scout config        CONFIG             show the resolved settings (no internet)
    scout run           CONFIG [options]   search, harmonize, score, load, report
    scout review-sheet  CONFIG [--n 100]   create the blind manual review spreadsheet
    scout validate      CONFIG             compare the filled sheet with the program
    scout report        CONFIG             rebuild the HTML files from the last run
    scout sql           CONFIG "SELECT ..." query the run's database

Exit codes: 0 success, 1 a data check failed, 2 configuration or usage
problem, 3 network problem, 4 a recorded response is missing in replay mode.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

from . import __version__
from .config import ConfigError, ScoutConfig, load_config, ncbi_identity
from .db import DatabaseError, query
from .http import MODES, HttpError, ReplayMissError
from .ncbi import NcbiError, build_search_term

log = logging.getLogger("dataset_scout")

EXIT_OK, EXIT_CHECK_FAILED, EXIT_USAGE, EXIT_NETWORK, EXIT_REPLAY = 0, 1, 2, 3, 4


def load_env_file(path: Path = Path(".env")) -> list[str]:
    """Read KEY=VALUE lines from a local .env file into the environment.

    Handy on Windows, where setting environment variables for every new
    terminal is tedious. Values already set in the environment win. The
    file is listed in .gitignore, so an API key never reaches GitHub.
    """
    loaded: list[str] = []
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _setup_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False


def _load(path: str) -> ScoutConfig:
    return load_config(path)


def cmd_config(args: argparse.Namespace) -> int:
    config = _load(args.config)
    term = build_search_term(config.query_terms, config.organism, config.published_from, config.published_to)
    email, api_key = ncbi_identity()
    print(f"Configuration: {config.source_path}")
    print(f"  name:            {config.name}")
    print(f"  question:        {config.description.strip() or '(no description)'}")
    print(f"  GEO search:      {term}")
    print(f"  max studies:     {config.max_studies}")
    print(f"  organisms:       {', '.join(config.scope_organisms)}")
    print(f"  data types:      {', '.join(config.scope_data_types)}")
    print(f"  min per group:   {config.min_per_group}")
    for organism, t in config.age_groups.items():
        print(
            f"  age groups:      {organism}: young {t.young_min_months:g}-{t.young_max_months:g} months, "
            f"old from {t.old_min_months:g} months"
        )
    print(f"  weights:         {', '.join(f'{k} {v:g}' for k, v in config.weights.items())}")
    print(f"  thresholds:      usable {config.usable_threshold:g}, ready {config.ready_threshold:g}")
    print(f"  snapshot folder: {config.snapshot_dir}")
    print(f"  output folder:   {config.output_dir}")
    print(f"  review folder:   {config.validation_dir}")
    print(f"  fingerprint:     {config.sha256[:12]}")
    print(f"  NCBI e-mail:     {'set' if email else 'NOT SET (set NCBI_EMAIL before a live run)'}")
    print(f"  NCBI API key:    {'set' if api_key else 'not set (optional: allows 10 requests/s instead of 3)'}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    from .pipeline import run_pipeline, with_overrides

    config = with_overrides(_load(args.config), max_studies=args.max_studies, output_dir=args.output_dir)
    email, api_key = ncbi_identity()
    if args.mode != "replay" and not email:
        log.warning(
            "NCBI_EMAIL is not set. NCBI asks tools to identify themselves; "
            "add NCBI_EMAIL=you@example.com to a .env file in this folder."
        )
    result = run_pipeline(config, mode=args.mode, email=email, api_key=api_key)
    s = result.summary
    counts = {status: sum(1 for c in result.checks if c.status == status) for status in ("pass", "warn", "fail")}
    print()
    print(f"Run {result.run_id} finished")
    print(
        f"  studies:  {s['search_count']:,} matched, {s['retrieved']:,} retrieved, "
        f"{s['n_in_scope']:,} in scope, {s['n_usable']:,} usable ({s['n_ready']:,} ready)"
    )
    print(f"  samples:  {s['n_expression_samples']:,} in-scope expression samples harmonized")
    print(f"  checks:   {counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail")
    print(f"  requests: {result.http_stats['downloaded']} downloaded, {result.http_stats['replayed']} replayed")
    print(f"  report:   {result.artifacts.report}")
    print(f"  summary:  {result.artifacts.one_pager}")
    print(f"  database: {result.artifacts.database}")
    if result.failed:
        print("\nAt least one automated check failed. Open the report's 'Automated checks' section.")
        return EXIT_CHECK_FAILED
    return EXIT_OK


def _database_for(config: ScoutConfig) -> Path:
    from .pipeline import DATABASE_NAME

    return config.output_dir / DATABASE_NAME


def cmd_review_sheet(args: argparse.Namespace) -> int:
    from .validation import draw_review_sample, sheet_has_input, sheet_path, write_review_sheet

    config = _load(args.config)
    path = Path(args.out) if args.out else sheet_path(config)
    if sheet_has_input(path) and not args.force:
        print(
            f"{path} already contains reviewed values. It was not overwritten, to protect manual work.\n"
            "Use --out to write a new file, or --force if you really want to replace it."
        )
        return EXIT_USAGE
    frame = draw_review_sample(_database_for(config), n=args.n, seed=args.seed)
    write_review_sheet(frame, path, config, seed=args.seed)
    studies = frame["gse"].nunique()
    print(f"Review sheet written: {path}")
    print(f"  {len(frame)} samples from {studies} studies (seed {args.seed}).")
    print("  Fill in the yellow columns without looking at the program's output, save, then run:")
    print(f"  scout validate {args.config}")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    from .report import rebuild_reports
    from .validation import disagreements_path, results_path, sheet_path

    config = _load(args.config)
    database = _database_for(config)
    if not sheet_path(config).exists():
        print(f"No review sheet at {sheet_path(config)}. Create it with: scout review-sheet {args.config}")
        return EXIT_USAGE
    report, _, validation = rebuild_reports(config, database)
    if not validation:
        print(f"{sheet_path(config)} has no reviewed values yet. Fill in the yellow columns and save the file.")
        return EXIT_USAGE
    print(f"Compared {validation['rows_reviewed']} reviewed samples with the program's output.")
    print(f"  {'field':<14}{'reviewed':>9}{'agreement':>11}   95% interval   wrong missed invented")
    for field in validation["fields"]:
        if not field["n_reviewed"]:
            continue
        print(
            f"  {field['field']:<14}{field['n_reviewed']:>9}{field['accuracy']:>11.0%}   "
            f"{field['ci_low']:.0%}-{field['ci_high']:.0%}{'':>7}"
            f"{field['n_wrong']:>5}{field['n_missed']:>7}{field['n_invented']:>9}"
        )
    if validation["rows_not_in_run"]:
        print(f"  {validation['rows_not_in_run']} reviewed samples are not in this run and were ignored.")
    print(f"  results:        {results_path(config)}")
    print(f"  disagreements:  {disagreements_path(config)}")
    print(f"  report updated: {report}")
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    from .report import rebuild_reports

    config = _load(args.config)
    report, one_pager, _ = rebuild_reports(config, _database_for(config))
    print(f"Report:  {report}")
    print(f"Summary: {one_pager}")
    return EXIT_OK


def cmd_sql(args: argparse.Namespace) -> int:
    config = _load(args.config)
    frame = query(_database_for(config), args.query)
    if args.csv:
        frame.to_csv(args.csv, index=False, encoding="utf-8")
        print(f"{len(frame)} rows written to {args.csv}")
        return EXIT_OK
    with pd.option_context("display.max_rows", args.limit, "display.max_columns", 30, "display.width", 200,
                           "display.max_colwidth", 60):
        print(frame.head(args.limit).to_string(index=False) if len(frame) else "(no rows)")
    if len(frame) > args.limit:
        print(f"... {len(frame) - args.limit} more rows (use --limit or --csv)")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scout",
        description="Find, harmonize and score public gene-expression datasets for a research question.",
    )
    parser.add_argument("--version", action="version", version=f"dataset-scout {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="print more detail while running")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config", help="show the resolved settings of a configuration file")
    p.add_argument("config", help="path to a YAML configuration, e.g. config/microglia_aging.yaml")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("run", help="run the whole pipeline")
    p.add_argument("config", help="path to a YAML configuration")
    p.add_argument(
        "--mode",
        choices=MODES,
        default="auto",
        help="auto: reuse recorded responses and download the rest (default); "
        "live: download everything again; replay: never touch the network",
    )
    p.add_argument(
        "--max-studies", type=int, default=None, help="override limits.max_studies (e.g. 5 for a quick test)"
    )
    p.add_argument("--output-dir", default=None, help="write outputs somewhere else")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("review-sheet", help="create the blind manual review spreadsheet")
    p.add_argument("config")
    p.add_argument("--n", type=int, default=100, help="number of samples to review (default 100)")
    p.add_argument("--seed", type=int, default=20260916, help="random seed, so the draw can be repeated")
    p.add_argument("--out", default=None, help="write the sheet to another path")
    p.add_argument("--force", action="store_true", help="overwrite a sheet that already has reviewed values")
    p.set_defaults(func=cmd_review_sheet)

    p = sub.add_parser("validate", help="compare the filled review sheet with the program's output")
    p.add_argument("config")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("report", help="rebuild report.html and one_pager.html from the last run")
    p.add_argument("config")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("sql", help="run a SQL query against the run's DuckDB database")
    p.add_argument("config")
    p.add_argument("query", help='for example: "SELECT gse, score FROM v_usable_studies"')
    p.add_argument("--limit", type=int, default=40, help="rows to print (default 40)")
    p.add_argument("--csv", default=None, help="write the full result to a CSV file instead of printing")
    p.set_defaults(func=cmd_sql)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    load_env_file()
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ReplayMissError as exc:
        print(f"Replay stopped: {exc}", file=sys.stderr)
        return EXIT_REPLAY
    except (HttpError, NcbiError) as exc:
        print(f"Could not talk to NCBI: {exc}", file=sys.stderr)
        print("Check your internet connection and try again in a few minutes.", file=sys.stderr)
        return EXIT_NETWORK
    except (DatabaseError, FileNotFoundError, ValueError) as exc:
        print(f"Problem: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\nStopped by the user. Responses downloaded so far are kept in the snapshot.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
