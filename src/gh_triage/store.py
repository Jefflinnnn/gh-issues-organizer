"""Stores one repository's issues in a local SQLite file."""

import dataclasses
import datetime
import json
import logging
import pathlib
import sqlite3

from gh_triage import github_api

logger = logging.getLogger(__name__)

# Bump when the tables change. A file with another version is rejected so
# old caches never silently miss columns.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repository (
    -- Exactly one row: the repository this file belongs to.
    id               TEXT PRIMARY KEY,
    name_with_owner  TEXT NOT NULL,
    url              TEXT NOT NULL,
    added_at         TEXT NOT NULL,
    last_synced_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    id                  TEXT PRIMARY KEY,
    number              INTEGER NOT NULL UNIQUE,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    url                 TEXT NOT NULL,
    state               TEXT NOT NULL,
    state_reason        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    closed_at           TEXT,
    author              TEXT,
    author_association  TEXT NOT NULL,
    labels_json         TEXT NOT NULL,
    comment_count       INTEGER NOT NULL,
    reaction_count      INTEGER NOT NULL,
    thumbs_up           INTEGER NOT NULL,
    raw_json            TEXT NOT NULL,
    fetched_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id                  TEXT PRIMARY KEY,
    issue_id            TEXT NOT NULL
                        REFERENCES issues(id) ON DELETE CASCADE,
    author              TEXT,
    author_association  TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    body                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS comments_by_issue ON comments(issue_id);

