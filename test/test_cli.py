"""Tests for add, list and remove with GitHub replaced by a recording."""

import dataclasses
import pathlib
from collections.abc import Callable

import pytest

from gh_triage import cli
from gh_triage import github_api
from gh_triage import repo_ref

Triage = Callable[..., int]
AnswerPrompt = Callable[..., None]


# --- add ----------------------------------------------------------------


@pytest.mark.usefixtures("fake_github")
def test_add_creates_one_cache_file(
    triage: Triage,
    tmp_path: pathlib.Path,
    cache_file: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert triage("add", "https://github.com/vorssaint/vorssaint-utils") == 0

    assert list(tmp_path.iterdir()) == [cache_file]
    assert "stored 3 of 887 open issues" in capsys.readouterr().out


@pytest.mark.usefixtures("tracked")
def test_add_twice_is_refused(
    triage: Triage, caplog: pytest.LogCaptureFixture
) -> None:
    assert triage("add", "Vorssaint/Vorssaint-Utils") == 1
    assert "already tracked" in caplog.text


def test_add_stores_under_the_canonical_name(
    triage: Triage,
    tmp_path: pathlib.Path,
    cache_file: pathlib.Path,
    fake_github: list[repo_ref.RepoRef],
) -> None:
    assert triage("add", "old-owner/old-name") == 0

    assert fake_github == [repo_ref.RepoRef("old-owner", "old-name")]
    assert list(tmp_path.iterdir()) == [cache_file]


def test_add_rejects_a_bad_link(
    triage: Triage, caplog: pytest.LogCaptureFixture
) -> None:
    assert triage("add", "https://gitlab.com/a/b") == 1
    assert "Not a GitHub repository" in caplog.text


def test_failed_fetch_leaves_no_file(
    triage: Triage,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise github_api.GitHubError("Repository nope/nope not found")

    monkeypatch.setattr(github_api, "load_token", lambda: "test-token")
    monkeypatch.setattr(github_api, "fetch_open_issues", fail)

    assert triage("add", "nope/nope") == 1

    assert "not found" in caplog.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("fake_github")
def test_failed_write_leaves_no_file(
    triage: Triage,
    tmp_path: pathlib.Path,
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
        triage("add", "vorssaint/vorssaint-utils")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--data-dir", "{dir}", "-v", "add", "vorssaint/vorssaint-utils"],
        ["--data-dir", "{dir}", "add", "-v", "vorssaint/vorssaint-utils"],
        ["add", "vorssaint/vorssaint-utils", "-v", "--data-dir", "{dir}"],
    ],
)
@pytest.mark.usefixtures("fake_github")
def test_global_options_work_before_or_after_the_command(
    argv: list[str], tmp_path: pathlib.Path, cache_file: pathlib.Path
) -> None:
    filled = [arg.replace("{dir}", str(tmp_path)) for arg in argv]

    assert cli.main(filled) == 0
    assert list(tmp_path.iterdir()) == [cache_file]


# --- list ---------------------------------------------------------------


def test_list_with_nothing_tracked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--data-dir", str(tmp_path / "missing"), "list"]) == 0
    assert "No tracked repositories" in capsys.readouterr().out


@pytest.mark.usefixtures("tracked")
def test_list_shows_tracked_repositories(
    triage: Triage, capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("list") == 0

    header, row = capsys.readouterr().out.splitlines()
    assert header.split()[0] == "REPO"
    assert "LAST SYNCED" in header and "UTC" not in header
    assert row.split()[:3] == ["vorssaint/vorssaint-utils", "3", "887"]
    # Local time, not the stored ISO form.
    assert "T" not in row.split()[3] and "+00:00" not in row


# --- remove -------------------------------------------------------------


def test_remove_with_yes_deletes_the_file(
    triage: Triage,
    tracked: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert triage("remove", "vorssaint/vorssaint-utils", "--yes") == 0

    assert not tracked.exists()
    assert "Removed vorssaint/vorssaint-utils" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["y\n", "YES\n"])
def test_remove_deletes_after_yes(
    triage: Triage,
    tracked: pathlib.Path,
    answer_prompt: AnswerPrompt,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    answer_prompt(answer)

    exit_code = triage("remove", "https://github.com/Vorssaint/Vorssaint-Utils")

    assert exit_code == 0
    assert not tracked.exists()
    assert "3 stored issues? You'll need 'triage add'" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize("answer", ["\n", "n\n", "nope\n", ""])
def test_remove_keeps_the_file_unless_confirmed(
    triage: Triage,
    tracked: pathlib.Path,
    answer_prompt: AnswerPrompt,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    answer_prompt(answer)

    assert triage("remove", "vorssaint/vorssaint-utils") == 0

    assert tracked.exists()
    assert "Nothing removed." in capsys.readouterr().out


def test_remove_without_a_terminal_needs_yes(
    triage: Triage,
    tracked: pathlib.Path,
    answer_prompt: AnswerPrompt,
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer_prompt("y\n", terminal=False)

    assert triage("remove", "vorssaint/vorssaint-utils") == 1

    assert tracked.exists()
    assert "--yes" in caplog.text


def test_remove_an_untracked_repo_fails(
    triage: Triage, caplog: pytest.LogCaptureFixture
) -> None:
    assert triage("remove", "nobody/nothing", "--yes") == 1
    assert "not tracked" in caplog.text


def test_remove_deletes_an_unreadable_file(
    triage: Triage,
    cache_file: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_file.write_bytes(b"not a database")

    assert triage("remove", "vorssaint/vorssaint-utils", "--yes") == 0

    assert not cache_file.exists()
    assert "Removed vorssaint/vorssaint-utils" in capsys.readouterr().out
