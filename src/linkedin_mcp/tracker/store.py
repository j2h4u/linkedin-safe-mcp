"""Local job-application tracker backed by SQLite.

This is the "job hunt CRM": everything lives on the user's machine, so tracking
a pipeline (interested → applied → interviewing → …) touches LinkedIn not at all.
Each saved job keeps a description snapshot so agents can tailor résumés or
cover letters later even if the posting is taken down.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ..config import data_dir, ensure_private, open_private
from ..errors import LinkedInError
from ..models import JobEvent, SavedJob, SavedJobDraft, SavedJobsList, SavedJobSummary

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


def _optional_text(row: sqlite3.Row, column: str) -> str | None:
    value = cast(object, row[column])
    if value is None:
        return None
    if not isinstance(value, str):
        raise LinkedInError(f"Tracker database column {column!r} is not text.")
    return value


def _required_text(row: sqlite3.Row, column: str) -> str:
    value = _optional_text(row, column)
    if value is None:
        raise LinkedInError(f"Tracker database column {column!r} is unexpectedly empty.")
    return value


def _required_int(row: sqlite3.Row, column: str) -> int:
    value = cast(object, row[column])
    if not isinstance(value, int) or isinstance(value, bool):
        raise LinkedInError(f"Tracker database column {column!r} is not an integer.")
    return value


class TrackerStore:
    def __init__(self, path: Path | None = None):
        self._path = path

    @property
    def path(self) -> Path:
        return self._path or (data_dir() / "tracker.db")

    def _connect(self) -> sqlite3.Connection:
        path = self.path
        # sqlite3.connect() follows symlinks and creates missing files at the umask
        # default (0644). Both matter: the DB holds salary and interview notes, and a
        # symlink planted here would redirect the write to whatever it points at.
        if path.is_symlink():
            raise LinkedInError(
                f"Refusing to open the tracker database: {path} is a symlink. "
                "Remove it, or point LINKEDIN_MCP_DIR at a directory you control."
            )
        if not path.exists():
            try:
                os.close(open_private(path, exclusive=True))
            except FileExistsError as exc:  # appeared between the checks — a race
                raise LinkedInError(
                    f"Refusing to open the tracker database: {path} was created underneath us. Remove it and retry."
                ) from exc
        ensure_private(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        return conn

    # ------------------------------------------------------------------- writes

    def save(self, draft: SavedJobDraft) -> tuple[SavedJob, bool]:
        """Insert a job; if it already exists, just record the note. Returns
        (saved_job, created)."""
        _validate_status(draft.status)
        with closing(self._connect()) as conn, conn:
            existing = cast(
                sqlite3.Row | None,
                conn.execute("SELECT job_id FROM saved_jobs WHERE job_id = ?", (draft.job_id,)).fetchone(),
            )
            now = _now()
            if existing:
                if draft.note:
                    conn.execute(
                        "INSERT INTO job_events (job_id, at, note) VALUES (?, ?, ?)",
                        (draft.job_id, now, draft.note),
                    )
                    conn.execute("UPDATE saved_jobs SET updated_at = ? WHERE job_id = ?", (now, draft.job_id))
                return self._get(conn, draft.job_id), False
            conn.execute(
                """INSERT INTO saved_jobs
                   (job_id, title, company, location, url, salary, description,
                    status, saved_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft.job_id,
                    draft.title,
                    draft.company,
                    draft.location,
                    draft.url,
                    draft.salary,
                    draft.description,
                    draft.status,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO job_events (job_id, at, status, note) VALUES (?, ?, ?, ?)",
                (draft.job_id, now, draft.status, draft.note or "saved"),
            )
            return self._get(conn, draft.job_id), True

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
            cursor = conn.execute("UPDATE saved_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id))
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
        row = cast(
            sqlite3.Row | None,
            conn.execute("SELECT * FROM saved_jobs WHERE job_id = ?", (job_id,)).fetchone(),
        )
        if row is None:
            raise LinkedInError(f"Job {job_id} is not in the tracker.")
        job = SavedJob(
            job_id=_required_text(row, "job_id"),
            title=_optional_text(row, "title"),
            company=_optional_text(row, "company"),
            location=_optional_text(row, "location"),
            url=_optional_text(row, "url"),
            salary=_optional_text(row, "salary"),
            description=_optional_text(row, "description"),
            status=_required_text(row, "status"),
            saved_at=_required_text(row, "saved_at"),
            updated_at=_required_text(row, "updated_at"),
        )
        event_rows = cast(
            list[sqlite3.Row],
            conn.execute(
                "SELECT at, status, note FROM job_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall(),
        )
        events = [
            JobEvent(
                at=_required_text(event, "at"),
                status=_optional_text(event, "status"),
                note=_optional_text(event, "note"),
            )
            for event in event_rows
        ]
        job.events = events
        return job

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
            rows = cast(
                list[sqlite3.Row],
                conn.execute(
                    f"SELECT job_id, title, company, location, status, updated_at, url "
                    f"FROM saved_jobs {clause} ORDER BY updated_at DESC",
                    args,
                ).fetchall(),
            )
            count_rows = cast(
                list[sqlite3.Row],
                conn.execute("SELECT status, COUNT(*) AS count FROM saved_jobs GROUP BY status").fetchall(),
            )
            counts = {_required_text(row, "status"): _required_int(row, "count") for row in count_rows}
        return SavedJobsList(
            total=sum(counts.values()),
            by_status=counts,
            jobs=[
                SavedJobSummary(
                    job_id=_required_text(row, "job_id"),
                    title=_optional_text(row, "title"),
                    company=_optional_text(row, "company"),
                    location=_optional_text(row, "location"),
                    status=_required_text(row, "status"),
                    updated_at=_required_text(row, "updated_at"),
                    url=_optional_text(row, "url"),
                )
                for row in rows
            ],
        )
