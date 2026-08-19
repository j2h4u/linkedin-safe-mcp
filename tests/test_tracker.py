import sqlite3
from contextlib import closing

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


# --------------------------------------------------------------- security regressions


def test_tracker_db_is_private(store, tmp_path):
    """The tracker holds salary talk, recruiter names and interview dates.

    sqlite3.connect() creates the file at the umask default (0644), which left
    the user's whole job hunt readable by every other local account.
    """
    store.save("111", title="Engineer", company="Acme")
    assert (tmp_path / "tracker.db").stat().st_mode & 0o777 == 0o600


def test_tracker_db_tightens_a_legacy_loose_file(tmp_path):
    """A DB created by an older version must be tightened on next use."""
    path = tmp_path / "tracker.db"
    TrackerStore(path=path).save("1", title="t")
    path.chmod(0o644)

    TrackerStore(path=path).list()
    assert path.stat().st_mode & 0o777 == 0o600


def test_tracker_refuses_a_symlinked_db_path(tmp_path):
    """sqlite3.connect() follows symlinks, and pre-creating the file does not stop it.

    A link planted at the DB path was an arbitrary-file-create/clobber primitive:
    SQLite happily wrote its header through the link, at 0644.
    """
    victim = tmp_path / "victim.txt"
    victim.write_text("do not clobber")
    link = tmp_path / "tracker.db"
    link.symlink_to(victim)

    with pytest.raises(LinkedInError, match="symlink"):
        TrackerStore(path=link).list()
    assert victim.read_text() == "do not clobber"


def test_tracker_refuses_a_dangling_symlinked_db_path(tmp_path):
    """A link to a non-existent target let SQLite create that target at 0644."""
    link = tmp_path / "tracker.db"
    target = tmp_path / "not-there-yet"
    link.symlink_to(target)

    with pytest.raises(LinkedInError, match="symlink"):
        TrackerStore(path=link).list()
    assert not target.exists()


def test_sqlite_sidecars_inherit_private_mode(tmp_path):
    """WAL/SHM files are created by SQLite, not by us — confirm they inherit 0600."""
    path = tmp_path / "tracker.db"
    store = TrackerStore(path=path)
    store.save("1", title="t")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS x (a)")
        conn.commit()
        for sidecar in tmp_path.glob("tracker.db-*"):
            assert sidecar.stat().st_mode & 0o777 == 0o600, sidecar
