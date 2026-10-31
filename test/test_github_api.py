import copy
import pathlib
from typing import Any

import pytest

from issue_store import github_api
from issue_store import repo_ref
from issue_store import stale_policy

REPO = repo_ref.RepoRef(owner="vorssaint", name="vorssaint-utils")


def test_parse_reads_repository_and_counts(
    fetch_result: github_api.FetchResult,
) -> None:
    assert fetch_result.repository.id == "R_kgDOS4xLOw"
    assert (
        fetch_result.repository.name_with_owner == "vorssaint/vorssaint-utils"
    )
    assert fetch_result.total_open == 887
    assert fetch_result.rate_limit_cost == 1
    assert [issue.number for issue in fetch_result.issues] == [3267, 3668, 3678]


def test_parse_reads_issue_fields(fetch_result: github_api.FetchResult) -> None:
    issue = fetch_result.issues[0]
    assert issue.state == "OPEN"
    assert issue.closed_at is None
    assert issue.author == "leontievilya"
    assert issue.author_type == "User"
    assert issue.labels == ["enhancement"]
    assert issue.comment_count == 2
    assert [c.author for c in issue.recent_comments] == ["shu-vro", "Psylar87"]
    assert issue.raw["number"] == 3267


def test_parse_handles_deleted_authors(issues_payload: dict[str, Any]) -> None:
    node = issues_payload["data"]["repository"]["issues"]["nodes"][0]
    node["author"] = None
    node["comments"]["nodes"][0]["author"] = None

    result = github_api.parse_issues_response(issues_payload, REPO)

    assert result.issues[0].author is None
    assert result.issues[0].author_type is None
    assert result.issues[0].recent_comments[0].author is None
    assert result.issues[0].recent_comments[0].author_type is None


def test_parse_reads_bot_authors(issues_payload: dict[str, Any]) -> None:
    node = issues_payload["data"]["repository"]["issues"]["nodes"][0]
    node["comments"]["nodes"][0]["author"] = {
        "login": "github-actions",
        "__typename": "Bot",
    }

    result = github_api.parse_issues_response(issues_payload, REPO)

    comment = result.issues[0].recent_comments[0]
    assert (comment.author, comment.author_type) == ("github-actions", "Bot")


def test_parse_finds_the_repositorys_stale_window(
    issues_payload: dict[str, Any],
) -> None:
    repository = issues_payload["data"]["repository"]
    repository["probotStale"] = None
    repository["workflows"] = {
        "entries": [
            {"name": "README.md", "object": {"text": "days"}},
            {"name": "assets", "object": {}},
            {
                "name": "stale.yml",
                "object": {
                    "text": "jobs:\n  s:\n    steps:\n"
                    "      - uses: actions/stale@v9\n"
                    "        with:\n          days-before-issue-stale: 30\n"
                },
            },
        ]
    }

    result = github_api.parse_issues_response(issues_payload, REPO)

    assert result.repository.stale_window == stale_policy.StalePolicy(
        30, ".github/workflows/stale.yml"
    )


def test_parse_without_stale_config_has_no_window(
    fetch_result: github_api.FetchResult,
) -> None:
    assert fetch_result.repository.stale_window is None


def _pull_node(
    number: int, repo: str = "vorssaint/vorssaint-utils"
) -> dict[str, Any]:
    return {
        "number": number,
        "state": "MERGED",
        "isDraft": False,
        "mergedAt": "2026-10-24T03:01:07Z",
        "baseRepository": {"nameWithOwner": repo},
    }


def test_parse_reads_same_repository_linked_pull_requests(
    issues_payload: dict[str, Any],
) -> None:
    node = issues_payload["data"]["repository"]["issues"]["nodes"][0]
    node["linkedPullRequests"] = {
        "nodes": [
            {"source": _pull_node(3457)},
            {"source": _pull_node(3457)},
            {"subject": _pull_node(12)},
            {"source": _pull_node(9, repo="someone/fork")},
            # A cross-reference from another issue, not a pull request.
            {"source": {}},
        ]
    }

    result = github_api.parse_issues_response(issues_payload, REPO)

    pulls = result.issues[0].linked_pull_requests
    assert [pull.number for pull in pulls] == [12, 3457]
    assert pulls[1] == github_api.LinkedPullRequest(
        number=3457,
        state="MERGED",
        is_draft=False,
        merged_at="2026-10-24T03:01:07Z",
    )


def test_parse_keeps_only_published_stable_releases(
    issues_payload: dict[str, Any],
) -> None:
    issues_payload["data"]["repository"]["releases"] = {
        "nodes": [
            {
                "tagName": "v2-rc1",
                "publishedAt": "2026-10-30T00:00:00Z",
                "isDraft": False,
                "isPrerelease": True,
            },
            {
                "tagName": "v2",
                "publishedAt": None,
                "isDraft": True,
                "isPrerelease": False,
            },
            {
                "tagName": "v1",
                "publishedAt": "2026-10-29T00:00:00Z",
                "isDraft": False,
                "isPrerelease": False,
            },
        ]
    }

    result = github_api.parse_issues_response(issues_payload, REPO)

    assert result.repository.releases == (
        github_api.Release(tag_name="v1", published_at="2026-10-29T00:00:00Z"),
    )


