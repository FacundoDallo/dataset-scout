"""Load and validate a YAML run configuration.

A configuration describes one scientific question: what to search for in GEO,
which studies are in scope, how to define "young" and "old", and how to weight
the usability score. Keeping all of that in a file (instead of in the code)
means a scientist can review the assumptions without reading Python.

Relative paths in the file are resolved against the folder you run the
command from, so run `scout` from the project folder.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

GEO_VIEWS = ("brief", "quick", "full")

DEFAULTS: dict[str, Any] = {
    "description": "",
    "query": {"organism": None, "published_from": None, "published_to": None},
    "limits": {"max_studies": 150},
    "scope": {
        "organisms": ["Mus musculus"],
        "data_types": ["bulk RNA-seq", "single-cell RNA-seq", "microarray"],
        "exclude_superseries": True,
    },
    "design": {"type": "age_contrast", "min_per_group": 3},
    "age_groups": {
        "Mus musculus": {"young_min_months": 1.5, "young_max_months": 6, "old_min_months": 18},
        "Rattus norvegicus": {"young_min_months": 1.5, "young_max_months": 6, "old_min_months": 20},
        "Homo sapiens": {"young_min_months": 216, "young_max_months": 480, "old_min_months": 780},
    },
    "harmonize": {"age_fallback": True, "sex_fallback": True},
    "scoring": {
        "weights": {
            "metadata_completeness": 0.40,
            "design_fit": 0.30,
            "data_type_fit": 0.15,
            "traceability": 0.15,
        },
        "data_type_fit": {
            "bulk RNA-seq": 1.0,
            "single-cell RNA-seq": 0.8,
            "microarray": 0.6,
        },
        "usable_threshold": 60,
        "ready_threshold": 75,
    },
    "geo": {"view": "brief"},
    "paths": {"snapshot": None, "output": None, "vocabulary": None, "validation": None},
    "report": {"author": None, "project_url": None},
    "demo": False,
}


class ConfigError(ValueError):
    """Raised when a configuration file is missing information or is inconsistent."""


@dataclass(frozen=True)
class AgeThresholds:
    young_min_months: float
    young_max_months: float
    old_min_months: float


@dataclass(frozen=True)
class ScoutConfig:
    """Everything a run needs, already validated."""

    name: str
    description: str
    query_terms: str
    organism: str | None
    published_from: str | None
    published_to: str | None
    max_studies: int
    scope_organisms: tuple[str, ...]
    scope_data_types: tuple[str, ...]
    exclude_superseries: bool
    min_per_group: int
    age_groups: dict[str, AgeThresholds]
    age_fallback: bool
    sex_fallback: bool
    weights: dict[str, float]
    data_type_fit: dict[str, float]
    usable_threshold: float
    ready_threshold: float
    geo_view: str
    snapshot_dir: Path
    output_dir: Path
    vocabulary_path: Path | None
    validation_dir: Path
    is_demo: bool
    source_path: Path | None
    data: dict[str, Any]

    @property
    def sha256(self) -> str:
        """Fingerprint of the settings that change results (used for lineage)."""
        canonical = json.dumps(self.data, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def author(self) -> str | None:
        return (self.data.get("report") or {}).get("author") or None

    @property
    def project_url(self) -> str | None:
        return (self.data.get("report") or {}).get("project_url") or None

    def thresholds_for(self, organism: str | None) -> AgeThresholds | None:
        if not organism:
            return None
        return self.age_groups.get(organism)

    def settings_rows(self) -> dict[str, str]:
        """Flat key/value view stored in the database so SQL views can read it."""
        mouse = self.age_groups.get("Mus musculus")
        rows = {
            "config_name": self.name,
            "config_sha256": self.sha256,
            "min_per_group": str(self.min_per_group),
            "usable_threshold": str(self.usable_threshold),
            "ready_threshold": str(self.ready_threshold),
            "query_terms": self.query_terms,
        }
        if mouse:
            rows.update(
                {
                    "mouse_young_min_months": str(mouse.young_min_months),
                    "mouse_young_max_months": str(mouse.young_max_months),
                    "mouse_old_min_months": str(mouse.old_min_months),
                }
            )
        return rows


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _slug(text: str) -> str:
    keep = [c if c.isalnum() else "-" for c in text.lower()]
    return "-".join(part for part in "".join(keep).split("-") if part) or "run"


def config_from_dict(raw: dict[str, Any], source_path: Path | None = None) -> ScoutConfig:
    """Build a validated `ScoutConfig` from a plain dictionary."""
    if not isinstance(raw, dict):
        raise ConfigError("The configuration must be a YAML mapping (key: value pairs).")
    if not raw.get("name"):
        raise ConfigError("Missing 'name': give the run a short name, e.g. 'microglia-aging'.")
    terms = (raw.get("query") or {}).get("terms")
    if not terms or not str(terms).strip():
        raise ConfigError("Missing 'query.terms': the words to search for in GEO.")

    data = _deep_merge(DEFAULTS, raw)
    name = _slug(str(data["name"]))

    weights = {k: float(v) for k, v in data["scoring"]["weights"].items()}
    expected = set(DEFAULTS["scoring"]["weights"])
    if set(weights) != expected:
        raise ConfigError(f"scoring.weights must define exactly: {sorted(expected)}")
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ConfigError(f"scoring.weights must add up to 1.0 (they add up to {sum(weights.values()):.3f}).")

    view = str(data["geo"]["view"]).lower()
    if view not in GEO_VIEWS:
        raise ConfigError(f"geo.view must be one of {GEO_VIEWS}, got '{view}'.")

    age_groups: dict[str, AgeThresholds] = {}
    for organism, spec in data["age_groups"].items():
        try:
            thresholds = AgeThresholds(
                young_min_months=float(spec["young_min_months"]),
                young_max_months=float(spec["young_max_months"]),
                old_min_months=float(spec["old_min_months"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(
                f"age_groups.{organism} needs young_min_months, young_max_months and old_min_months."
            ) from exc
        if not thresholds.young_min_months <= thresholds.young_max_months < thresholds.old_min_months:
            raise ConfigError(f"age_groups.{organism}: expected young_min <= young_max < old_min.")
        age_groups[organism] = thresholds

    min_per_group = int(data["design"]["min_per_group"])
    if min_per_group < 1:
        raise ConfigError("design.min_per_group must be at least 1.")
    max_studies = int(data["limits"]["max_studies"])
    if not 1 <= max_studies <= 10_000:
        raise ConfigError("limits.max_studies must be between 1 and 10000.")

    paths = data["paths"]
    snapshot = Path(paths["snapshot"]) if paths.get("snapshot") else Path("snapshots") / name
    output = Path(paths["output"]) if paths.get("output") else Path("outputs") / name
    vocabulary = Path(paths["vocabulary"]) if paths.get("vocabulary") else None
    validation = Path(paths["validation"]) if paths.get("validation") else Path("validation") / name

    query = data["query"]
    return ScoutConfig(
        name=name,
        description=str(data.get("description") or ""),
        query_terms=" ".join(str(terms).split()),
        organism=query.get("organism"),
        published_from=str(query["published_from"]) if query.get("published_from") else None,
        published_to=str(query["published_to"]) if query.get("published_to") else None,
        max_studies=max_studies,
        scope_organisms=tuple(data["scope"]["organisms"]),
        scope_data_types=tuple(data["scope"]["data_types"]),
        exclude_superseries=bool(data["scope"]["exclude_superseries"]),
        min_per_group=min_per_group,
        age_groups=age_groups,
        age_fallback=bool(data["harmonize"]["age_fallback"]),
        sex_fallback=bool(data["harmonize"]["sex_fallback"]),
        weights=weights,
        data_type_fit={k: float(v) for k, v in data["scoring"]["data_type_fit"].items()},
        usable_threshold=float(data["scoring"]["usable_threshold"]),
        ready_threshold=float(data["scoring"]["ready_threshold"]),
        geo_view=view,
        snapshot_dir=snapshot,
        output_dir=output,
        vocabulary_path=vocabulary,
        validation_dir=validation,
        is_demo=bool(data.get("demo")),
        source_path=source_path,
        data=data,
    )


def load_config(path: str | os.PathLike[str]) -> ScoutConfig:
    """Read a YAML file and return a validated configuration."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path.resolve()}")
    with config_path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return config_from_dict(raw, source_path=config_path)


def ncbi_identity() -> tuple[str | None, str | None]:
    """E-mail and API key for NCBI, read from environment variables (never from files)."""
    email = os.environ.get("NCBI_EMAIL") or None
    api_key = os.environ.get("NCBI_API_KEY") or None
    return email, api_key
