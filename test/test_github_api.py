import pathlib
from typing import Any

import pytest

from gh_triage import github_api
from gh_triage import repo_ref

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
    assert result.issues[0].recent_comments[0].author is None


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
