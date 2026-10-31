"""Stores one repository's issues in a local SQLite file."""

import collections
import dataclasses
import datetime
import json
import logging
import pathlib
import sqlite3

from issue_store import github_api

logger = logging.getLogger(__name__)

# Bump when the tables change, and add the step that upgrades the previous
# version to _MIGRATIONS. A file from a newer version is rejected rather than
# misread.
SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repository (
    -- Exactly one row: the repository this file belongs to.
    id               TEXT PRIMARY KEY,
    name_with_owner  TEXT NOT NULL,
    url              TEXT NOT NULL,
    added_at         TEXT NOT NULL,
    last_synced_at   TEXT NOT NULL,
    -- The repo-wide stale window its own files define, refreshed on each
    -- fetch; NULL when it defines none.
    stale_days       INTEGER,
    stale_source     TEXT
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
    author_type         TEXT,
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
    author_type         TEXT,
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


# _MIGRATIONS[n] upgrades a file from version n + 1 to n + 2. Rows stored
# before a step keep NULL in the columns it adds.
_MIGRATIONS = (
    # 2: who wrote each issue and comment, a person or a bot.
    """
    ALTER TABLE issues ADD COLUMN author_type TEXT;
    ALTER TABLE comments ADD COLUMN author_type TEXT;
    """,
    # 3: the repository's own stale window.
    """
    ALTER TABLE repository ADD COLUMN stale_days INTEGER;
    ALTER TABLE repository ADD COLUMN stale_source TEXT;
    """,
)


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


@dataclasses.dataclass(frozen=True)
class Changes:
    """How a saved fetch differs from what was already stored.

    An issue GitHub sent again with the same ``updated_at`` is unchanged and
    counted nowhere.

    Attributes:
        created: Issues not stored before.
        closed: Stored open issues that are now closed.
        reopened: Stored closed issues that are open again.
        updated: Other stored issues that changed, e.g. new comments.
    """

    created: int = 0
    closed: int = 0
    reopened: int = 0
    updated: int = 0


def utc_now() -> str:
    """Returns the current UTC time as an ISO 8601 string."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Opens a cache file, creating or upgrading its tables if needed.

    A file from an older version of the tool is upgraded in place, keeping
    its stored issues.

    Args:
        path: The SQLite file. Its folder is created if missing.

    Returns:
        An open connection with foreign keys enforced.

    Raises:
        StoreError: The file was made by a newer version of the tool, or
            upgrading it failed.
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
    elif version > SCHEMA_VERSION:
        connection.close()
        raise StoreError(
            f"{path} was written by a newer version of triage (schema "
            f"version {version}; this one reads up to {SCHEMA_VERSION}). "
            "Update triage: run git pull in the project folder."
        )
    elif version < SCHEMA_VERSION:
        _upgrade(connection, path, version)
    return connection


def _upgrade(
    connection: sqlite3.Connection, path: pathlib.Path, version: int
) -> None:
    """Runs each upgrade step after ``version``, one transaction per step."""
    for target in range(version + 1, SCHEMA_VERSION + 1):
        try:
            connection.executescript(
                "BEGIN;\n"
                + _MIGRATIONS[target - 2]
                + f"PRAGMA user_version = {target};\nCOMMIT;"
            )
        except sqlite3.DatabaseError as error:
            connection.rollback()
            connection.close()
            raise StoreError(
                f"Couldn't upgrade {path} to schema version {target}: "
                f"{error}. Remove the repo and add it again."
            ) from error
        logger.info("Upgraded %s to schema version %d", path, target)


def last_activity(connection: sqlite3.Connection) -> str:
    """Returns the time after which ``sync`` should look for changes.

    This is the newest ``updated_at`` stored, a time GitHub reported, so a
    wrong clock on this computer can't make ``sync`` skip changes. A file
    with no issues falls back to when the repository was added.

    Args:
        connection: An open cache file from ``connect``.

    Returns:
        An ISO 8601 time.

    Raises:
        StoreError: The file has no repository recorded.
    """
    repo = connection.execute("SELECT added_at FROM repository").fetchone()
    if repo is None:
        raise StoreError("This cache file has no repository recorded.")
    newest = connection.execute("SELECT MAX(updated_at) FROM issues").fetchone()
    result: str = newest[0] or repo["added_at"]
    return result


def save_fetch(
    connection: sqlite3.Connection,
    fetch: github_api.FetchResult,
    kind: str,
    started_at: str,
) -> Changes:
    """Writes a fetch's repository, issues, comments and run in one step.

    Issues are upserted on their node ID, so saving the same issue twice
    updates it in place. An issue's stored comments are replaced by the
    ones in this fetch.

    Args:
        connection: An open cache file from ``connect``.
        fetch: The data to store.
        kind: What produced the fetch, such as ``"add"``.
        started_at: ISO 8601 time the run began.

    Returns:
        How the fetch differs from what was stored.

    Raises:
        StoreError: The file already belongs to a different repository.
    """
    finished_at = utc_now()
    counts = collections.Counter[str]()
    with connection:
        _save_repository(connection, fetch.repository, finished_at)
        for issue in fetch.issues:
            counts[_change_kind(connection, issue)] += 1
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
    return Changes(
        created=counts["created"],
        closed=counts["closed"],
        reopened=counts["reopened"],
        updated=counts["updated"],
    )


def _change_kind(
    connection: sqlite3.Connection, issue: github_api.Issue
) -> str:
    """Says how an issue differs from its stored copy, if it's stored."""
    stored = connection.execute(
        "SELECT state, updated_at FROM issues WHERE id = ?", (issue.id,)
    ).fetchone()
    if stored is None:
        return "created"
    if stored["updated_at"] == issue.updated_at:
        return "unchanged"
    if stored["state"] == "OPEN" and issue.state == "CLOSED":
        return "closed"
    if stored["state"] == "CLOSED" and issue.state == "OPEN":
        return "reopened"
    return "updated"


def _save_repository(
    connection: sqlite3.Connection,
    repository: github_api.Repository,
    synced_at: str,
) -> None:
    """Records the file's repository, refusing to mix two repositories."""
    window = repository.stale_window
    stale_days = window.days if window else None
    stale_source = window.source if window else None
    row = connection.execute(
        "SELECT id, name_with_owner FROM repository"
    ).fetchone()
    if row is None:
        connection.execute(
            """
            INSERT INTO repository (id, name_with_owner, url, added_at,
                                    last_synced_at, stale_days, stale_source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repository.id,
                repository.name_with_owner,
                repository.url,
                synced_at,
                synced_at,
                stale_days,
                stale_source,
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
        "last_synced_at = ?, stale_days = ?, stale_source = ?",
        (
            repository.name_with_owner,
            repository.url,
            synced_at,
            stale_days,
            stale_source,
        ),
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
                            author, author_type, author_association,
                            labels_json, comment_count, reaction_count,
                            thumbs_up, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            author_type = excluded.author_type,
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
            issue.author_type,
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
        INSERT INTO comments (id, issue_id, author, author_type,
                              author_association, created_at, body)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                comment.id,
                issue.id,
                comment.author,
                comment.author_type,
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
