import dataclasses
import datetime
import pathlib

from issue_store import github_api
from issue_store import store
from triager import loader


def _saved(
    tmp_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> list[loader.OpenIssue]:
    connection = store.connect(tmp_path / "repo.db")
    try:
        store.save_fetch(connection, fetch_result, "add", store.utc_now())
        return loader.load_open_issues(connection)
    finally:
        connection.close()


def test_loads_open_issues_with_their_comments(
    tmp_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    issues = _saved(tmp_path, fetch_result)

    assert [issue.number for issue in issues] == [3267, 3668, 3678]
    first = issues[0]
    assert first.author_type == "User"
    assert first.labels == ["enhancement"]
    assert (first.comment_count, first.reaction_count) == (2, 3)
    assert first.created_at == datetime.datetime(
        2026, 10, 20, 0, 44, 43, tzinfo=datetime.timezone.utc
    )
    assert [comment.created_at.day for comment in first.recent_comments] == [
        22,
        30,
    ]


def test_skips_closed_issues(
    tmp_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    first, *others = fetch_result.issues
    closed = dataclasses.replace(first, state="CLOSED")

    issues = _saved(
        tmp_path, dataclasses.replace(fetch_result, issues=[closed, *others])
    )

    assert [issue.number for issue in issues] == [3668, 3678]


def test_parse_time_reads_github_timestamps() -> None:
    assert loader.parse_time("2026-10-20T00:44:43Z").tzinfo is not None
