"""Direct behavior coverage for the thin MCP tool adapters."""

from __future__ import annotations

import pytest

from linkedin_mcp import server
from linkedin_mcp.errors import LinkedInError
from linkedin_mcp.models import JobDetail, SavedJob, SavedJobDraft


def _saved(job_id: str) -> SavedJob:
    return SavedJob(
        job_id=job_id,
        status="interested",
        saved_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


class _Jobs:
    def __init__(self, detail: JobDetail | None = None):
        self.detail = detail

    def job(self, job_id: str) -> JobDetail:
        if self.detail is None:
            raise LinkedInError(f"job {job_id} unavailable")
        return self.detail


class _Tracker:
    def __init__(self):
        self.draft: SavedJobDraft | None = None

    def save(self, draft: SavedJobDraft) -> tuple[SavedJob, bool]:
        self.draft = draft
        return _saved(draft.job_id), True


def test_save_job_passes_posting_snapshot_to_tracker(monkeypatch: pytest.MonkeyPatch):
    jobs = _Jobs(
        JobDetail(
            job_id="123",
            title="Staff Python Engineer",
            company="Acme",
            location="Remote",
            url="https://www.linkedin.com/jobs/view/123",
            salary="$200k",
            description="Build reliable systems.",
        )
    )
    tracker = _Tracker()
    monkeypatch.setattr(server, "_jobs", lambda: jobs)
    monkeypatch.setattr(server, "_tracker", lambda: tracker)

    saved = server.save_job("123", status="applied", note="sent resume")

    assert saved.job_id == "123"
    assert tracker.draft == SavedJobDraft(
        job_id="123",
        title="Staff Python Engineer",
        company="Acme",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/123",
        salary="$200k",
        description="Build reliable systems.",
        status="applied",
        note="sent resume",
    )


def test_save_job_keeps_reference_when_posting_is_unavailable(monkeypatch: pytest.MonkeyPatch):
    jobs = _Jobs()
    tracker = _Tracker()
    monkeypatch.setattr(server, "_jobs", lambda: jobs)
    monkeypatch.setattr(server, "_tracker", lambda: tracker)

    saved = server.save_job("456", note="review manually")

    assert saved.job_id == "456"
    assert tracker.draft == SavedJobDraft(job_id="456", note="review manually")
