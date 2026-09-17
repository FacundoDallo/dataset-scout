"""Parse free-text ages into months.

GEO does not enforce a format for age. The same information shows up as
"3 months", "3mo", "12-week-old", "P60", "age (months): 18" or just "aged".
`parse_age` converts those strings into months and records *which rule*
produced the number, so every harmonized value can be traced back.

Design choices worth knowing:
- Nothing is guessed silently. A bare number without a unit ("20") is only
  converted when the field name says the unit ("age (months)") or for human
  samples, where years are the convention; both cases are flagged.
- Ranges ("10-12 weeks") become their midpoint and are flagged as ranges.
- Embryonic stages (E14.5) keep a stage but no age in months.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import AgeThresholds
from ..text import clean_words, is_missing

DAYS_PER_MONTH = 365.25 / 12
_TO_MONTHS = {"days": 1 / DAYS_PER_MONTH, "weeks": 7 / DAYS_PER_MONTH, "months": 1.0, "years": 12.0}

UNITS = r"years?|yrs?|yo|y|months?|mths?|mon|mos?|m|weeks?|wks?|wo|w|days?|d"
_NUM = r"(?P<num>\d+(?:\.\d+)?)"
_NUM2 = r"(?P<num2>\d+(?:\.\d+)?)"

EMBRYONIC_RE = re.compile(r"(?<![a-z0-9])(?:e|embryonic day)\s?(?P<num>\d+(?:\.\d+)?)(?![0-9])")
POSTNATAL_RE = re.compile(
    r"(?<![a-z0-9])(?:p|pnd|postnatal day)\s?(?P<num>\d+(?:\.\d+)?)(?![a-z0-9])"
)
RANGE_RE = re.compile(rf"{_NUM}\s*(?:-|to|~)\s*{_NUM2}\s*-?\s*(?P<unit>{UNITS})(?![a-z])")
VALUE_RE = re.compile(rf"{_NUM}\s*-?\s*(?P<unit>{UNITS})(?![a-z])")
UNIT_FIRST_RE = re.compile(rf"\b(?P<unit>years?|months?|weeks?|days?)\s*{_NUM}(?![0-9])")
BARE_NUMBER_RE = re.compile(rf"^{_NUM}$")
BARE_RANGE_RE = re.compile(rf"^{_NUM}\s*(?:-|to)\s*{_NUM2}$")
OLD_SUFFIX_RE = re.compile(rf"{_NUM}\s*-?\s*(?P<unit>{UNITS})\s*-?\s*old(?![a-z])")
AGED_PREFIX_RE = re.compile(rf"\baged\s+{_NUM}\s*-?\s*(?P<unit>{UNITS})(?![a-z])")
KEY_UNIT_RE = re.compile(rf"(?<![a-z])(?P<unit>{UNITS})(?![a-z])")

QUALITATIVE = (
    ("middle", re.compile(r"\bmiddle[- ]?aged?\b")),
    ("old", re.compile(r"\b(old|aged|elderly|geriatric)\b")),
    ("young", re.compile(r"\b(young|juvenile)\b")),
    ("adult", re.compile(r"\badults?\b")),
    ("developmental", re.compile(r"\b(embryos?|embryonic|fetal|foetal|neonatal|neonates?|newborns?|pups?)\b")),
)

AGE_GROUPS = ("developmental", "young", "middle", "old", "adult")

# In "young serum infusion" the age word describes a donor or a transferred material, not the animal.
TRANSFER_RE = re.compile(
    r"\b(serum|plasma|infusions?|infused|transfusions?|transplant\w*|parabio\w*|heterochronic|donors?)\b"
)


@dataclass(frozen=True)
class AgeResult:
    raw: str
    rule: str
    value: float | None = None
    unit: str | None = None
    months: float | None = None
    stage: str | None = None
    qualitative: str | None = None
    flags: tuple[str, ...] = ()

    @property
    def parsed(self) -> bool:
        return self.months is not None or self.qualitative is not None or self.stage is not None


def canonical_unit(token: str) -> str:
    t = token.lower()
    if t.startswith("y"):
        return "years"
    if t.startswith("w"):
        return "weeks"
    if t.startswith("d"):
        return "days"
    if t.startswith("m"):
        return "months"
    raise ValueError(f"Unknown age unit: {token}")


def to_months(value: float, unit: str) -> float:
    return round(value * _TO_MONTHS[unit], 2)


def _number(text: str) -> float:
    return float(text)


def _normalize_decimals(text: str) -> str:
    # "2,5 months" is a decimal comma, "2, 5" is a list; only the first is rewritten.
    return re.sub(r"(?<=\d),(?=\d)", ".", text)


def unit_from_key(key: str | None) -> str | None:
    """'age (months)' -> 'months'; 'age_wk' -> 'weeks'; 'age' -> None."""
    if not key:
        return None
    text = clean_words(key)
    text = re.sub(r"\bages?\b", " ", text)
    match = KEY_UNIT_RE.search(text)
    return canonical_unit(match.group("unit")) if match else None


def parse_age(value: object, key: str | None = None, organism: str | None = None) -> AgeResult:
    """Parse an age value that came from an explicit age field."""
    raw = "" if value is None else str(value).strip()
    text = _normalize_decimals(clean_words(raw))
    if is_missing(text):
        return AgeResult(raw=raw, rule="missing", flags=("missing",))

    match = EMBRYONIC_RE.search(text)
    if match:
        return AgeResult(
            raw=raw,
            rule="embryonic_day",
            value=_number(match.group("num")),
            unit="embryonic days",
            stage="embryonic",
        )

    match = POSTNATAL_RE.search(text)
    if match:
        days = _number(match.group("num"))
        return AgeResult(
            raw=raw,
            rule="postnatal_day",
            value=days,
            unit="days",
            months=to_months(days, "days"),
            stage="postnatal",
        )

    match = RANGE_RE.search(text)
    if match:
        unit = canonical_unit(match.group("unit"))
        low, high = _number(match.group("num")), _number(match.group("num2"))
        midpoint = (low + high) / 2
        return AgeResult(
            raw=raw,
            rule="range_midpoint",
            value=midpoint,
            unit=unit,
            months=to_months(midpoint, unit),
            flags=("range",),
        )

    match = VALUE_RE.search(text)
    if match:
        unit = canonical_unit(match.group("unit"))
        number = _number(match.group("num"))
        return AgeResult(
            raw=raw,
            rule="number_with_unit",
            value=number,
            unit=unit,
            months=to_months(number, unit),
        )

    match = UNIT_FIRST_RE.search(text)
    if match:
        unit = canonical_unit(match.group("unit"))
        number = _number(match.group("num"))
        return AgeResult(
            raw=raw,
            rule="unit_then_number",
            value=number,
            unit=unit,
            months=to_months(number, unit),
        )

    bare = BARE_NUMBER_RE.match(text)
    bare_range = BARE_RANGE_RE.match(text)
    if bare or bare_range:
        if bare:
            number, extra_flags = _number(bare.group("num")), ()
        else:
            number = (_number(bare_range.group("num")) + _number(bare_range.group("num2"))) / 2
            extra_flags = ("range",)
        key_unit = unit_from_key(key)
        if key_unit:
            return AgeResult(
                raw=raw,
                rule="unit_from_field_name",
                value=number,
                unit=key_unit,
                months=to_months(number, key_unit),
                flags=extra_flags,
            )
        if organism == "Homo sapiens":
            return AgeResult(
                raw=raw,
                rule="assumed_years_for_human",
                value=number,
                unit="years",
                months=to_months(number, "years"),
                flags=("unit_assumed", *extra_flags),
            )
        return AgeResult(
            raw=raw,
            rule="number_without_unit",
            value=number,
            flags=("unit_missing", *extra_flags),
        )

    for label, pattern in QUALITATIVE:
        if pattern.search(text):
            stage = "embryonic_or_neonatal" if label == "developmental" else None
            return AgeResult(raw=raw, rule="qualitative", qualitative=label, stage=stage, flags=("qualitative",))

    return AgeResult(raw=raw, rule="unparsed", flags=("unparsed",))


def find_age_in_text(text: object, source: str) -> AgeResult | None:
    """Look for an age inside free text that is *not* an age field.

    Only unambiguous patterns are accepted ("24-month-old", "aged 18 months",
    "young", "aged"), because titles also contain times such as "3 days post
    injury" that must not be read as ages. Words such as "young" are ignored
    when the text mentions serum, plasma or a transfer. Results are flagged as
    inferred.
    """
    raw = "" if text is None else str(text).strip()
    cleaned = _normalize_decimals(clean_words(raw))
    if not cleaned:
        return None
    for rule, pattern in (("text_n_units_old", OLD_SUFFIX_RE), ("text_aged_n_units", AGED_PREFIX_RE)):
        match = pattern.search(cleaned)
        if match:
            unit = canonical_unit(match.group("unit"))
            number = _number(match.group("num"))
            return AgeResult(
                raw=raw,
                rule=f"{rule}:{source}",
                value=number,
                unit=unit,
                months=to_months(number, unit),
                flags=("inferred",),
            )
    if TRANSFER_RE.search(cleaned):
        return None
    for label, pattern in QUALITATIVE:
        if label in {"young", "old", "middle"} and pattern.search(cleaned):
            return AgeResult(
                raw=raw,
                rule=f"text_qualitative:{source}",
                qualitative=label,
                flags=("inferred", "qualitative"),
            )
    return None


def assign_age_group(result: AgeResult | None, thresholds: AgeThresholds | None) -> str | None:
    """Place a parsed age into developmental / young / middle / old / adult."""
    if result is None:
        return None
    if result.months is not None and thresholds is not None:
        months = result.months
        if result.stage == "embryonic" or months < thresholds.young_min_months:
            return "developmental"
        if months <= thresholds.young_max_months:
            return "young"
        if months < thresholds.old_min_months:
            return "middle"
        return "old"
    if result.stage in {"embryonic", "embryonic_or_neonatal"}:
        return "developmental"
    if result.qualitative in AGE_GROUPS:
        return result.qualitative
    return None