CREATE TABLE IF NOT EXISTS runs (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                  TEXT NOT NULL,
    started_at            TEXT NOT NULL,
    finished_at           TEXT NOT NULL,
    issues_fetched        INTEGER NOT NULL,
    total_open            INTEGER NOT NULL,
    rate_limit_cost       INTEGER NOT NULL,
    rate_limit_remaining  INTEGER NOT NULL
);
"""


class StoreError(RuntimeError):
    """Raised when a cache file is missing, foreign, or out of date."""


@dataclasses.dataclass(frozen=True)
class RepoSummary:
    """What ``triage list`` shows for one tracked repository.

    Attributes:
        name_with_owner: Canonical ``owner/name``.
        open_issues: Open issues stored in the file.
        total_open: Open issues on GitHub at the last run.
        last_synced_at: ISO 8601 time of the last run.
        path: The cache file.
    """

    name_with_owner: str
    open_issues: int
    total_open: int
    last_synced_at: str
    path: pathlib.Path


def utc_now() -> str:
    """Returns the current UTC time as an ISO 8601 string."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Opens a cache file, creating it and its tables if needed.

    Args:
        path: The SQLite file. Its folder is created if missing.

    Returns:
        An open connection with foreign keys enforced.

    Raises:
        StoreError: The file was made by another schema version.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        with connection:
            connection.executescript(_SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        connection.close()
        raise StoreError(
            f"{path} uses schema version {version}, but this version of the "
            f"tool needs {SCHEMA_VERSION}. Remove the repo and add it again."
        )
    return connection


def save_fetch(
    connection: sqlite3.Connection,
    fetch: github_api.FetchResult,
    kind: str,
    started_at: str,
) -> None:
    """Writes a fetch's repository, issues, comments and run in one step.

    Issues are upserted on their node ID, so saving the same issue twice
    updates it in place. An issue's stored comments are replaced by the
    ones in this fetch.

    Args:
        connection: An open cache file from ``connect``.
        fetch: The data to store.
        kind: What produced the fetch, such as ``"add"``.
        started_at: ISO 8601 time the run began.

    Raises:
        StoreError: The file already belongs to a different repository.
    """
    finished_at = utc_now()
    with connection:
        _save_repository(connection, fetch.repository, finished_at)
        for issue in fetch.issues:
            _upsert_issue(connection, issue, finished_at)
        connection.execute(
            """
            INSERT INTO runs (kind, started_at, finished_at, issues_fetched,
                              total_open, rate_limit_cost,
                              rate_limit_remaining)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                started_at,
                finished_at,
                len(fetch.issues),
                fetch.total_open,
                fetch.rate_limit_cost,
                fetch.rate_limit_remaining,
            ),
        )
    logger.info(
        "Stored %d issues for %s",
        len(fetch.issues),
        fetch.repository.name_with_owner,
    )


def _save_repository(
    connection: sqlite3.Connection,
    repository: github_api.Repository,
    synced_at: str,
) -> None:
    """Records the file's repository, refusing to mix two repositories."""
    row = connection.execute(
        "SELECT id, name_with_owner FROM repository"
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO repository (id, name_with_owner, url, added_at,
                                    last_synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                repository.id,
                repository.name_with_owner,
                repository.url,
                synced_at,
                synced_at,
            ),
        )
        return
    if row["id"] != repository.id:
        raise StoreError(
            f"This cache file belongs to {row['name_with_owner']}, not "
            f"{repository.name_with_owner}."
        )
    connection.execute(
        "UPDATE repository SET name_with_owner = ?, url = ?, "
        "last_synced_at = ?",
        (repository.name_with_owner, repository.url, synced_at),
    )


def _upsert_issue(
    connection: sqlite3.Connection,
    issue: github_api.Issue,
    fetched_at: str,
) -> None:
    """Inserts or updates one issue and replaces its stored comments."""
    connection.execute(
        """
        INSERT INTO issues (id, number, title, body, url, state,
                            state_reason, created_at, updated_at, closed_at,
                            author, author_association, labels_json,
                            comment_count, reaction_count, thumbs_up,
                            raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            number = excluded.number,
            title = excluded.title,
            body = excluded.body,
            url = excluded.url,
            state = excluded.state,
            state_reason = excluded.state_reason,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            closed_at = excluded.closed_at,
            author = excluded.author,
            author_association = excluded.author_association,
            labels_json = excluded.labels_json,
            comment_count = excluded.comment_count,
            reaction_count = excluded.reaction_count,
            thumbs_up = excluded.thumbs_up,
            raw_json = excluded.raw_json,
            fetched_at = excluded.fetched_at
        """,
        (
            issue.id,
            issue.number,
            issue.title,
            issue.body,
            issue.url,
            issue.state,
            issue.state_reason,
            issue.created_at,
            issue.updated_at,
            issue.closed_at,
            issue.author,
            issue.author_association,
            json.dumps(issue.labels),
            issue.comment_count,
            issue.reaction_count,
            issue.thumbs_up,
            json.dumps(issue.raw),
            fetched_at,
        ),
    )
    connection.execute("DELETE FROM comments WHERE issue_id = ?", (issue.id,))
    connection.executemany(
        """
        INSERT INTO comments (id, issue_id, author, author_association,
                              created_at, body)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                comment.id,
                issue.id,
                comment.author,
                comment.author_association,
                comment.created_at,
                comment.body,
            )
            for comment in issue.recent_comments
        ],
    )


def summarize(path: pathlib.Path) -> RepoSummary:
    """Reads what ``triage list`` shows from one cache file.

    Args:
        path: An existing cache file.

    Returns:
        The repository's summary.

    Raises:
        StoreError: The file is from another schema version or has no
            repository recorded.
    """
    connection = connect(path)
    try:
        repo = connection.execute(
            "SELECT name_with_owner, last_synced_at FROM repository"
        ).fetchone()
        if repo is None:
            raise StoreError(f"{path} has no repository recorded.")
        open_issues = connection.execute(
            "SELECT COUNT(*) FROM issues WHERE state = 'OPEN'"
        ).fetchone()[0]
        last_run = connection.execute(
            "SELECT total_open FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    return RepoSummary(
        name_with_owner=repo["name_with_owner"],
        open_issues=open_issues,
        total_open=last_run["total_open"] if last_run else open_issues,
        last_synced_at=repo["last_synced_at"],
        path=path,
    )
