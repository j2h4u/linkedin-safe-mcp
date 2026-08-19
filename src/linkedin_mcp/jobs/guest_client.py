"""HTTP client for LinkedIn's public guest job endpoints.

No login and no cookies are involved — these are the endpoints behind the
logged-out jobs pages, so the user's LinkedIn account is never at risk. The
trade-off is IP-based throttling (HTTP 429), which we soften with an in-memory
TTL cache, retry with backoff, and a politeness delay between page fetches.
"""

from __future__ import annotations

import logging
import os
import random
import time
from urllib.parse import urlencode

import httpx

from ..api.urns import extract_job_id
from ..errors import LinkedInError, RateLimitedError
from ..models import JobCard, JobDetail
from .parser import parse_job_detail, parse_search_results

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

SEARCH_CACHE_TTL = 600.0  # 10 min — listings churn
JOB_CACHE_TTL = 6 * 3600.0  # 6 h — postings are static
_MAX_START = 975  # LinkedIn stops serving guest results past ~1000
_RETRY_DELAYS = (2.5, 6.0)

_RATE_LIMIT_MSG = (
    "LinkedIn is rate-limiting anonymous job requests from this IP (HTTP 429). "
    "Wait a minute or two before searching again, and prefer smaller `limit` values."
)


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": os.environ.get(
            "LINKEDIN_MCP_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


class _TTLCache:
    def __init__(self):
        self._data: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        self._data.pop(key, None)
        return None

    def set(self, key: str, value: str, ttl: float) -> None:
        self._data[key] = (time.monotonic() + ttl, value)


class GuestJobsClient:
    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(
            headers=_default_headers(), timeout=20.0, follow_redirects=True
        )
        self._cache = _TTLCache()

    def _get(self, url: str, params: dict[str, str], ttl: float) -> str:
        key = f"{url}?{urlencode(sorted(params.items()))}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        last_status = None
        for attempt, delay in enumerate((0.0, *_RETRY_DELAYS)):
            if delay:
                time.sleep(delay + random.uniform(0, 0.8))
            try:
                resp = self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                last_status = f"network error: {exc}"
                logger.warning("guest request failed (attempt %d): %s", attempt + 1, exc)
                continue
            if resp.status_code == 200:
                self._cache.set(key, resp.text, ttl)
                return resp.text
            if resp.status_code == 404:
                raise LinkedInError(
                    "LinkedIn returned 404 — this job no longer exists or was never public."
                )
            last_status = resp.status_code
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.info(
                    "guest endpoint %s (attempt %d), backing off", resp.status_code, attempt + 1
                )
                continue
            raise LinkedInError(f"LinkedIn guest endpoint returned HTTP {resp.status_code}.")

        if last_status == 429:
            raise RateLimitedError(_RATE_LIMIT_MSG)
        raise LinkedInError(
            f"LinkedIn guest endpoint kept failing (last result: {last_status}). Try again shortly."
        )

    def search(self, params: dict[str, str], limit: int = 25) -> list[JobCard]:
        results: list[JobCard] = []
        seen: set[str] = set()
        start = 0
        while len(results) < limit and start <= _MAX_START:
            page = parse_search_results(
                self._get(SEARCH_URL, {**params, "start": str(start)}, SEARCH_CACHE_TTL)
            )
            if not page:
                break
            fresh = [card for card in page if card.job_id not in seen]
            seen.update(card.job_id for card in page)
            results.extend(fresh)
            start += len(page)
            if not fresh:
                break  # only repeats left — LinkedIn is padding with promoted posts
            if len(results) < limit:
                time.sleep(1.0 + random.uniform(0, 0.6))  # politeness between pages
        return results[:limit]

    def job(self, job: str) -> JobDetail:
        job_id = extract_job_id(job)
        html = self._get(JOB_URL.format(job_id=job_id), {}, JOB_CACHE_TTL)
        return parse_job_detail(html, job_id)
