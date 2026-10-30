import dataclasses
import pathlib
import sqlite3

import pytest

from issue_store import github_api
from issue_store import store


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "cache" / "repo.db"


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


def test_connect_creates_folder_tables_and_version(
    db_path: pathlib.Path,
) -> None:
    connection = store.connect(db_path)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"repository", "issues", "comments", "runs"} <= tables
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == store.SCHEMA_VERSION


def test_connect_rejects_other_schema_versions(db_path: pathlib.Path) -> None:
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA user_version = 999")

    with pytest.raises(store.StoreError, match="schema version 999"):
        store.connect(db_path)


def test_save_fetch_stores_every_record(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = store.connect(db_path)

    store.save_fetch(connection, fetch_result, "add", store.utc_now())

    assert _count(connection, "repository") == 1
    assert _count(connection, "issues") == 3
    assert _count(connection, "comments") == 3
    run = connection.execute("SELECT * FROM runs").fetchone()
    assert (run["kind"], run["issues_fetched"], run["total_open"]) == (
        "add",
        3,
        887,
    )


def test_saving_again_updates_instead_of_duplicating(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = store.connect(db_path)
    store.save_fetch(connection, fetch_result, "add", store.utc_now())
    first = fetch_result.issues[0]
    retitled = dataclasses.replace(
        first,
        title="New title",
        recent_comments=first.recent_comments[-1:],
    )
    refetch = dataclasses.replace(
        fetch_result, issues=[retitled, *fetch_result.issues[1:]]
    )

    store.save_fetch(connection, refetch, "sync", store.utc_now())

    assert _count(connection, "issues") == 3
    title = connection.execute(
        "SELECT title FROM issues WHERE id = ?", (first.id,)
    ).fetchone()[0]
    assert title == "New title"
    comment_authors = [
        row[0]
        for row in connection.execute(
            "SELECT author FROM comments WHERE issue_id = ?", (first.id,)
        )
    ]
    assert comment_authors == ["Psylar87"]
    assert _count(connection, "runs") == 2


def test_save_fetch_refuses_a_different_repository(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = store.connect(db_path)
    store.save_fetch(connection, fetch_result, "add", store.utc_now())
    other_repo = dataclasses.replace(
        fetch_result,
        repository=github_api.Repository(
            id="R_other", name_with_owner="someone/else", url="https://x"
        ),
    )

    with pytest.raises(store.StoreError, match="belongs to"):
        store.save_fetch(connection, other_repo, "sync", store.utc_now())

    assert _count(connection, "runs") == 1


def test_summarize_reports_counts(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = store.connect(db_path)
    store.save_fetch(connection, fetch_result, "add", store.utc_now())
    connection.close()

    summary = store.summarize(db_path)

    assert summary.name_with_owner == "vorssaint/vorssaint-utils"
    assert (summary.open_issues, summary.total_open) == (3, 887)


def _saved(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> sqlite3.Connection:
    connection = store.connect(db_path)
    store.save_fetch(connection, fetch_result, "add", store.utc_now())
    return connection


def _fetch_of(
    fetch_result: github_api.FetchResult, *issues: github_api.Issue
) -> github_api.FetchResult:
    return dataclasses.replace(fetch_result, issues=list(issues))


def test_last_activity_is_the_newest_stored_update(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = _saved(db_path, fetch_result)

    assert store.last_activity(connection) == "2026-10-30T04:43:23Z"


def test_last_activity_without_issues_is_when_the_repo_was_added(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = _saved(db_path, _fetch_of(fetch_result))

    added = connection.execute("SELECT added_at FROM repository").fetchone()
    assert store.last_activity(connection) == added["added_at"]


def test_last_activity_needs_a_repository(db_path: pathlib.Path) -> None:
    with pytest.raises(store.StoreError, match="no repository"):
        store.last_activity(store.connect(db_path))


def test_first_save_counts_every_issue_as_created(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = store.connect(db_path)

    changes = store.save_fetch(connection, fetch_result, "add", "t")

    assert changes == store.Changes(created=3)


def test_save_fetch_classifies_each_change(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = _saved(db_path, fetch_result)
    first, second, third = fetch_result.issues
    closed = dataclasses.replace(
        first, state="CLOSED", updated_at="2026-10-31T00:00:00Z"
    )
    commented = dataclasses.replace(
        second, comment_count=9, updated_at="2026-10-31T00:00:01Z"
    )
    brand_new = dataclasses.replace(third, id="new-id", number=9999)

    changes = store.save_fetch(
        connection,
        _fetch_of(fetch_result, closed, commented, brand_new),
        "sync",
        "t",
    )

    assert changes == store.Changes(created=1, closed=1, updated=1)
    state = connection.execute(
        "SELECT state FROM issues WHERE id = ?", (first.id,)
    ).fetchone()
    assert state["state"] == "CLOSED"


def test_save_fetch_counts_a_reopened_issue(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = _saved(db_path, fetch_result)
    issue = fetch_result.issues[0]
    closed = dataclasses.replace(
        issue, state="CLOSED", updated_at="2026-10-31T00:00:00Z"
    )
    reopened = dataclasses.replace(
        issue, state="OPEN", updated_at="2026-11-01T00:00:00Z"
    )
    store.save_fetch(connection, _fetch_of(fetch_result, closed), "sync", "t")

    changes = store.save_fetch(
        connection, _fetch_of(fetch_result, reopened), "sync", "t"
    )

    assert changes == store.Changes(reopened=1)


def test_save_fetch_ignores_an_issue_that_has_not_changed(
    db_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    connection = _saved(db_path, fetch_result)

    changes = store.save_fetch(connection, fetch_result, "sync", "t")

    assert changes == store.Changes()
