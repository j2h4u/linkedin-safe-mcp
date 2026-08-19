import pytest

from linkedin_mcp.api.urns import extract_job_id, extract_post_urn, post_url


def test_post_urn_passthrough():
    assert (
        extract_post_urn("urn:li:share:6844785523593134080") == "urn:li:share:6844785523593134080"
    )
    assert extract_post_urn("urn:li:ugcPost:123456") == "urn:li:ugcPost:123456"


def test_post_urn_from_feed_url():
    url = "https://www.linkedin.com/feed/update/urn:li:activity:7215551234567890123/?utm=x"
    assert extract_post_urn(url) == "urn:li:activity:7215551234567890123"


def test_post_urn_from_posts_slug_url():
    url = "https://www.linkedin.com/posts/jane-doe_ai-agents-activity-7215551234567890123-Ab_C"
    assert extract_post_urn(url) == "urn:li:activity:7215551234567890123"


def test_post_urn_rejects_garbage():
    with pytest.raises(ValueError):
        extract_post_urn("https://example.com/nothing")


def test_job_id_variants():
    assert extract_job_id("4449049579") == "4449049579"
    assert extract_job_id("urn:li:jobPosting:4449049579") == "4449049579"
    assert extract_job_id("https://www.linkedin.com/jobs/view/4449049579") == "4449049579"
    assert (
        extract_job_id(
            "https://www.linkedin.com/jobs/view/software-engineer-at-twin-prime-4449049579?position=1"
        )
        == "4449049579"
    )
    assert (
        extract_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4449049579&keywords=x")
        == "4449049579"
    )


def test_job_id_not_confused_by_digits_in_slug():
    url = "https://www.linkedin.com/jobs/view/covid19-response-123456-lead-4449049579"
    assert extract_job_id(url) == "4449049579"


def test_job_id_rejects_garbage():
    with pytest.raises(ValueError):
        extract_job_id("not a job")


def test_post_url():
    assert post_url("urn:li:share:1") == "https://www.linkedin.com/feed/update/urn:li:share:1/"
