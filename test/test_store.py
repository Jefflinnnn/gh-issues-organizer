import dataclasses
import pathlib
import sqlite3

import pytest

from gh_triage import github_api
from gh_triage import store


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
