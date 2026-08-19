"""Human-friendly filter values mapped to LinkedIn guest search query params."""

from __future__ import annotations

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


def _lookup(table: dict, value: str, what: str) -> str | None:
    try:
        return table[value]
    except KeyError:
        raise ValueError(f"Unknown {what} {value!r}; allowed: {', '.join(table)}") from None


def build_search_params(
    keywords: str,
    location: str | None = None,
    workplace: str | None = None,
    time_posted: str = "any",
    experience_levels: list[str] | None = None,
    job_types: list[str] | None = None,
    easy_apply: bool = False,
    sort: str = "relevance",
) -> dict[str, str]:
    params: dict[str, str] = {"keywords": keywords}
    if location:
        params["location"] = location
    if workplace:
        params["f_WT"] = _lookup(WORKPLACE, workplace, "workplace")
    tpr = _lookup(TIME_POSTED, time_posted, "time_posted")
    if tpr:
        params["f_TPR"] = tpr
    if experience_levels:
        params["f_E"] = ",".join(
            _lookup(EXPERIENCE, e, "experience level") for e in experience_levels
        )
    if job_types:
        params["f_JT"] = ",".join(_lookup(JOB_TYPE, j, "job type") for j in job_types)
    if easy_apply:
        params["f_AL"] = "true"
    sort_value = _lookup(SORT, sort, "sort")
    if sort_value:
        params["sortBy"] = sort_value
    return params
