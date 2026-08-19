"""Local job-application tracker backed by SQLite.

This is the "job hunt CRM": everything lives on the user's machine, so tracking
a pipeline (interested → applied → interviewing → …) touches LinkedIn not at all.
Each saved job keeps a description snapshot so agents can tailor résumés or
cover letters later even if the posting is taken down.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from ..config import data_dir
from ..errors import LinkedInError
from ..models import JobEvent, SavedJob, SavedJobsList, SavedJobSummary

STATUSES = ("interested", "applied", "interviewing", "offer", "rejected", "withdrawn", "archived")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_jobs (
    job_id      TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    location    TEXT,
    url         TEXT,
    salary      TEXT,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'interested',
    saved_at    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES saved_jobs(job_id) ON DELETE CASCADE,
    at     TEXT NOT NULL,
    status TEXT,
    note   TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate_status(status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status!r}; allowed: {', '.join(STATUSES)}")
    return status


class TrackerStore:
    def __init__(self, path: Path | None = None):
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or (data_dir() / "tracker.db")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        return conn

    # ------------------------------------------------------------------- writes

    def save(
        self,
        job_id: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
        url: str | None = None,
        salary: str | None = None,
        description: str | None = None,
        status: str = "interested",
        note: str | None = None,
    ) -> tuple[SavedJob, bool]:
        """Insert a job; if it already exists, just record the note. Returns
        (saved_job, created)."""
        _validate_status(status)
        with closing(self._connect()) as conn, conn:
            existing = conn.execute(
                "SELECT job_id FROM saved_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            now = _now()
            if existing:
                if note:
                    conn.execute(
                        "INSERT INTO job_events (job_id, at, note) VALUES (?, ?, ?)",
                        (job_id, now, note),
                    )
                    conn.execute(
                        "UPDATE saved_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id)
                    )
                return self._get(conn, job_id), False
            conn.execute(
                """INSERT INTO saved_jobs
                   (job_id, title, company, location, url, salary, description,
                    status, saved_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, title, company, location, url, salary, description, status, now, now),
            )
            conn.execute(
                "INSERT INTO job_events (job_id, at, status, note) VALUES (?, ?, ?, ?)",
                (job_id, now, status, note or "saved"),
            )
            return self._get(conn, job_id), True

    def update_status(self, job_id: str, status: str, note: str | None = None) -> SavedJob:
        _validate_status(status)
        with closing(self._connect()) as conn, conn:
            now = _now()
            cursor = conn.execute(
                "UPDATE saved_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (status, now, job_id),
            )
            if cursor.rowcount == 0:
                raise LinkedInError(f"Job {job_id} is not in the tracker; save it first.")
            conn.execute(
                "INSERT INTO job_events (job_id, at, status, note) VALUES (?, ?, ?, ?)",
                (job_id, now, status, note),
            )
            return self._get(conn, job_id)

    def add_note(self, job_id: str, note: str) -> SavedJob:
        with closing(self._connect()) as conn, conn:
            now = _now()
            cursor = conn.execute(
                "UPDATE saved_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id)
            )
            if cursor.rowcount == 0:
                raise LinkedInError(f"Job {job_id} is not in the tracker; save it first.")
            conn.execute(
                "INSERT INTO job_events (job_id, at, note) VALUES (?, ?, ?)",
                (job_id, now, note),
            )
            return self._get(conn, job_id)

    def remove(self, job_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM saved_jobs WHERE job_id = ?", (job_id,))
            return cursor.rowcount > 0

    # -------------------------------------------------------------------- reads

    def get(self, job_id: str) -> SavedJob:
        with closing(self._connect()) as conn, conn:
            return self._get(conn, job_id)

    def _get(self, conn: sqlite3.Connection, job_id: str) -> SavedJob:
        row = conn.execute("SELECT * FROM saved_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise LinkedInError(f"Job {job_id} is not in the tracker.")
        events = [
            JobEvent(at=e["at"], status=e["status"], note=e["note"])
            for e in conn.execute(
                "SELECT at, status, note FROM job_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            )
        ]
        return SavedJob(**dict(row), events=events)

    def list(self, status: str | None = None, search: str | None = None) -> SavedJobsList:
        if status:
            _validate_status(status)
        with closing(self._connect()) as conn, conn:
            where, args = [], []
            if status:
                where.append("status = ?")
                args.append(status)
            if search:
                where.append("(title LIKE ? OR company LIKE ?)")
                args.extend([f"%{search}%"] * 2)
            clause = f"WHERE {' AND '.join(where)}" if where else ""
            rows = conn.execute(
                f"SELECT job_id, title, company, location, status, updated_at, url "
                f"FROM saved_jobs {clause} ORDER BY updated_at DESC",
                args,
            ).fetchall()
            counts = dict(
                conn.execute("SELECT status, COUNT(*) FROM saved_jobs GROUP BY status").fetchall()
            )
        return SavedJobsList(
            total=sum(counts.values()),
            by_status=counts,
            jobs=[SavedJobSummary(**dict(r)) for r in rows],
        )
