"""HTTP access with record and replay.

Every response the pipeline downloads is written to a *snapshot* folder,
keyed by the request. That gives three modes:

- ``live``:   always download, then store (refreshes the snapshot)
- ``auto``:   use the stored copy if there is one, otherwise download and store
- ``replay``: only use stored copies and fail loudly if one is missing

Replay is what makes a run reproducible: the same snapshot and the same
configuration always produce the same tables and report, with no internet.
It is the data equivalent of keeping the original lab notebook.

Permanent failures (for example a 404 for a withdrawn series) are recorded
too, so a replayed run fails in exactly the same places as the original.

Credentials (``api_key``) and identity (``email``, ``tool``) are left out of
the request key and never written to disk, so snapshots are safe to share.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from . import __version__

log = logging.getLogger(__name__)

MODES = ("live", "auto", "replay")
VOLATILE_PARAMS = frozenset({"api_key", "email", "tool"})
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class ReplayMissError(RuntimeError):
    """Raised in replay mode when a request was never recorded."""


class HttpError(RuntimeError):
    """Raised when a request keeps failing after all retries."""


def canonical_url(url: str, params: dict[str, Any] | None = None) -> str:
    """URL with sorted parameters and without credentials (stable across runs)."""
    items = sorted((k, str(v)) for k, v in (params or {}).items() if k not in VOLATILE_PARAMS)
    return f"{url}?{urlencode(items)}" if items else url


def request_key(url: str, params: dict[str, Any] | None = None) -> str:
    """Short, stable identifier for a request (first 20 hex chars of a SHA-256)."""
    return hashlib.sha256(canonical_url(url, params).encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class Response:
    key: str
    url: str
    status: int
    text: str
    fetched_at: str
    replayed: bool


class RecordingClient:
    """A polite HTTP client: rate-limited, retries transient errors, records everything."""

    def __init__(
        self,
        snapshot_dir: str | Path,
        mode: str = "auto",
        min_interval: float = 0.34,
        timeout: float = 60.0,
        max_retries: int = 4,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got '{mode}'")
        self.snapshot_dir = Path(snapshot_dir)
        self.responses_dir = self.snapshot_dir / "responses"
        self.mode = mode
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.update({"User-Agent": f"dataset-scout/{__version__} (research tool)"})
        self.stats = {"replayed": 0, "downloaded": 0, "retries": 0}
        self._last_request = 0.0

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.responses_dir / f"{key}.meta.json", self.responses_dir / f"{key}.body"

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response:
        key = request_key(url, params)
        meta_path, body_path = self._paths(key)

        if self.mode != "live" and meta_path.exists() and body_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.stats["replayed"] += 1
            if int(meta["status"]) != 200:
                raise HttpError(f"HTTP {meta['status']} for {meta['url']} (recorded)")
            return Response(
                key=key,
                url=meta["url"],
                status=int(meta["status"]),
                text=body_path.read_bytes().decode("utf-8", errors="replace"),
                fetched_at=meta["fetched_at"],
                replayed=True,
            )

        if self.mode == "replay":
            raise ReplayMissError(
                f"No recorded response for {canonical_url(url, params)}\n"
                f"Looked in: {self.responses_dir}\n"
                "Run with --mode auto (or live) once to record it."
            )

        http_response = self._fetch(url, params)
        body = http_response.content
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta = {
            "url": canonical_url(url, params),
            "status": http_response.status_code,
            "fetched_at": fetched_at,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": http_response.headers.get("Content-Type", ""),
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.stats["downloaded"] += 1
        if http_response.status_code != 200:
            raise HttpError(f"HTTP {http_response.status_code} for {meta['url']}")
        return Response(
            key=key,
            url=meta["url"],
            status=http_response.status_code,
            text=body.decode("utf-8", errors="replace"),
            fetched_at=fetched_at,
            replayed=False,
        )

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            self.sleep(wait)
        self._last_request = time.monotonic()

    def _fetch(self, url: str, params: dict[str, Any] | None) -> Any:
        attempt = 0
        while True:
            self._throttle()
            problem: str
            cause: Exception | None = None
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                problem, cause = f"network error ({exc.__class__.__name__})", exc
            else:
                if response.status_code == 200 or response.status_code not in RETRYABLE_STATUS:
                    # Success, or a permanent answer (e.g. 404) that is worth recording.
                    return response
                problem = f"HTTP {response.status_code}"

            attempt += 1
            if attempt > self.max_retries:
                raise HttpError(
                    f"Giving up after {self.max_retries} retries ({problem}): {canonical_url(url, params)}"
                ) from cause
            wait = 2 ** (attempt - 1)
            self.stats["retries"] += 1
            log.warning("  %s, retrying in %ss (attempt %s/%s)", problem, wait, attempt, self.max_retries)
            self.sleep(wait)

    def write_index(self) -> Path | None:
        """Write snapshot_index.csv: one human-readable line per recorded request."""
        if not self.responses_dir.exists():
            return None
        rows = []
        for meta_path in sorted(self.responses_dir.glob("*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "key": meta_path.name.removesuffix(".meta.json"),
                    "fetched_at": meta["fetched_at"],
                    "bytes": meta["bytes"],
                    "sha256": meta["sha256"],
                    "url": meta["url"],
                }
            )
        index_path = self.snapshot_dir / "snapshot_index.csv"
        with index_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["key", "fetched_at", "bytes", "sha256", "url"])
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: r["url"]))
        return index_path
