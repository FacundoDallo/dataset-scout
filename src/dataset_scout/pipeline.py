"""Run the whole pipeline for one configuration.

    search GEO ─► study summaries ─► screen ─► sample metadata ─► harmonize
        ─► classify and scope ─► score ─► checks ─► DuckDB ─► CSV + report

Steps in plain words:

1. Extract: ask NCBI which series match the query, then download the
   study summaries and, for studies worth a closer look, the sample metadata.
   Every response is recorded in the snapshot folder.
2. Screen: skip SuperSeries and series from other organisms or assays
   before downloading their samples (fewer requests, same answer).
3. Harmonize: turn each sample's free text into standard fields, keeping
   the raw text and the rule used next to every value.
4. Score: rate each in-scope study for the question in the configuration.
5. Validate: run automated checks on the build (see checks.py).
6. Load: write everything to a fresh DuckDB file with SQL views on top.
7. Publish: CSV exports, an HTML report, a one-page summary and a JSON
   manifest with the lineage of the run.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import pandas as pd

from . import __version__
from .checks import FAIL, CheckResult, checks_frame, run_checks, worst_status
from .config import ScoutConfig
from .db import build_database
from .db import query as db_query
from .harmonize.assay import classify_data_type
from .harmonize.samples import SAMPLE_COLUMNS, HarmonizeOptions, harmonize_sample
from .harmonize.vocabulary import Vocabulary
from .http import HttpError, RecordingClient
from .ncbi import NcbiError, NcbiGeoClient, build_search_term
from .quality import QUALITY_COLUMNS, score_studies, summarize_run
from .report import write_reports
from .soft import parse_soft_samples
from .studies import Study, assess_scope, check_summary_schema, parse_study
from .validation import refresh_validation

log = logging.getLogger(__name__)

SEQUENCING_TYPES = ("bulk RNA-seq", "single-cell RNA-seq")
DATABASE_NAME = "dataset_scout.duckdb"

STUDY_COLUMNS = [
    "gse",
    "uid",
    "title",
    "summary",
    "organism",
    "gds_type",
    "data_type",
    "data_type_flags",
    "platform",
    "n_samples_reported",
    "published",
    "pubmed_ids",
    "bioproject",
    "supplementary",
    "is_superseries",
    "samples_fetched",
    "in_scope",
    "exclusion_reason",
    "fetch_error",
    "geo_url",
]
LINK_COLUMNS = ["gse", "gsm"]
CHARACTERISTIC_COLUMNS = ["gsm", "channel", "position", "key_raw", "value_raw", "mapped_field"]
FETCH_ERROR_COLUMNS = ["gse", "step", "message"]


@dataclass
class RunArtifacts:
    """Where the outputs of a run were written."""

    output_dir: Path
    database: Path
    report: Path | None = None
    one_pager: Path | None = None
    manifest: Path | None = None
    exports: dict[str, Path] = field(default_factory=dict)


@dataclass
class RunResult:
    run_id: str
    summary: dict[str, Any]
    checks: list[CheckResult]
    artifacts: RunArtifacts
    http_stats: dict[str, int]

    @property
    def status(self) -> str:
        return worst_status(self.checks)

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _package_versions() -> dict[str, str]:
    versions = {}
    for name in ("pandas", "duckdb", "requests", "PyYAML", "Jinja2", "openpyxl"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "not installed"
    return versions


def study_row(study: Study, samples_fetched: bool) -> dict[str, Any]:
    return {
        "gse": study.gse,
        "uid": study.uid,
        "title": study.title,
        "summary": study.summary,
        "organism": study.organism,
        "gds_type": study.gds_type,
        "data_type": study.data_type,
        "data_type_flags": ";".join(study.data_type_flags),
        "platform": study.platform,
        "n_samples_reported": study.n_samples_reported,
        "published": study.published,
        "pubmed_ids": ";".join(study.pubmed_ids),
        "bioproject": study.bioproject,
        "supplementary": study.supplementary,
        "is_superseries": study.is_superseries,
        "samples_fetched": samples_fetched,
        "in_scope": study.in_scope,
        "exclusion_reason": study.exclusion_reason,
        "fetch_error": study.fetch_error,
        "geo_url": study.geo_url,
    }


def screening_types(config: ScoutConfig) -> tuple[str, ...]:
    """Data types worth downloading samples for.

    Bulk and single-cell sequencing can only be told apart reliably after
    reading the samples' library descriptions, so if either is in scope,
    both are downloaded and the final decision is made afterwards.
    """
    types = set(config.scope_data_types)
    if types & set(SEQUENCING_TYPES):
        types |= set(SEQUENCING_TYPES)
    return tuple(sorted(types))


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


class Pipeline:
    """One run of Dataset Scout. Create it, call `run()`, read the result."""

    def __init__(
        self,
        config: ScoutConfig,
        mode: str = "auto",
        email: str | None = None,
        api_key: str | None = None,
        http_session: Any | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self.mode = mode
        self.clock = clock
        self.vocab = Vocabulary.load(config.vocabulary_path)
        client_kwargs: dict[str, Any] = {"mode": mode, "session": http_session}
        if sleep is not None:
            client_kwargs["sleep"] = sleep
        self.http = RecordingClient(config.snapshot_dir, **client_kwargs)
        self.ncbi = NcbiGeoClient(self.http, email=email, api_key=api_key)
        self.options = HarmonizeOptions(age_fallback=config.age_fallback, sex_fallback=config.sex_fallback)

    # ------------------------------------------------------------------ extract
    def _search(self) -> tuple[str, int, list[dict[str, Any]]]:
        cfg = self.config
        term = build_search_term(cfg.query_terms, cfg.organism, cfg.published_from, cfg.published_to)
        log.info("Searching GEO: %s", term)
        result = self.ncbi.search_series(term, retmax=cfg.max_studies)
        log.info("  %s series match; retrieving %s", result.count, len(result.uids))
        documents = self.ncbi.summarize_series(result.uids) if result.uids else []
        log.info("  %s study summaries downloaded", len(documents))
        return term, result.count, documents

    def _fetch_samples(self, study: Study) -> tuple[list[Any], str | None]:
        try:
            text = self.ncbi.fetch_series_samples(study.gse, view=self.config.geo_view)
        except (HttpError, NcbiError) as exc:
            return [], str(exc)
        if _looks_like_html(text):
            return [], "GEO answered with a web page instead of SOFT text"
        samples = parse_soft_samples(text)
        if not samples:
            return [], "the SOFT response contained no samples"
        return samples, None

    # --------------------------------------------------------------------- run
    def run(self) -> RunResult:
        cfg = self.config
        started = self.clock()
        run_id = f"{cfg.name}-{started:%Y%m%dT%H%M%SZ}"
        output_dir = cfg.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(output_dir / "run.log", mode="w", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root = logging.getLogger("dataset_scout")
        root.addHandler(file_handler)
        try:
            return self._run(run_id, started, output_dir)
        finally:
            root.removeHandler(file_handler)
            file_handler.close()

    def _run(self, run_id: str, started: datetime, output_dir: Path) -> RunResult:
        cfg = self.config
        log.info("Run %s (mode: %s, config: %s)", run_id, self.mode, cfg.name)
        term, search_count, documents = self._search()
        missing_keys = check_summary_schema(documents)
        if missing_keys:
            log.warning("  NCBI summaries are missing expected fields: %s", ", ".join(missing_keys))

        studies: list[Study] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for document in documents:
            study = parse_study(document)
            if not study.gse.startswith("GSE"):
                log.warning("  skipping a summary without a series accession (uid %s)", study.uid)
                continue
            if study.gse in seen:
                duplicates.append(study.gse)
                continue
            seen.add(study.gse)
            studies.append(study)

        study_rows: list[dict[str, Any]] = []
        samples_by_gsm: dict[str, dict[str, Any]] = {}
        conflicts: list[str] = []
        links: list[dict[str, str]] = []
        characteristic_rows: list[dict[str, Any]] = []
        fetch_errors: list[dict[str, str]] = []
        to_fetch_types = screening_types(cfg)

        for index, study in enumerate(studies, start=1):
            texts = [study.title, study.summary]
            screen = classify_data_type(study.gds_type, texts, self.vocab.single_cell_terms)
            study.data_type, study.data_type_flags = screen.data_type, screen.flags
            worth_fetching, reason = assess_scope(
                study, cfg.scope_organisms, to_fetch_types, cfg.exclude_superseries
            )
            if not worth_fetching:
                study.in_scope, study.exclusion_reason = False, reason
                study_rows.append(study_row(study, samples_fetched=False))
                log.info("  [%s/%s] %s skipped: %s", index, len(studies), study.gse, reason)
                continue

            soft_samples, error = self._fetch_samples(study)
            library_sources: set[str] = set()
            sample_keys: set[str] = set()
            if error:
                study.fetch_error = error
                fetch_errors.append({"gse": study.gse, "step": "sample metadata", "message": error})
                log.warning("  [%s/%s] %s: %s", index, len(studies), study.gse, error)
            for sample in soft_samples:
                row, chars = harmonize_sample(sample, self.vocab, cfg.age_groups, self.options)
                gsm = row["gsm"]
                links.append({"gse": study.gse, "gsm": gsm})
                if row["library_source"]:
                    library_sources.add(row["library_source"])
                sample_keys.update(c["key_raw"] for c in chars if c["key_raw"])
                previous = samples_by_gsm.get(gsm)
                if previous is None:
                    samples_by_gsm[gsm] = row
                    characteristic_rows.extend(chars)
                elif previous != row:
                    conflicts.append(gsm)

            final = classify_data_type(
                study.gds_type,
                texts,
                self.vocab.single_cell_terms,
                library_sources,
                sample_keys,
                self.vocab.single_cell_qc_keys,
            )
            study.data_type, study.data_type_flags = final.data_type, final.flags
            study.in_scope, study.exclusion_reason = assess_scope(
                study, cfg.scope_organisms, cfg.scope_data_types, cfg.exclude_superseries
            )
            study_rows.append(study_row(study, samples_fetched=True))
            state = "in scope" if study.in_scope else f"out of scope: {study.exclusion_reason}"
            log.info(
                "  [%s/%s] %s: %s samples, %s, %s",
                index,
                len(studies),
                study.gse,
                len(soft_samples),
                study.data_type,
                state,
            )

        studies_df = pd.DataFrame(study_rows, columns=STUDY_COLUMNS)
        samples_df = pd.DataFrame(list(samples_by_gsm.values()), columns=list(SAMPLE_COLUMNS))
        links_df = pd.DataFrame(links, columns=LINK_COLUMNS).drop_duplicates(ignore_index=True)
        chars_df = pd.DataFrame(characteristic_rows, columns=CHARACTERISTIC_COLUMNS).drop_duplicates(
            subset=["gsm", "channel", "position"], ignore_index=True
        )
        errors_df = pd.DataFrame(fetch_errors, columns=FETCH_ERROR_COLUMNS)

        log.info("Scoring %s in-scope studies", int(studies_df["in_scope"].sum()) if len(studies_df) else 0)
        quality_df = (
            score_studies(studies_df, samples_df, links_df, cfg)
            if len(studies_df)
            else pd.DataFrame(columns=QUALITY_COLUMNS)
        )

        checks = run_checks(
            search_count=search_count,
            retrieved=len(documents),
            max_studies=cfg.max_studies,
            missing_summary_keys=missing_keys,
            duplicate_studies=sorted(set(duplicates)),
            conflicting_samples=sorted(set(conflicts)),
            studies=studies_df,
            samples=samples_df,
            links=links_df,
            fetch_errors=errors_df,
            quality=quality_df,
        )
        for check in checks:
            level = logging.WARNING if check.status != "pass" else logging.INFO
            log.log(level, "  check %-26s %s: %s", check.name, check.status.upper(), check.detail)

        summary = summarize_run(
            studies_df,
            samples_df,
            links_df,
            chars_df,
            quality_df,
            search_count=search_count,
            retrieved=len(documents),
            config=cfg,
        )

        snapshot_index = self.http.write_index()
        finished = self.clock()
        runs_df = pd.DataFrame(
            [
                {
                    "run_id": run_id,
                    "started_at": started.replace(tzinfo=None),
                    "finished_at": finished.replace(tzinfo=None),
                    "mode": self.mode,
                    "config_name": cfg.name,
                    "config_sha256": cfg.sha256,
                    "search_term": term,
                    "snapshot_dir": cfg.snapshot_dir.as_posix(),
                    "tool_version": __version__,
                    "n_found": search_count,
                    "n_retrieved": len(documents),
                    "is_demo": cfg.is_demo,
                }
            ]
        )
        settings_df = pd.DataFrame(
            [{"key": k, "value": v} for k, v in cfg.settings_rows().items()], columns=["key", "value"]
        )

        tables = {
            "runs": runs_df,
            "run_settings": settings_df,
            "studies": studies_df,
            "samples": samples_df,
            "study_samples": links_df,
            "sample_characteristics": chars_df,
            "study_quality": quality_df,
            "fetch_errors": errors_df,
            "data_checks": checks_frame(checks),
        }
        database = build_database(output_dir / DATABASE_NAME, tables)
        artifacts = RunArtifacts(output_dir=output_dir, database=database)
        artifacts.exports = export_tables(database, output_dir)

        lineage = {
            "run_id": run_id,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "mode": self.mode,
            "tool_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": _package_versions(),
            "config_name": cfg.name,
            "config_file": cfg.source_path.as_posix() if cfg.source_path else None,
            "config_sha256": cfg.sha256,
            "search_term": term,
            "snapshot_dir": cfg.snapshot_dir.as_posix(),
            "snapshot_index": snapshot_index.as_posix() if snapshot_index else None,
            "http": dict(self.http.stats),
            "vocabulary": self.vocab.source,
        }

        validation = refresh_validation(cfg, database)
        artifacts.report, artifacts.one_pager = write_reports(cfg, database, summary, checks, lineage, validation)
        artifacts.manifest = write_manifest(output_dir, summary, checks, lineage, validation, artifacts)
        log.info("Done in %ss. Report: %s", int((finished - started).total_seconds()), artifacts.report)
        return RunResult(run_id, summary, checks, artifacts, dict(self.http.stats))


EXPORT_QUERIES: dict[str, str] = {
    "studies_ranked.csv": """
        SELECT gse, title, data_type, score, tier, n_samples, n_young, n_old, age_min_months,
               age_max_months, sexes, metadata_completeness, sample_types, tissues, cell_types,
               flags, pubmed_ids, geo_url
        FROM v_study_overview
        WHERE in_scope
        ORDER BY score DESC, n_samples DESC, gse
    """,
    "studies_all.csv": """
        SELECT gse, title, organism, data_type, published, in_scope, exclusion_reason, fetch_error,
               score, tier, geo_url
        FROM v_study_overview
        ORDER BY in_scope DESC, score DESC NULLS LAST, gse
    """,
    "samples_harmonized.csv": """
        SELECT s.*
        FROM v_in_scope_samples AS s
        ORDER BY s.gsm
    """,
    "needs_review.csv": """
        SELECT * FROM v_needs_review ORDER BY field, raw_value, gsm
    """,
    "data_checks.csv": """
        SELECT * FROM data_checks
        ORDER BY CASE status WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, check_name
    """,
}


def export_tables(database: Path, output_dir: Path) -> dict[str, Path]:
    """Write the CSV files a reviewer can open without SQL."""
    paths = {}
    for filename, sql in EXPORT_QUERIES.items():
        frame = db_query(database, sql)
        path = output_dir / filename
        frame.to_csv(path, index=False, encoding="utf-8")
        paths[filename] = path
    return paths


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def write_manifest(
    output_dir: Path,
    summary: dict[str, Any],
    checks: list[CheckResult],
    lineage: dict[str, Any],
    validation: dict[str, Any] | None,
    artifacts: RunArtifacts,
) -> Path:
    """run_summary.json: every number in the report plus where it came from."""
    manifest = {
        "lineage": lineage,
        "summary": summary,
        "checks": [dataclasses.asdict(c) for c in checks],
        "validation": validation,
        "outputs": {
            "database": artifacts.database.name,
            "report": artifacts.report.name if artifacts.report else None,
            "one_pager": artifacts.one_pager.name if artifacts.one_pager else None,
            "exports": sorted(artifacts.exports),
        },
    }
    path = output_dir / "run_summary.json"
    path.write_text(json.dumps(manifest, indent=2, default=_json_default), encoding="utf-8")
    return path


def run_pipeline(config: ScoutConfig, mode: str = "auto", **kwargs: Any) -> RunResult:
    """Convenience wrapper: `run_pipeline(load_config(path))`."""
    return Pipeline(config, mode=mode, **kwargs).run()


def with_overrides(
    config: ScoutConfig, max_studies: int | None = None, output_dir: str | Path | None = None
) -> ScoutConfig:
    changes: dict[str, Any] = {}
    if max_studies is not None:
        if max_studies < 1:
            raise ValueError("--max-studies must be at least 1")
        changes["max_studies"] = max_studies
        data = dict(config.data)
        data["limits"] = {**data.get("limits", {}), "max_studies": max_studies}
        changes["data"] = data
    if output_dir is not None:
        changes["output_dir"] = Path(output_dir)
    return dataclasses.replace(config, **changes) if changes else config
