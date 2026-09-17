"""Build and query the DuckDB database (the "load" step).

DuckDB is an analytical database that lives in a single file, like SQLite,
but is built for the kind of queries analysts run (group by, joins over
many rows). No server is needed, which keeps the project easy to run.
The same tables would map one-to-one to Delta tables on Databricks or to a
Postgres schema in a client environment.

Every run rebuilds the database from scratch. That makes a run idempotent:
running it twice with the same snapshot gives the same tables, and no stale
rows survive from an earlier run.
"""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

import duckdb
import pandas as pd

log = logging.getLogger(__name__)

# Insert order matters: a row can only reference rows that already exist.
LOAD_ORDER: tuple[str, ...] = (
    "runs",
    "run_settings",
    "studies",
    "samples",
    "study_samples",
    "sample_characteristics",
    "study_quality",
    "fetch_errors",
    "data_checks",
    "validation_results",
)

# Tables a report needs when it is rebuilt without re-running the pipeline.
REPORT_TABLES: tuple[str, ...] = (
    "runs",
    "run_settings",
    "studies",
    "samples",
    "study_samples",
    "sample_characteristics",
    "study_quality",
    "data_checks",
    "validation_results",
)


class DatabaseError(RuntimeError):
    """The database file could not be written or read."""


def read_sql(name: str) -> str:
    return resources.files("dataset_scout.sql").joinpath(name).read_text(encoding="utf-8")


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()]


def _prepare(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Keep the table's columns, in order, with missing values as SQL NULL."""
    prepared = frame.reindex(columns=columns)
    return prepared.astype(object).where(prepared.notna(), None)


def build_database(path: str | Path, tables: dict[str, pd.DataFrame]) -> Path:
    """Create a fresh database file with the schema, the data and the views."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in (db_path, db_path.with_name(db_path.name + ".wal")):
        try:
            stale.unlink(missing_ok=True)
        except PermissionError as exc:
            raise DatabaseError(
                f"Cannot replace {stale}: the file is open in another program "
                "(a SQL client or another terminal). Close it and run again."
            ) from exc

    con = duckdb.connect(str(db_path))
    try:
        con.execute(read_sql("schema.sql"))
        for table in LOAD_ORDER:
            frame = tables.get(table)
            if frame is None or not len(frame):
                continue
            columns = _columns(con, table)
            prepared = _prepare(frame, columns)
            view_name = f"incoming_{table}"
            con.register(view_name, prepared)
            column_list = ", ".join(columns)
            con.execute(f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM {view_name}")
            con.unregister(view_name)
            log.debug("  loaded %s rows into %s", len(prepared), table)
        con.execute(read_sql("views.sql"))
    finally:
        con.close()
    return db_path


def query(path: str | Path, sql: str) -> pd.DataFrame:
    """Run a read-only query and return the result as a DataFrame."""
    db_path = Path(path)
    if not db_path.exists():
        raise DatabaseError(f"No database at {db_path}. Run `scout run` first.")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def load_tables(path: str | Path, names: tuple[str, ...] = REPORT_TABLES) -> dict[str, pd.DataFrame]:
    """Read whole tables back into DataFrames (used to rebuild a report)."""
    db_path = Path(path)
    if not db_path.exists():
        raise DatabaseError(f"No database at {db_path}. Run `scout run` first.")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        frames = {name: con.execute(f"SELECT * FROM {name}").df() for name in names}
    finally:
        con.close()
    return frames


def replace_table(path: str | Path, table: str, frame: pd.DataFrame) -> None:
    """Swap the contents of one table (used after a manual validation)."""
    con = duckdb.connect(str(Path(path)))
    try:
        columns = _columns(con, table)
        con.execute(f"DELETE FROM {table}")
        if len(frame):
            prepared = _prepare(frame, columns)
            con.register("incoming", prepared)
            column_list = ", ".join(columns)
            con.execute(f"INSERT INTO {table} ({column_list}) SELECT {column_list} FROM incoming")
            con.unregister("incoming")
    finally:
        con.close()
