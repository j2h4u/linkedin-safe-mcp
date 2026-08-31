"""Parser tests pinned against live guest-endpoint HTML captured 2026-08-05.

If LinkedIn changes its markup these tests fail first — recapture fixtures with:
curl 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=x'
"""

from conftest import fixture_text
from linkedin_mcp.jobs.parser import parse_job_detail, parse_search_results


def test_search_results_parse_all_cards():
    jobs = parse_search_results(fixture_text("search_results.html"))
    assert len(jobs) >= 10
    assert all(job.job_id.isdigit() for job in jobs)
    assert all(job.url.startswith("https://") for job in jobs)


def test_search_first_card_fields():
    first = parse_search_results(fixture_text("search_results.html"))[0]
    assert first.job_id == "4449049579"
    assert first.title == "Software Engineer"
    assert first.company == "Twin Prime"
    assert first.location == "New York, NY"
    assert first.company_url == "https://www.linkedin.com/company/twinprime"
    assert first.posted_date == "2026-08-04"
    assert "?" not in first.url  # tracking params stripped


def test_search_remote_fixture_parses():
    jobs = parse_search_results(fixture_text("search_results_remote.html"))
    assert len(jobs) >= 5
    assert all(job.job_id for job in jobs)


def test_job_detail_fields():
    detail = parse_job_detail(fixture_text("job_detail.html"), "4449049579")
    assert detail.title == "Software Engineer"
    assert detail.company == "Twin Prime"
    assert detail.location == "New York, NY"
    assert detail.employment_type == "Full-time"
    assert detail.industries == "Artificial Intelligence"
    assert detail.applicants and "applicants" in detail.applicants
    assert detail.description and len(detail.description) > 200
    assert detail.url == "https://www.linkedin.com/jobs/view/4449049579"


def test_job_detail_handles_empty_html():
    detail = parse_job_detail("<html><body></body></html>", "123")
    assert detail.job_id == "123"
    assert detail.title is None
    assert detail.description is None
