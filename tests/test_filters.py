import pytest

from linkedin_mcp.jobs.filters import build_search_params


def test_minimal_params():
    params = build_search_params("python developer")
    assert params == {"keywords": "python developer", "sortBy": "R"}


def test_full_params():
    params = build_search_params(
        "ml engineer",
        location="India",
        workplace="remote",
        time_posted="past_week",
        experience_levels=["entry", "associate"],
        job_types=["full_time", "contract"],
        easy_apply=True,
        sort="recent",
    )
    assert params == {
        "keywords": "ml engineer",
        "location": "India",
        "f_WT": "2",
        "f_TPR": "r604800",
        "f_E": "2,3",
        "f_JT": "F,C",
        "f_AL": "true",
        "sortBy": "DD",
    }


def test_time_posted_any_omitted():
    assert "f_TPR" not in build_search_params("x", time_posted="any")


def test_unknown_value_raises_with_allowed_list():
    with pytest.raises(ValueError, match="past_24h"):
        build_search_params("x", time_posted="yesterday")
