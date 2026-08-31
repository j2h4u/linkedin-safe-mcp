"""Pydantic models returned by MCP tools (they become each tool's structured output
schema, so field names and descriptions are part of the agent-facing contract)."""

from __future__ import annotations

from typing import Literal

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


class CreatePostInput(BaseModel):
    text: str
    visibility: Literal["PUBLIC", "CONNECTIONS"] = "PUBLIC"
    link: str | None = None
    link_title: str | None = None
    link_description: str | None = None
    image_path: str | None = None


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


class SearchJobsInput(BaseModel):
    keywords: str
    location: str | None = None
    workplace: Literal["onsite", "remote", "hybrid"] | None = None
    time_posted: Literal["any", "past_24h", "past_week", "past_month"] = "any"
    experience_levels: (
        list[Literal["internship", "entry", "associate", "mid_senior", "director", "executive"]] | None
    ) = None
    job_types: (
        list[Literal["full_time", "part_time", "contract", "temporary", "internship", "volunteer", "other"]] | None
    ) = None
    easy_apply: bool = False
    sort: Literal["relevance", "recent"] = "relevance"
    limit: int = 25


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
    apply_url: str | None = Field(default=None, description="External ATS apply URL when the posting is not Easy Apply")
    description: str | None = None
