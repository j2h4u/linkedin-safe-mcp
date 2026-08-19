import pytest

from linkedin_mcp.errors import LinkedInError
from linkedin_mcp.tracker.store import TrackerStore


@pytest.fixture
def store(tmp_path):
    return TrackerStore(path=tmp_path / "tracker.db")


def test_save_and_get(store):
    saved, created = store.save(
        "111", title="Engineer", company="Acme", url="https://x", description="desc"
    )
    assert created is True
    assert saved.status == "interested"
    assert saved.events and saved.events[0].note == "saved"
    fetched = store.get("111")
    assert fetched.description == "desc"


def test_save_is_idempotent_and_appends_note(store):
    store.save("111", title="Engineer")
    saved, created = store.save("111", note="found again via remote search")
    assert created is False
    assert any(e.note == "found again via remote search" for e in saved.events)


def test_status_pipeline(store):
    store.save("111", title="Engineer")
    updated = store.update_status("111", "applied", note="sent resume")
    assert updated.status == "applied"
    updated = store.update_status("111", "interviewing")
    assert [e.status for e in updated.events if e.status] == [
        "interested",
        "applied",
        "interviewing",
    ]


def test_add_note_keeps_status(store):
    store.save("111")
    noted = store.add_note("111", "recruiter: Sam")
    assert noted.status == "interested"
    assert noted.events[-1].note == "recruiter: Sam"


def test_invalid_status_rejected(store):
    with pytest.raises(ValueError, match="interviewing"):
        store.save("111", status="ghosted")


def test_update_unknown_job_raises(store):
    with pytest.raises(LinkedInError, match="not in the tracker"):
        store.update_status("999", "applied")


def test_list_filters_and_counts(store):
    store.save("1", title="Python Dev", company="Acme")
    store.save("2", title="Go Dev", company="Beta")
    store.update_status("2", "applied")
    all_jobs = store.list()
    assert all_jobs.total == 2
    assert all_jobs.by_status == {"interested": 1, "applied": 1}
    only_applied = store.list(status="applied")
    assert [job.job_id for job in only_applied.jobs] == ["2"]
    by_text = store.list(search="python")
    assert [job.job_id for job in by_text.jobs] == ["1"]


def test_remove(store):
    store.save("1")
    assert store.remove("1") is True
    assert store.remove("1") is False
    assert store.list().total == 0
