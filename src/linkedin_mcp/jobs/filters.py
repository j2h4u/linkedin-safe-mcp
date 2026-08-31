"""Human-friendly filter values mapped to LinkedIn guest search query params."""

from __future__ import annotations

from collections.abc import Mapping

from ..models import SearchJobsInput

TIME_POSTED = {
    "any": None,
    "past_24h": "r86400",
    "past_week": "r604800",
    "past_month": "r2592000",
}
EXPERIENCE = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid_senior": "4",
    "director": "5",
    "executive": "6",
}
JOB_TYPE = {
    "full_time": "F",
    "part_time": "P",
    "contract": "C",
    "temporary": "T",
    "internship": "I",
    "volunteer": "V",
    "other": "O",
}
WORKPLACE = {"onsite": "1", "remote": "2", "hybrid": "3"}
SORT = {"relevance": "R", "recent": "DD"}


def _lookup(table: Mapping[str, str | None], value: str, what: str) -> str | None:
    try:
        return table[value]
    except KeyError:
        raise ValueError(f"Unknown {what} {value!r}; allowed: {', '.join(table)}") from None


def build_search_params(query: SearchJobsInput) -> dict[str, str]:
    params: dict[str, str] = {"keywords": query.keywords}
    if query.location:
        params["location"] = query.location
    if query.workplace:
        workplace = _lookup(WORKPLACE, query.workplace, "workplace")
        if workplace:
            params["f_WT"] = workplace
    time_posted = _lookup(TIME_POSTED, query.time_posted, "time_posted")
    if time_posted:
        params["f_TPR"] = time_posted
    if query.experience_levels:
        values = (_lookup(EXPERIENCE, value, "experience level") for value in query.experience_levels)
        params["f_E"] = ",".join(value for value in values if value is not None)
    if query.job_types:
        values = (_lookup(JOB_TYPE, value, "job type") for value in query.job_types)
        params["f_JT"] = ",".join(value for value in values if value is not None)
    if query.easy_apply:
        params["f_AL"] = "true"
    sort = _lookup(SORT, query.sort, "sort")
    if sort:
        params["sortBy"] = sort
    return params
