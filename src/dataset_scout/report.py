"""Render the HTML report and the one-page summary.

Both files are self-contained (no external fonts, scripts or images), so
they open offline and can be attached to an e-mail or printed to PDF from
any browser (Ctrl+P, "Save as PDF").

Every number shown comes from the run: the summary computed in quality.py,
the database views, the automated checks and, when available, the blind
manual review. Nothing is typed in by hand.

GEO titles and descriptions are text written by thousands of submitters,
so the templates escape everything by default (Jinja2 autoescape).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from . import __version__
from .checks import CheckResult
from .config import ScoutConfig
from .db import query
from .quality import FLAG_DESCRIPTIONS, FLAG_LABELS, TIER_LOW, TIER_READY, TIER_USABLE

TIER_CLASS = {TIER_READY: "ready", TIER_USABLE: "usable", TIER_LOW: "low"}
FIELD_LABELS = {
    "age_group": "Age group",
    "age_months": "Age in months",
    "sex": "Sex",
    "tissue": "Tissue",
    "cell_type": "Cell type",
    "sample_type": "Sample type",
}


def _pct(value: Any, digits: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    return f"{100 * float(value):.{digits}f}%"


def _num(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    number = float(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,.1f}"


def _months(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    number = float(value)
    return f"{number:.0f}" if abs(number - round(number)) < 0.05 else f"{number:.1f}"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or singular + "s")


def environment() -> Environment:
    env = Environment(
        loader=PackageLoader("dataset_scout", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(pct=_pct, num=_num, months=_months)
    env.globals.update(plural=_plural)
    return env


def _clean(value: Any) -> Any:
    """NaN and pandas NA become None so templates can test them simply."""
    try:
        if value is None or value != value:  # NaN is the only value not equal to itself
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(frame) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row.items()} for row in frame.to_dict("records")]


def _flag_list(flags: str | None) -> list[dict[str, str]]:
    return [
        {"code": f, "label": FLAG_LABELS.get(f, f), "description": FLAG_DESCRIPTIONS.get(f, f)}
        for f in (flags or "").split(";")
        if f
    ]


def _cascade(summary: dict[str, Any]) -> list[dict[str, Any]]:
    steps = summary.get("funnel", [])
    top = max((s["count"] for s in steps), default=0) or 1
    rows, previous = [], None
    for step in steps:
        count = int(step["count"])
        rows.append(
            {
                "label": step["label"],
                "count": count,
                "width": max(0.6, 100 * count / top) if count else 0,
                "dropped": (previous - count) if previous is not None and previous > count else 0,
            }
        )
        previous = count
    return rows


def _headline(summary: dict[str, Any]) -> str:
    usable, ready = summary.get("n_usable", 0), summary.get("n_ready", 0)
    in_scope, found = summary.get("n_in_scope", 0), summary.get("search_count", 0)
    if not found:
        return "The search returned no studies. Broaden the query terms and run again."
    if not in_scope:
        return f"None of the {found} matching studies is in scope for this question."
    if not usable:
        return (
            f"None of the {in_scope} in-scope studies reaches the usable threshold yet. "
            "The sections below show what is missing."
        )
    ready_part = f", {ready} of them ready as submitted" if ready else ", all of them need some curation"
    return (
        f"{usable} of {in_scope} in-scope {_plural(in_scope, 'study', 'studies')} "
        f"{'is' if usable == 1 else 'are'} usable for this question{ready_part}."
    )


def _flag_counts(shortlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in shortlist:
        for flag in row["flag_list"]:
            counts[flag["code"]] = counts.get(flag["code"], 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"label": FLAG_DESCRIPTIONS.get(k, k), "count": v} for k, v in ordered]


def _validation_rows(validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not validation:
        return []
    rows = []
    for field in validation.get("fields", []):
        if not field.get("n_reviewed"):
            continue
        rows.append({**field, "label": FIELD_LABELS.get(field["field"], field["field"])})
    return rows


def build_context(
    config: ScoutConfig,
    database: Path,
    summary: dict[str, Any],
    checks: list[CheckResult],
    lineage: dict[str, Any],
    validation: dict[str, Any] | None,
) -> dict[str, Any]:
    overview = query(
        database,
        """
        SELECT * FROM v_study_overview
        WHERE in_scope
        ORDER BY score DESC, n_samples DESC, gse
        """,
    )
    studies = _records(overview)
    for rank, row in enumerate(studies, start=1):
        row["rank"] = rank
        row["flag_list"] = _flag_list(row.get("flags"))
        row["tier_class"] = TIER_CLASS.get(row.get("tier"), "low")
        row["published_year"] = str(row["published"])[:4] if row.get("published") else None
    usable = [r for r in studies if (r.get("score") or 0) >= config.usable_threshold]
    not_usable = [r for r in studies if (r.get("score") or 0) < config.usable_threshold]
    review_inbox = _records(
        query(
            database,
            """
            SELECT field, reason, raw_value, COUNT(*) AS n
            FROM v_needs_review
            GROUP BY ALL
            ORDER BY n DESC, field, raw_value
            LIMIT 12
            """,
        )
    )
    check_rows = [asdict(c) for c in checks]
    status_counts = {s: sum(1 for c in checks if c.status == s) for s in ("pass", "warn", "fail")}
    weights = config.weights
    return {
        "tool_version": __version__,
        "config": config,
        "question": config.description.strip() or config.name,
        "headline": _headline(summary),
        "summary": summary,
        "cascade": _cascade(summary),
        "shortlist": usable,
        "others": not_usable,
        "top_five": usable[:5] if usable else studies[:5],
        "top_five_are_usable": bool(usable),
        "flag_counts": _flag_counts(studies),
        "review_inbox": review_inbox,
        "checks": check_rows,
        "status_counts": status_counts,
        "validation": validation,
        "validation_rows": _validation_rows(validation),
        "lineage": lineage,
        "weights": [
            {"label": "Metadata completeness", "weight": weights["metadata_completeness"]},
            {"label": "Design fit (young and old groups)", "weight": weights["design_fit"]},
            {"label": "Data type fit", "weight": weights["data_type_fit"]},
            {"label": "Traceability (paper and raw data)", "weight": weights["traceability"]},
        ],
        "data_type_fit": config.data_type_fit,
        "author": config.author,
        "project_url": config.project_url,
        "mouse_thresholds": config.age_groups.get("Mus musculus"),
    }


def write_reports(
    config: ScoutConfig,
    database: Path,
    summary: dict[str, Any],
    checks: list[CheckResult],
    lineage: dict[str, Any],
    validation: dict[str, Any] | None,
) -> tuple[Path, Path]:
    context = build_context(config, database, summary, checks, lineage, validation)
    env = environment()
    output_dir = database.parent
    report = output_dir / "report.html"
    one_pager = output_dir / "one_pager.html"
    report.write_text(env.get_template("report.html.j2").render(**context), encoding="utf-8")
    one_pager.write_text(env.get_template("one_pager.html.j2").render(**context), encoding="utf-8")
    return report, one_pager


def rebuild_reports(config: ScoutConfig, database: Path) -> tuple[Path, Path, dict[str, Any] | None]:
    """Re-render the reports from a finished run (for example after a manual review)."""
    from .validation import refresh_validation

    manifest_path = database.parent / "run_summary.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} not found. Run `scout run` first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = [CheckResult(**c) for c in manifest["checks"]]
    validation = refresh_validation(config, database)
    report, one_pager = write_reports(config, database, manifest["summary"], checks, manifest["lineage"], validation)
    manifest["validation"] = validation
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report, one_pager, validation
