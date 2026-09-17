"""Shared test fixtures.

`project` gives each test an empty working folder with the demo
configuration, so outputs never leak between tests.
`recorded_project` additionally runs the pipeline once against the fake
NCBI, leaving a complete snapshot and database behind.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fake_ncbi import FakeNcbiSession

from dataset_scout.config import load_config
from dataset_scout.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
REAL = Path(__file__).parent / "fixtures" / "real"


@pytest.fixture
def project(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    for name in ("NCBI_EMAIL", "NCBI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def config(project):
    return load_config("config/microglia_aging.yaml")


@pytest.fixture
def recorded(config):
    session = FakeNcbiSession()
    result = run_pipeline(config, mode="auto", http_session=session, sleep=lambda s: None)
    return result, session


@pytest.fixture
def real_text():
    def read(name: str) -> str:
        return (REAL / name).read_bytes().decode("utf-8")

    return read
