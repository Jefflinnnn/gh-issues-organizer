"""Shared fixtures: a recorded GitHub response, a fake GitHub, CLI helpers.

``fixtures/issues_response.json`` is the body GitHub returned for
``ISSUES_QUERY`` on vorssaint/vorssaint-utils with limit 3 and comments 3,
recorded on 2026-10-29. The ``__typename`` of each author was added by
hand on 2026-10-30, after the query started asking for it; GitHub reports
all six authors as ``User``. Tests use it so they never need the network
or a token.
"""

import copy
import dataclasses
import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from issue_store import github_api
from issue_store import repo_ref
from triage_cli import cli

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


@dataclasses.dataclass
class FakeSync:
    """What the fake GitHub serves to ``sync`` and what it was asked for.

    Attributes:
        issues: The changed issues the next sync receives.
        requests: One ``(owner/name, since)`` pair per sync request.
        links: The linked pull requests the link refresh returns.
        link_requests: The issue IDs each link refresh asked about.
    """

    issues: list[github_api.Issue] = dataclasses.field(default_factory=list)
    requests: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    links: dict[str, list[github_api.LinkedPullRequest]] = dataclasses.field(
        default_factory=dict
    )
    link_requests: list[list[str]] = dataclasses.field(default_factory=list)


@pytest.fixture
def fake_sync(
    monkeypatch: pytest.MonkeyPatch, fetch_result: github_api.FetchResult
) -> FakeSync:
    """Serves ``sync`` the issues set on the returned object."""
    served = FakeSync()

    def fetch(
        repo: repo_ref.RepoRef, since: str, comments: int, token: str
    ) -> github_api.FetchResult:
        served.requests.append((repo.full_name, since))
        repository = fetch_result.repository
        if repo.full_name != repository.name_with_owner:
            repository = github_api.Repository(
                id=f"R_{repo.name}",
                name_with_owner=repo.full_name,
                url=f"https://github.com/{repo.full_name}",
            )
        return dataclasses.replace(
            fetch_result,
            repository=repository,
            issues=served.issues,
            total_open=887,
        )

    def fetch_links(
        name_with_owner: str, issue_ids: list[str], token: str
    ) -> tuple[dict[str, list[github_api.LinkedPullRequest]], int]:
        served.link_requests.append(sorted(issue_ids))
        return served.links, 1

    monkeypatch.setattr(github_api, "load_token", lambda: "test-token")
    monkeypatch.setattr(github_api, "fetch_changed_issues", fetch)
    monkeypatch.setattr(github_api, "fetch_linked_pull_requests", fetch_links)
    return served


@pytest.fixture
def cache_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Returns where ``add`` stores the fixture repo under ``tmp_path``."""
    return tmp_path / "vorssaint__vorssaint-utils.db"


@pytest.fixture
def triage(tmp_path: pathlib.Path) -> Callable[..., int]:
    """Returns a function that runs the CLI with its cache in tmp_path."""

    def run(*args: str) -> int:
        return cli.main(["--data-dir", str(tmp_path), *args])

    return run


@pytest.fixture
def tracked(
    triage: Callable[..., int],
    cache_file: pathlib.Path,
    fake_github: list[repo_ref.RepoRef],
    capsys: pytest.CaptureFixture[str],
) -> pathlib.Path:
    """Tracks the fixture repo, clears its output, returns its cache file."""
    assert triage("add", "vorssaint/vorssaint-utils") == 0
    capsys.readouterr()
    return cache_file


class _FakeStdin:
    """Stands in for sys.stdin: a chosen answer, on or off a terminal."""

    def __init__(self, answer: str, is_terminal: bool) -> None:
        self._answer = answer
        self._is_terminal = is_terminal

    def isatty(self) -> bool:
        return self._is_terminal

    def readline(self) -> str:
        return self._answer


@pytest.fixture
def answer_prompt(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Returns a function that fakes what the user types at a [y/N] prompt.

    ``answer_prompt("y\\n")`` answers yes; ``answer_prompt(terminal=False)``
    simulates running from a script, where nobody can answer.
    """

    def answer(text: str = "", *, terminal: bool = True) -> None:
        monkeypatch.setattr("sys.stdin", _FakeStdin(text, terminal))

    return answer
