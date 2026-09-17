import pytest

from dataset_scout.config import AgeThresholds
from dataset_scout.harmonize.age import assign_age_group, find_age_in_text, parse_age, unit_from_key

MOUSE = AgeThresholds(young_min_months=1.5, young_max_months=6, old_min_months=18)


@pytest.mark.parametrize(
    ("raw", "key", "months", "rule"),
    [
        ("3 months", "age", 3.0, "number_with_unit"),
        ("3mo", "age", 3.0, "number_with_unit"),
        ("12-week-old", "age", 2.76, "number_with_unit"),
        ("12 wk", "age", 2.76, "number_with_unit"),
        ("24 months old", "age", 24.0, "number_with_unit"),
        ("2 years", "age", 24.0, "number_with_unit"),
        ("10-12 weeks", "age", 2.53, "range_midpoint"),
        ("18 to 20 months", "age", 19.0, "range_midpoint"),
        ("2,5 months", "age", 2.5, "number_with_unit"),
        ("P60", "age", 1.97, "postnatal_day"),
        ("week 8", "age", 1.84, "unit_then_number"),
        ("20", "age (months)", 20.0, "unit_from_field_name"),
        ("8", "age_wk", 1.84, "unit_from_field_name"),
    ],
)
def test_numeric_ages_are_converted_to_months(raw, key, months, rule):
    result = parse_age(raw, key=key, organism="Mus musculus")
    assert result.months == pytest.approx(months, abs=0.01)
    assert result.rule == rule


def test_embryonic_stage_has_no_months_but_is_developmental():
    result = parse_age("E14.5", key="age")
    assert result.stage == "embryonic" and result.months is None
    assert assign_age_group(result, MOUSE) == "developmental"


def test_bare_number_is_not_guessed_for_mouse():
    result = parse_age("20", key="age", organism="Mus musculus")
    assert result.months is None
    assert result.rule == "number_without_unit"
    assert "unit_missing" in result.flags


def test_bare_number_is_years_for_human_and_flagged():
    result = parse_age("70", key="age", organism="Homo sapiens")
    assert result.months == 840
    assert "unit_assumed" in result.flags


@pytest.mark.parametrize(("raw", "group"), [("aged", "old"), ("young adult", "young"), ("middle-aged", "middle")])
def test_qualitative_ages(raw, group):
    result = parse_age(raw, key="age")
    assert result.rule == "qualitative"
    assert assign_age_group(result, MOUSE) == group


@pytest.mark.parametrize("raw", ["", "N/A", "unknown", "not available"])
def test_missing_values(raw):
    assert parse_age(raw).rule == "missing"


def test_unreadable_age_is_reported_not_guessed():
    result = parse_age("twenty months", key="age")
    assert result.rule == "unparsed" and not result.parsed


@pytest.mark.parametrize(
    ("months", "group"), [(1.0, "developmental"), (1.5, "young"), (6, "young"), (12, "middle"), (18, "old")]
)
def test_group_boundaries(months, group):
    result = parse_age(f"{months} months", key="age")
    assert assign_age_group(result, MOUSE) == group


def test_text_fallback_only_accepts_unambiguous_patterns():
    assert find_age_in_text("liver, 3 days post injury", "title") is None
    found = find_age_in_text("microglia from 24-month-old mice", "title")
    assert found.months == 24 and "inferred" in found.flags
    assert find_age_in_text("Aged_rep1", "title").qualitative == "old"


def test_young_serum_is_not_the_age_of_the_animal():
    # Old mice infused with young serum: "young" describes the donor, not the animal.
    assert find_age_in_text("young seurm infusion, hippocampus, 3", "title") is None
    assert find_age_in_text("old mice given young plasma", "title") is None
    assert find_age_in_text("24-month-old mice given young plasma", "title").months == 24


def test_unit_from_key():
    assert unit_from_key("Age (months)") == "months"
    assert unit_from_key("age") is None
    assert unit_from_key("age_days") == "days"
