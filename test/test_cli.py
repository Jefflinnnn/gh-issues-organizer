"""Tests for the CLI with GitHub replaced by the recorded response."""

import dataclasses
import pathlib

import pytest

from gh_triage import cli
from gh_triage import github_api
from gh_triage import repo_ref

DB_NAME = "vorssaint__vorssaint-utils.db"


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


def _run(data_dir: pathlib.Path, *args: str) -> int:
    return cli.main(["--data-dir", str(data_dir), *args])


@pytest.mark.usefixtures("fake_github")
def test_add_creates_one_cache_file(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _run(
        tmp_path, "add", "https://github.com/vorssaint/vorssaint-utils"
    )

    assert exit_code == 0
    assert [p.name for p in tmp_path.iterdir()] == [DB_NAME]
    assert "stored 3 of 887 open issues" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_github")
def test_add_twice_is_refused(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    _run(tmp_path, "add", "vorssaint/vorssaint-utils")

    exit_code = _run(tmp_path, "add", "Vorssaint/Vorssaint-Utils")

    assert exit_code == 1
    assert "already tracked" in caplog.text


def test_add_stores_under_the_canonical_name(
    tmp_path: pathlib.Path, fake_github: list[repo_ref.RepoRef]
) -> None:
    exit_code = _run(tmp_path, "add", "old-owner/old-name")

    assert exit_code == 0
    assert fake_github == [repo_ref.RepoRef("old-owner", "old-name")]
    assert [p.name for p in tmp_path.iterdir()] == [DB_NAME]


def test_failed_fetch_leaves_no_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise github_api.GitHubError("Repository nope/nope not found")

    monkeypatch.setattr(github_api, "load_token", lambda: "test-token")
    monkeypatch.setattr(github_api, "fetch_open_issues", fail)

    exit_code = _run(tmp_path, "add", "nope/nope")

    assert exit_code == 1
    assert "not found" in caplog.text
    assert list(tmp_path.iterdir()) == []


def test_failed_write_leaves_no_file(
    tmp_path: pathlib.Path,
    fake_github: list[repo_ref.RepoRef],
    fetch_result: github_api.FetchResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken_issue = dataclasses.replace(
        fetch_result.issues[0], raw={"unserializable": object()}
    )
    broken = dataclasses.replace(fetch_result, issues=[broken_issue])
    monkeypatch.setattr(
        github_api, "fetch_open_issues", lambda *args, **kwargs: broken
    )

    with pytest.raises(TypeError):
        _run(tmp_path, "add", "vorssaint/vorssaint-utils")

    assert list(tmp_path.iterdir()) == []


def test_add_rejects_a_bad_link(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert _run(tmp_path, "add", "https://gitlab.com/a/b") == 1
    assert "Not a GitHub repository" in caplog.text


def test_list_with_nothing_tracked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "missing", "list") == 0
    assert "No tracked repositories" in capsys.readouterr().out


@pytest.mark.usefixtures("fake_github")
def test_list_shows_tracked_repositories(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, "add", "vorssaint/vorssaint-utils")
    capsys.readouterr()

    assert _run(tmp_path, "list") == 0

    header, row = capsys.readouterr().out.splitlines()
    assert header.split()[0] == "REPO"
    assert row.split()[:3] == ["vorssaint/vorssaint-utils", "3", "887"]
