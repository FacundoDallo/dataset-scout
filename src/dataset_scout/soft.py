"""Parse GEO SOFT text into sample records.

SOFT ("Simple Omnibus Format in Text") is line based:

    ^SAMPLE = GSM11805                      <- a new entity starts
    !Sample_title = N035 Normal Kidney      <- an attribute of that entity
    !Sample_characteristics_ch1 = age: 70   <- free-text "key: value" metadata
    !sample_table_begin                     <- measurement table (skipped here)
    ...
    !sample_table_end

Attributes can repeat (a sample usually has several characteristics lines),
so every attribute is stored as a list. Characteristics are also split into
(key, value) pairs, keeping the original line for traceability.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

_CHARACTERISTICS = re.compile(r"^characteristics_ch(\d+)$")
_MAX_KEY_LENGTH = 60


@dataclass(frozen=True)
class Characteristic:
    channel: int
    position: int
    key: str | None
    value: str
    raw: str


@dataclass
class SoftSample:
    accession: str
    attributes: dict[str, list[str]] = field(default_factory=dict)
    characteristics: list[Characteristic] = field(default_factory=list)

    def get(self, name: str, default: str = "") -> str:
        values = self.attributes.get(name)
        return values[0] if values else default

    def get_all(self, name: str) -> list[str]:
        return list(self.attributes.get(name, []))


def normalize_attribute(name: str) -> str:
    """'!Sample_characteristics_ch1' -> 'characteristics_ch1'."""
    name = name.strip().lstrip("!")
    if name.lower().startswith("sample_"):
        name = name[len("sample_") :]
    return name.strip().lower()


def split_characteristic(text: str) -> tuple[str | None, str]:
    """'age: 3 months' -> ('age', '3 months'). Values without a usable key keep key=None."""
    stripped = text.strip()
    key, sep, value = stripped.partition(":")
    key = key.strip()
    if sep and key and len(key) <= _MAX_KEY_LENGTH and not key.lower().startswith(("http", "ftp")):
        return key, value.strip()
    return None, stripped


def parse_soft_samples(text: str) -> list[SoftSample]:
    """Return every ^SAMPLE entity found in a SOFT document."""
    samples: list[SoftSample] = []
    current: SoftSample | None = None
    in_table = False
    positions: dict[int, int] = defaultdict(int)

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("!sample_table_begin"):
            in_table = True
            continue
        if lowered.startswith("!sample_table_end"):
            in_table = False
            continue
        if in_table:
            continue

        if line.startswith("^"):
            entity, _, value = line[1:].partition("=")
            if entity.strip().upper() == "SAMPLE":
                current = SoftSample(accession=value.strip())
                samples.append(current)
                positions = defaultdict(int)
            else:
                current = None
            continue

        if current is None or not line.startswith("!"):
            continue
        name, sep, value = line[1:].partition("=")
        if not sep:
            continue
        attribute = normalize_attribute(name)
        value = value.strip()
        current.attributes.setdefault(attribute, []).append(value)

        if attribute == "geo_accession" and value:
            current.accession = value
        match = _CHARACTERISTICS.match(attribute)
        if match:
            channel = int(match.group(1))
            positions[channel] += 1
            key, clean_value = split_characteristic(value)
            current.characteristics.append(
                Characteristic(
                    channel=channel,
                    position=positions[channel],
                    key=key,
                    value=clean_value,
                    raw=value,
                )
            )
    return samples
