"""Reads the open issues the triager works on from a cache file."""

import dataclasses
import datetime
import json
import sqlite3


@dataclasses.dataclass(frozen=True)
class StoredComment:
    """The parts of a stored comment the triager uses.

    Attributes:
        author: Login of the author, or None for a deleted account.
        author_type: The author's account type, e.g. User or Bot. None for
            a deleted account, or when the type wasn't recorded (rows
            stored before schema version 2).
        created_at: When the comment was posted, in UTC.
    """

    author: str | None
    author_type: str | None
    created_at: datetime.datetime


@dataclasses.dataclass(frozen=True)
class StoredPullRequest:
    """A stored pull request that links to an issue.

    Attributes:
        number: The pull request's number.
        state: OPEN, CLOSED or MERGED.
        is_draft: Whether it is still a draft.
        merged_at: When it was merged, in UTC, or None.
    """

    number: int
    state: str
    is_draft: bool
    merged_at: datetime.datetime | None


@dataclasses.dataclass(frozen=True)
class OpenIssue:
    """One open issue as the triager sees it.

    Attributes:
        number: The issue number within its repository.
        title: Issue title.
        url: Link to the issue on github.com.
        author: Login of the reporter, or None for a deleted account.
        author_type: The author's account type, or None when unknown, as
            for ``StoredComment``.
        created_at: When the issue was opened, in UTC.
        labels: Names of the repo labels on the issue.
        comment_count: Total comments on GitHub, not just those stored.
        reaction_count: Total reactions of every kind.
        recent_comments: The stored comments, oldest first. The cache keeps
            only the latest few per issue.
        linked_pull_requests: Pull requests in the same repository that
            link to the issue, by number.
    """

    number: int
    title: str
    url: str
    author: str | None
    author_type: str | None
    created_at: datetime.datetime
    labels: list[str]
    comment_count: int
    reaction_count: int
    recent_comments: list[StoredComment]
    linked_pull_requests: list[StoredPullRequest]


def load_open_issues(connection: sqlite3.Connection) -> list[OpenIssue]:
    """Reads every open issue and its stored comments.

    Args:
        connection: An open cache file from ``store.connect``.

    Returns:
        The open issues, lowest number first.
    """
    comments: dict[str, list[StoredComment]] = {}
    for row in connection.execute(
        """
        SELECT comments.issue_id, comments.author, comments.author_type,
               comments.created_at
        FROM comments JOIN issues ON issues.id = comments.issue_id
        WHERE issues.state = 'OPEN'
        ORDER BY comments.created_at
        """
    ):
        comments.setdefault(row["issue_id"], []).append(
            StoredComment(
                author=row["author"],
                author_type=row["author_type"],
                created_at=parse_time(row["created_at"]),
            )
        )
    pulls: dict[str, list[StoredPullRequest]] = {}
    for row in connection.execute(
        "SELECT issue_id, number, state, is_draft, merged_at "
        "FROM linked_pull_requests ORDER BY number"
    ):
        pulls.setdefault(row["issue_id"], []).append(
            StoredPullRequest(
                number=row["number"],
                state=row["state"],
                is_draft=bool(row["is_draft"]),
                merged_at=(
                    parse_time(row["merged_at"]) if row["merged_at"] else None
                ),
            )
        )
    return [
        OpenIssue(
            number=row["number"],
            title=row["title"],
            url=row["url"],
            author=row["author"],
            author_type=row["author_type"],
            created_at=parse_time(row["created_at"]),
            labels=json.loads(row["labels_json"]),
            comment_count=row["comment_count"],
            reaction_count=row["reaction_count"],
            recent_comments=comments.get(row["id"], []),
            linked_pull_requests=pulls.get(row["id"], []),
        )
        for row in connection.execute(
            """
            SELECT id, number, title, url, author, author_type, created_at,
                   labels_json, comment_count, reaction_count
            FROM issues WHERE state = 'OPEN' ORDER BY number
            """
        )
    ]


def load_release_times(
    connection: sqlite3.Connection,
) -> list[datetime.datetime]:
    """Reads when each stored stable release was published.

    Args:
        connection: An open cache file from ``store.connect``.

    Returns:
        The publish times, oldest first.
    """
    return [
        parse_time(row["published_at"])
        for row in connection.execute(
            "SELECT published_at FROM releases ORDER BY published_at"
        )
    ]


def parse_time(iso: str) -> datetime.datetime:
    """Parses one of GitHub's ISO 8601 UTC timestamps.

    Args:
        iso: A timestamp such as ``2026-10-20T00:44:43Z``.

    Returns:
        The moment as a timezone-aware datetime.
    """
    # Python 3.10's fromisoformat doesn't accept the "Z" suffix.
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