def test_fetch_linked_pull_requests_pages_and_skips_deleted_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages: list[list[str]] = []

    def post(
        query: str, variables: dict[str, Any], token: str
    ) -> dict[str, Any]:
        ids = variables["ids"]
        pages.append(ids)
        nodes: list[dict[str, Any] | None] = [
            None
            if issue_id == "gone"
            else {
                "id": issue_id,
                "linkedPullRequests": {"nodes": [{"source": _pull_node(1)}]},
            }
            for issue_id in ids
        ]
        return {
            "data": {"rateLimit": {"cost": 1, "remaining": 9}, "nodes": nodes},
            "errors": [{"type": "NOT_FOUND"}] if "gone" in ids else [],
        }

    monkeypatch.setattr(github_api, "_post_graphql", post)
    issue_ids = [f"I_{n}" for n in range(150)] + ["gone"]

    links, cost = github_api.fetch_linked_pull_requests(
        "vorssaint/vorssaint-utils", issue_ids, "token"
    )

    assert [len(page) for page in pages] == [100, 51]
    assert cost == 2
    assert len(links) == 150
    assert "gone" not in links
    assert [pull.number for pull in links["I_0"]] == [1]


def test_parse_reports_missing_repository() -> None:
    payload = {
        "data": {"repository": None},
        "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}],
    }
    with pytest.raises(github_api.GitHubError, match="not found"):
        github_api.parse_issues_response(payload, REPO)


def test_parse_reports_other_graphql_errors() -> None:
    payload = {"errors": [{"type": "MAX_NODE_LIMIT", "message": "too big"}]}
    with pytest.raises(github_api.GitHubError, match="too big"):
        github_api.parse_issues_response(payload, REPO)


@pytest.mark.parametrize("limit", [0, 101])
def test_fetch_rejects_out_of_range_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        github_api.fetch_open_issues(
            REPO, limit=limit, comments=3, token="unused"
        )


def test_load_token_prefers_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert github_api.load_token(env_file) == "from-env"


@pytest.mark.parametrize(
    "line",
    [
        "GITHUB_TOKEN=from-file",
        'GITHUB_TOKEN="from-file"',
        "export GITHUB_TOKEN='from-file'",
    ],
)
def test_load_token_reads_env_file(
    line: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"# comment\nOTHER=x\n{line}\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert github_api.load_token(env_file) == "from-file"


def test_load_token_explains_missing_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(github_api.GitHubError, match="triage setup"):
        github_api.load_token(tmp_path / ".env")


def test_parse_viewer_response() -> None:
    payload = {
        "data": {
            "viewer": {"login": "octocat"},
            "rateLimit": {"limit": 5000, "remaining": 4990},
        }
    }
    assert github_api.parse_viewer_response(payload) == github_api.TokenStatus(
        login="octocat", limit=5000, remaining=4990
    )


def test_parse_viewer_response_reports_errors() -> None:
    payload = {"errors": [{"message": "Bad credentials"}]}
    with pytest.raises(github_api.GitHubError, match="Bad credentials"):
        github_api.parse_viewer_response(payload)


def _changed_page(
    payload: dict[str, Any],
    nodes: list[dict[str, Any]],
    *,
    cursor: str | None,
    cost: int = 1,
) -> dict[str, Any]:
    """Reshapes the recorded response into a CHANGED_ISSUES_QUERY page."""
    page = copy.deepcopy(payload)
    page["data"]["rateLimit"]["cost"] = cost
    page["data"]["rateLimit"]["remaining"] = 5000 - cost
    repository = page["data"]["repository"]
    repository["openIssues"] = {"totalCount": 887}
    repository["issues"] = {
        "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
        "nodes": nodes,
    }
    return page


def test_parse_changed_response_reads_paging(
    issues_payload: dict[str, Any],
) -> None:
    nodes = issues_payload["data"]["repository"]["issues"]["nodes"]
    payload = _changed_page(issues_payload, nodes, cursor="abc")

    page = github_api.parse_changed_response(payload, REPO)

    assert (page.has_next_page, page.end_cursor) == (True, "abc")
    assert len(page.fetch.issues) == 3
    assert page.fetch.total_open == 887


def test_fetch_changed_issues_follows_every_page(
    issues_payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    nodes = issues_payload["data"]["repository"]["issues"]["nodes"]
    pages = [
        _changed_page(issues_payload, nodes[:2], cursor="c1", cost=1),
        _changed_page(issues_payload, nodes[2:], cursor=None, cost=2),
    ]
    sent: list[dict[str, Any]] = []

    def post(
        query: str, variables: dict[str, Any], token: str
    ) -> dict[str, Any]:
        sent.append(variables)
        return pages[len(sent) - 1]

    monkeypatch.setattr(github_api, "_post_graphql", post)

    result = github_api.fetch_changed_issues(
        REPO, "2026-10-01T00:00:00Z", comments=3, token="t"
    )

    assert [variables["after"] for variables in sent] == [None, "c1"]
    assert {variables["since"] for variables in sent} == {
        "2026-10-01T00:00:00Z"
    }
    assert [issue.number for issue in result.issues] == [3267, 3668, 3678]
    assert result.rate_limit_cost == 3


def test_fetch_changed_issues_keeps_the_newest_copy_of_a_repeat(
    issues_payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    node = issues_payload["data"]["repository"]["issues"]["nodes"][0]
    edited = {**node, "title": "Edited while paging"}
    pages = iter(
        [
            _changed_page(issues_payload, [node], cursor="c1"),
            _changed_page(issues_payload, [edited], cursor=None),
        ]
    )
    monkeypatch.setattr(github_api, "_post_graphql", lambda *args: next(pages))

    result = github_api.fetch_changed_issues(
        REPO, "2026-10-01T00:00:00Z", comments=3, token="t"
    )

    assert [issue.title for issue in result.issues] == ["Edited while paging"]


def test_fetch_changed_issues_rejects_out_of_range_comments() -> None:
    with pytest.raises(ValueError, match="comments"):
        github_api.fetch_changed_issues(REPO, "2026-10-01T00:00:00Z", 101, "t")
