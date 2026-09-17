import pytest

from dataset_scout.config import ConfigError, config_from_dict, load_config
from dataset_scout.pipeline import with_overrides


def base(**changes):
    raw = {"name": "My Question", "query": {"terms": "microglia"}}
    raw.update(changes)
    return raw


def test_defaults_and_slug():
    cfg = config_from_dict(base())
    assert cfg.name == "my-question"
    assert str(cfg.output_dir).replace("\\", "/") == "outputs/my-question"
    assert sum(cfg.weights.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "raw",
    [
        {"query": {"terms": "x"}},
        {"name": "x"},
        base(scoring={"weights": {"metadata_completeness": 0.9}}),
        base(age_groups={"Mus musculus": {"young_min_months": 1, "young_max_months": 20, "old_min_months": 18}}),
        base(geo={"view": "everything"}),
        base(design={"min_per_group": 0}),
    ],
)
def test_invalid_configs_are_rejected(raw):
    with pytest.raises(ConfigError):
        config_from_dict(raw)


def test_fingerprint_changes_with_settings():
    assert config_from_dict(base()).sha256 != config_from_dict(base(design={"min_per_group": 4})).sha256


def test_overrides_are_part_of_the_fingerprint(config):
    smaller = with_overrides(config, max_studies=5)
    assert smaller.max_studies == 5 and smaller.sha256 != config.sha256


def test_demo_config_loads(project):
    cfg = load_config("config/microglia_aging.yaml")
    assert cfg.is_demo and cfg.author == "Facundo Dallo"
    assert cfg.thresholds_for("Mus musculus").old_min_months == 18
