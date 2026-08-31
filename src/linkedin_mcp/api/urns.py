"""Helpers for LinkedIn URNs, post URLs, and job IDs.

Agents frequently hold a browser URL rather than a URN, so every tool that
targets a post or job accepts either and normalizes here.
"""

from __future__ import annotations

import re
from urllib.parse import quote

_POST_URN_RE = re.compile(r"urn:li:(?:share|ugcPost|activity):\d+")
# e.g. linkedin.com/posts/jane-doe_ai-agents-activity-7215551234567890123-Ab_C
_ACTIVITY_SLUG_RE = re.compile(r"activity[-:](\d{10,25})")
_JOB_ID_RE = re.compile(r"(?:jobPosting[:/]|jobs/view/(?:[^/?#]*?-)?|currentJobId=)(\d{6,15})(?=[/?#&]|$)")


def extract_post_urn(value: str) -> str:
    """Accept a post URN or any common LinkedIn post URL and return the URN."""
    value = value.strip()
    m = _POST_URN_RE.search(value)
    if m:
        return m.group(0)
    m = _ACTIVITY_SLUG_RE.search(value)
    if m:
        return f"urn:li:activity:{m.group(1)}"
    raise ValueError(
        f"Could not find a LinkedIn post URN in {value!r}. Pass a urn:li:share / "
        "urn:li:ugcPost / urn:li:activity URN, or a linkedin.com post URL "
        "(feed/update/... or posts/...)."
    )


def extract_job_id(value: str) -> str:
    """Accept a job ID, jobPosting URN, or any LinkedIn job URL and return the ID."""
    value = value.strip()
    if value.isdigit():
        return value
    m = _JOB_ID_RE.search(value)
    if m:
        return m.group(1)
    raise ValueError(
        f"Could not find a LinkedIn job ID in {value!r}. Pass the numeric ID, a "
        "urn:li:jobPosting URN, or a linkedin.com/jobs/view/... URL."
    )


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def encode_urn(urn: str) -> str:
    """URL-encode a URN for use inside a REST path segment."""
    return quote(urn, safe="")
