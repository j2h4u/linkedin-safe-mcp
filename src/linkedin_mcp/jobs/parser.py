"""Parsers for LinkedIn guest-endpoint HTML.

Selectors were verified against live responses (see tests/fixtures). LinkedIn can
change this markup at any time; parsers therefore treat every field except job_id
as optional and the tests pin the current shape so breakage is caught loudly.
"""

from __future__ import annotations

import html as html_mod
import re

from bs4 import BeautifulSoup, Tag

from ..api.urns import job_url
from ..models import JobCard, JobDetail

_JOB_URN_RE = re.compile(r"jobPosting:(\d+)")
_APPLY_URL_RE = re.compile(r'https?://[^"<>\\]+')

_CRITERIA_FIELDS = {
    "seniority level": "seniority",
    "employment type": "employment_type",
    "job function": "job_functions",
    "industries": "industries",
}


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def _text(el: Tag | None) -> str | None:
    return _clean(el.get_text(" ", strip=True)) if el else None


def _href(el: Tag | None) -> str | None:
    if el is None or not el.has_attr("href"):
        return None
    return str(el["href"]).split("?")[0] or None


def _block_text(el: Tag | None) -> str | None:
    """Readable plain text from rich description HTML (bullets kept, spacing sane)."""
    if el is None:
        return None
    for br in el.find_all("br"):
        br.replace_with("\n")
    for li in el.find_all("li"):
        li.insert_before("\n• ")
        li.append("\n")
    text = el.get_text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def parse_search_results(html: str) -> list[JobCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[JobCard] = []
    for div in soup.select("div.base-search-card"):
        match = _JOB_URN_RE.search(div.get("data-entity-urn") or "")
        if not match:
            continue
        job_id = match.group(1)
        company_link = div.select_one("h4.base-search-card__subtitle a")
        time_el = div.select_one("time")
        cards.append(
            JobCard(
                job_id=job_id,
                title=_text(div.select_one("h3.base-search-card__title")),
                company=_text(company_link)
                or _text(div.select_one("h4.base-search-card__subtitle")),
                location=_text(div.select_one("span.job-search-card__location")),
                url=_href(div.select_one("a.base-card__full-link")) or job_url(job_id),
                company_url=_href(company_link),
                posted_date=time_el.get("datetime") if time_el else None,
                posted_text=_text(time_el),
                salary=_text(div.select_one(".job-search-card__salary-info")),
            )
        )
    return cards


def parse_job_detail(html: str, job_id: str) -> JobDetail:
    soup = BeautifulSoup(html, "html.parser")

    criteria: dict[str, str] = {}
    for item in soup.select("li.description__job-criteria-item"):
        key = (_text(item.select_one("h3")) or "").lower()
        value = _text(item.select_one("span"))
        field = _CRITERIA_FIELDS.get(key)
        if field and value:
            criteria[field] = value

    apply_url = None
    apply_el = soup.find("code", id="applyUrl")
    if apply_el:
        match = _APPLY_URL_RE.search(str(apply_el))
        if match:
            apply_url = html_mod.unescape(match.group(0))

    company_link = soup.select_one("a.topcard__org-name-link")
    return JobDetail(
        job_id=job_id,
        title=_text(soup.select_one("h2.top-card-layout__title")),
        company=_text(company_link),
        company_url=_href(company_link),
        location=_text(soup.select_one("span.topcard__flavor--bullet")),
        url=job_url(job_id),
        posted_text=_text(soup.select_one("span.posted-time-ago__text")),
        applicants=_text(soup.select_one(".num-applicants__caption")),
        salary=_text(soup.select_one("div.compensation__salary"))
        or _text(soup.select_one(".salary")),
        apply_url=apply_url,
        description=_block_text(soup.select_one("div.show-more-less-html__markup")),
        **criteria,
    )
