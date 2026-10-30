"""Shared fixtures: a real GraphQL response recorded from GitHub.

``fixtures/issues_response.json`` is the body GitHub returned for
``ISSUES_QUERY`` on vorssaint/vorssaint-utils with limit 3 and comments 3,
recorded on 2026-10-29. Tests use it so they never need the network or a
token.
"""

import copy
import json
import pathlib
from typing import Any

import pytest

from gh_triage import github_api
from gh_triage import repo_ref

FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "issues_response.json"
)
FIXTURE_REPO = repo_ref.RepoRef(owner="vorssaint", name="vorssaint-utils")


@pytest.fixture
def issues_payload() -> dict[str, Any]:
    """Returns a fresh copy of the recorded response body."""
    payload: dict[str, Any] = json.loads(
        FIXTURE_PATH.read_text(encoding="utf-8")
    )
    return copy.deepcopy(payload)


@pytest.fixture
def fetch_result(issues_payload: dict[str, Any]) -> github_api.FetchResult:
    """Returns the recorded response parsed into a FetchResult."""
    return github_api.parse_issues_response(issues_payload, FIXTURE_REPO)


@pytest.fixture
def fake_github(
    monkeypatch: pytest.MonkeyPatch, fetch_result: github_api.FetchResult
) -> list[repo_ref.RepoRef]:
    """Serves the recorded fetch and records which repos were requested."""
    requested: list[repo_ref.RepoRef] = []

    def fetch(
        repo: repo_ref.RepoRef, limit: int, comments: int, token: str
    ) -> github_api.FetchResult:
        requested.append(repo)
        return fetch_result

    monkeypatch.setattr(github_api, "load_token", lambda: "test-token")
    monkeypatch.setattr(github_api, "fetch_open_issues", fetch)
    return requested
