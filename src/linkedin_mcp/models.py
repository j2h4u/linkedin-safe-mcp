"""Pydantic models returned by MCP tools (they become each tool's structured output
schema, so field names and descriptions are part of the agent-facing contract)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- auth / profile


class AuthStatus(BaseModel):
    configured: bool = Field(description="Whether LinkedIn app credentials are present")
    authenticated: bool = Field(description="Whether a valid (unexpired) access token exists")
    detail: str
    scopes: str | None = None
    expires_at: str | None = Field(default=None, description="Access token expiry, ISO 8601 UTC")
    profile_name: str | None = None
    setup_instructions: str | None = None


class LoginStarted(BaseModel):
    authorization_url: str
    message: str


class Profile(BaseModel):
    person_urn: str
    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    email: str | None = None
    locale: str | None = None
    picture: str | None = None


# ----------------------------------------------------------------------- posting


class PostResult(BaseModel):
    post_urn: str
    url: str = Field(description="Public URL of the created post")
    backend: str = Field(description="Which LinkedIn API created it: 'rest' or 'ugc'")
    visibility: str


class CommentResult(BaseModel):
    comment_urn: str | None = None
    target_urn: str
    message: str


# -------------------------------------------------------------------------- jobs


class JobCard(BaseModel):
    job_id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    url: str
    company_url: str | None = None
    posted_date: str | None = Field(default=None, description="YYYY-MM-DD if provided")
    posted_text: str | None = Field(default=None, description="e.g. '18 hours ago'")
    salary: str | None = None


class JobSearchResults(BaseModel):
    count: int
    jobs: list[JobCard]
    note: str | None = None


class JobDetail(BaseModel):
    job_id: str
    title: str | None = None
    company: str | None = None
    company_url: str | None = None
    location: str | None = None
    url: str
    posted_text: str | None = None
    applicants: str | None = None
    salary: str | None = None
    seniority: str | None = None
    employment_type: str | None = None
    job_functions: str | None = None
    industries: str | None = None
    apply_url: str | None = Field(
        default=None, description="External ATS apply URL when the posting is not Easy Apply"
    )
    description: str | None = None


# ----------------------------------------------------------------------- tracker


class JobEvent(BaseModel):
    at: str
    status: str | None = None
    note: str | None = None


class SavedJob(BaseModel):
    job_id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    url: str | None = None
    salary: str | None = None
    status: str
    saved_at: str
    updated_at: str
    description: str | None = None
    events: list[JobEvent] = []


class SavedJobSummary(BaseModel):
    job_id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    status: str
    updated_at: str
    url: str | None = None


class SavedJobsList(BaseModel):
    total: int
    by_status: dict[str, int]
    jobs: list[SavedJobSummary]
