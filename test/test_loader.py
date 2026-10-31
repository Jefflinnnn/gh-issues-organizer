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


def test_loads_linked_pull_requests_and_release_times(
    tmp_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    first, *others = fetch_result.issues
    pull = github_api.LinkedPullRequest(
        number=7,
        state="MERGED",
        is_draft=False,
        merged_at="2026-10-24T03:01:07Z",
    )
    fetch = dataclasses.replace(
        fetch_result,
        repository=dataclasses.replace(
            fetch_result.repository,
            releases=(
                github_api.Release("v2", "2026-10-29T20:00:45Z"),
                github_api.Release("v1", "2026-10-01T00:00:00Z"),
            ),
        ),
        issues=[
            dataclasses.replace(first, linked_pull_requests=[pull]),
            *others,
        ],
    )
    connection = store.connect(tmp_path / "repo.db")
    try:
        store.save_fetch(connection, fetch, "add", store.utc_now())
        issues = loader.load_open_issues(connection)
        releases = loader.load_release_times(connection)
    finally:
        connection.close()

    assert issues[0].author == "leontievilya"
    assert issues[0].linked_pull_requests == [
        loader.StoredPullRequest(
            number=7,
            state="MERGED",
            is_draft=False,
            merged_at=datetime.datetime(
                2026, 10, 24, 3, 1, 7, tzinfo=datetime.timezone.utc
            ),
        )
    ]
    assert [release.month for release in releases] == [10, 10]
    assert releases[0] < releases[1]
