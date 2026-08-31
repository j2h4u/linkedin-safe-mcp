"""Direct behavior coverage for the thin MCP tool adapters."""

from __future__ import annotations

import pytest

from linkedin_mcp import server
from linkedin_mcp.errors import LinkedInError
from linkedin_mcp.models import JobCard, JobDetail, JobSearchResults, SearchJobsInput

EXPECTED_TOOL_NAMES = {
    "auth_status",
    "login",
    "logout",
    "get_my_profile",
    "create_post",
    "delete_post",
    "comment_on_post",
    "like_post",
    "search_jobs",
    "get_job",
}
REMOVED_TRACKER_TOOL_NAMES = {
    "save_job",
    "get_saved_job",
    "list_saved_jobs",
    "update_job_status",
    "add_job_note",
    "remove_saved_job",
}


class _Jobs:
    def __init__(self, detail: JobDetail | None = None, cards: list[JobCard] | None = None):
        self.detail = detail
        self.cards = cards or []

    def job(self, job_id: str) -> JobDetail:
        if self.detail is None:
            raise LinkedInError(f"job {job_id} unavailable")
        return self.detail

    def search(self, _params: dict[str, object], *, limit: int) -> list[JobCard]:
        return self.cards[:limit]


def test_tool_surface_removes_tracker_and_keeps_public_tools():
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}

    assert names == EXPECTED_TOOL_NAMES
    assert not names & REMOVED_TRACKER_TOOL_NAMES


def test_search_jobs_remains_public_and_guest(monkeypatch: pytest.MonkeyPatch):
    jobs = _Jobs(cards=[JobCard(job_id="123", title="Staff Engineer", url="https://www.linkedin.com/jobs/view/123")])
    monkeypatch.setattr(server, "_jobs", lambda: jobs)

    result = server.search_jobs(SearchJobsInput(keywords="python", limit=1))

    assert isinstance(result, JobSearchResults)
    assert result.count == 1
    assert result.jobs[0].job_id == "123"


def test_get_job_remains_public_and_guest(monkeypatch: pytest.MonkeyPatch):
    detail = JobDetail(job_id="123", title="Staff Engineer", url="https://www.linkedin.com/jobs/view/123")
    monkeypatch.setattr(server, "_jobs", lambda: _Jobs(detail=detail))

    assert server.get_job("123") == detail
