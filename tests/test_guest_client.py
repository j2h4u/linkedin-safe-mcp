"""Behavior tests for the account-safe LinkedIn guest jobs client."""

from collections.abc import Sequence

import httpx
import pytest

from conftest import fixture_text
from linkedin_mcp.errors import LinkedInError, RateLimitedError
from linkedin_mcp.jobs import guest_client as guest_client_module
from linkedin_mcp.jobs.guest_client import SEARCH_URL, GuestJobsClient


def _mock_http(
    responses: Sequence[httpx.Response | Exception],
) -> tuple[httpx.Client, list[httpx.Request]]:
    remaining = iter(responses)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        try:
            response = next(remaining)
        except StopIteration as exc:
            raise AssertionError("the mock received more requests than expected") from exc
        if isinstance(response, Exception):
            raise response
        return response

    return httpx.Client(transport=httpx.MockTransport(handler)), requests


def _response(status: int, text: str = "response") -> httpx.Response:
    return httpx.Response(status, text=text)


def _without_waiting(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []

    def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    def no_jitter(_start: float, _stop: float) -> float:
        return 0.0

    monkeypatch.setattr(guest_client_module.time, "sleep", record_sleep)
    monkeypatch.setattr(guest_client_module.random, "uniform", no_jitter)
    return sleeps


def _search_page(*job_ids: str) -> str:
    return "".join(
        f'<div class="base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">'
        f'<h3 class="base-search-card__title">Job {job_id}</h3>'
        "</div>"
        for job_id in job_ids
    )


def test_get_success_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(200, "cached body")])
    with http:
        client = GuestJobsClient(http)
        params = {"keywords": "python", "location": "Almaty"}
        assert client._get(SEARCH_URL, params, 60.0) == "cached body"
        assert client._get(SEARCH_URL, dict(reversed(list(params.items()))), 60.0) == "cached body"

    assert len(requests) == 1
    assert requests[0].url.params["keywords"] == "python"
    assert sleeps == []


def test_get_retries_network_error_then_caches_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([httpx.ConnectError("offline"), _response(200, "recovered")])
    with http:
        client = GuestJobsClient(http)
        assert client._get(SEARCH_URL, {}, 60.0) == "recovered"

    assert len(requests) == 2
    assert sleeps == [2.5]


def test_get_404_fails_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(404)])
    with http, pytest.raises(LinkedInError, match="returned 404"):
        GuestJobsClient(http)._get(SEARCH_URL, {}, 60.0)

    assert len(requests) == 1
    assert sleeps == []


def test_get_non_retryable_status_fails_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(403)])
    with http, pytest.raises(LinkedInError, match="HTTP 403"):
        GuestJobsClient(http)._get(SEARCH_URL, {}, 60.0)

    assert len(requests) == 1
    assert sleeps == []


def test_get_rate_limit_retries_then_raises_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(429), _response(429), _response(429)])
    with http, pytest.raises(RateLimitedError, match="rate-limiting anonymous"):
        GuestJobsClient(http)._get(SEARCH_URL, {}, 60.0)

    assert len(requests) == 3
    assert sleeps == [2.5, 6.0]


def test_get_server_error_retries_then_raises_last_result(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(500), _response(503), _response(502)])
    with http, pytest.raises(LinkedInError, match="last result: 502"):
        GuestJobsClient(http)._get(SEARCH_URL, {}, 60.0)

    assert len(requests) == 3
    assert sleeps == [2.5, 6.0]


def test_get_network_failures_report_last_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    failures = [httpx.ConnectError("offline") for _ in range(3)]
    http, requests = _mock_http(failures)
    with http, pytest.raises(LinkedInError, match="network error: offline"):
        GuestJobsClient(http)._get(SEARCH_URL, {}, 60.0)

    assert len(requests) == 3
    assert sleeps == [2.5, 6.0]


def test_search_deduplicates_pages_and_tracks_source_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(200, _search_page("1", "2")), _response(200, _search_page("2", "3"))])
    with http:
        jobs = GuestJobsClient(http).search({"keywords": "python"}, limit=3)

    assert [job.job_id for job in jobs] == ["1", "2", "3"]
    assert [request.url.params["start"] for request in requests] == ["0", "2"]
    assert sleeps == [1.0]


def test_search_stops_when_page_is_empty_or_only_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(200, _search_page("1")), _response(200, _search_page("1"))])
    with http:
        jobs = GuestJobsClient(http).search({"keywords": "python"}, limit=5)

    assert [job.job_id for job in jobs] == ["1"]
    assert [request.url.params["start"] for request in requests] == ["0", "1"]
    assert sleeps == [1.0]

    empty_http, empty_requests = _mock_http([_response(200, "")])
    with empty_http:
        assert GuestJobsClient(empty_http).search({"keywords": "python"}) == []
    assert len(empty_requests) == 1


def test_search_respects_limit_without_sleeping_after_final_page(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(200, _search_page("1", "2"))])
    with http:
        jobs = GuestJobsClient(http).search({"keywords": "python"}, limit=1)

    assert [job.job_id for job in jobs] == ["1"]
    assert len(requests) == 1
    assert sleeps == []


def test_search_zero_limit_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_waiting(monkeypatch)
    http, requests = _mock_http([])
    with http:
        assert GuestJobsClient(http).search({"keywords": "python"}, limit=0) == []
    assert requests == []


def test_job_fetches_and_parses_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_waiting(monkeypatch)
    http, requests = _mock_http([_response(200, fixture_text("job_detail.html"))])
    with http:
        detail = GuestJobsClient(http).job("https://www.linkedin.com/jobs/view/4449049579")

    assert detail.job_id == "4449049579"
    assert detail.title == "Software Engineer"
    assert requests[0].url.path.endswith("/jobPosting/4449049579")
