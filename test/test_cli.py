"""Tests for add, list and remove with GitHub replaced by a recording."""

import dataclasses
import pathlib
import sqlite3
import sys
from collections.abc import Callable

import pytest
from conftest import FakeSync

from issue_store import github_api
from issue_store import repo_ref
from issue_store import store
from triage_cli import cli

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


# --- sync ---------------------------------------------------------------


def _other_repo_cache(
    tmp_path: pathlib.Path, fetch_result: github_api.FetchResult
) -> None:
    """Tracks a second repository by writing its cache file directly."""
    other = dataclasses.replace(
        fetch_result,
        repository=github_api.Repository(
            id="R_widgets",
            name_with_owner="acme/widgets",
            url="https://github.com/acme/widgets",
        ),
        issues=[],
    )
    connection = store.connect(tmp_path / "acme__widgets.db")
    store.save_fetch(connection, other, "add", store.utc_now())
    connection.close()


@pytest.mark.usefixtures("tracked")
def test_sync_stores_what_changed_since_the_newest_stored_issue(
    triage: Triage,
    cache_file: pathlib.Path,
    fetch_result: github_api.FetchResult,
    fake_sync: FakeSync,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = fetch_result.issues[0]
    closed = dataclasses.replace(
        first, state="CLOSED", updated_at="2026-10-31T00:00:00Z"
    )
    brand_new = dataclasses.replace(
        fetch_result.issues[1],
        id="new-id",
        number=9999,
        recent_comments=[],
    )
    fake_sync.issues = [closed, brand_new]

    assert triage("sync") == 0

    assert fake_sync.requests == [
        ("vorssaint/vorssaint-utils", "2026-10-30T04:43:23Z")
    ]
    assert capsys.readouterr().out == (
        "vorssaint/vorssaint-utils · 1 new · 1 closed · 887 open on GitHub\n"
    )
    with sqlite3.connect(cache_file) as connection:
        states = dict(connection.execute("SELECT number, state FROM issues"))
        kinds = [row[0] for row in connection.execute("SELECT kind FROM runs")]
    assert states[first.number] == "CLOSED"
    assert 9999 in states
    assert kinds == ["add", "sync"]


@pytest.mark.usefixtures("tracked")
def test_sync_with_nothing_new_says_so(
    triage: Triage,
    fake_sync: FakeSync,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert triage("sync") == 0

    assert fake_sync.issues == []
    assert "no changes · 887 open on GitHub" in capsys.readouterr().out


@pytest.mark.usefixtures("tracked")
def test_sync_one_repository_by_link(
    triage: Triage,
    tmp_path: pathlib.Path,
    fetch_result: github_api.FetchResult,
    fake_sync: FakeSync,
) -> None:
    _other_repo_cache(tmp_path, fetch_result)

    assert triage("sync", "https://github.com/vorssaint/vorssaint-utils") == 0

    assert [name for name, _ in fake_sync.requests] == [
        "vorssaint/vorssaint-utils"
    ]


@pytest.mark.usefixtures("tracked")
def test_sync_without_a_repo_syncs_every_tracked_one(
    triage: Triage,
    tmp_path: pathlib.Path,
    fetch_result: github_api.FetchResult,
    fake_sync: FakeSync,
) -> None:
    _other_repo_cache(tmp_path, fetch_result)

    assert triage("sync") == 0

    assert sorted(name for name, _ in fake_sync.requests) == [
        "acme/widgets",
        "vorssaint/vorssaint-utils",
    ]


@pytest.mark.usefixtures("fake_sync")
def test_sync_of_an_untracked_repo_is_refused(
    triage: Triage, caplog: pytest.LogCaptureFixture
) -> None:
    assert triage("sync", "vorssaint/vorssaint-utils") == 1
    assert "not tracked" in caplog.text


@pytest.mark.usefixtures("fake_sync")
def test_sync_with_nothing_tracked_points_to_add(
    triage: Triage, capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("sync") == 0
    assert "triage add" in capsys.readouterr().out


@pytest.mark.usefixtures("tracked")
def test_run_prints_each_open_issue_with_its_flags(
    triage: Triage, capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("run", "vorssaint/vorssaint-utils") == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("vorssaint/vorssaint-utils · 3 open issues")
    assert lines[0].endswith("· 1 hot")
    assert lines[1].split() == [
        "ISSUE",
        "FLAGS",
        "IDLE",
        "ENGAGEMENT",
        "TITLE",
    ]
    rows = {line.split()[0]: line for line in lines[2:]}
    assert set(rows) == {"#3267", "#3668", "#3678"}
    assert "hot" in rows["#3267"]
    assert "hot" not in rows["#3668"]


@pytest.mark.usefixtures("tracked")
def test_run_wraps_long_titles_on_a_terminal(
    triage: Triage,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "60")

    assert triage("run", "vorssaint/vorssaint-utils") == 0

    lines = capsys.readouterr().out.splitlines()
    assert all(len(line) <= 60 for line in lines[2:])
    title_column = lines[2].index("TITLE")
    first = next(i for i, line in enumerate(lines) if line.startswith("#3267"))
    continuation = lines[first + 1]
    assert continuation[:title_column].strip() == ""
    assert continuation[title_column] != " "


@pytest.mark.usefixtures("tracked")
def test_run_keeps_one_line_per_issue_when_piped(
    triage: Triage, capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("run", "vorssaint/vorssaint-utils") == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3 + 3
    assert lines[3].endswith("interface status (like Stats)")


@pytest.mark.usefixtures("tracked")
def test_run_limit_shows_the_first_issues(
    triage: Triage, capsys: pytest.CaptureFixture[str]
) -> None:
    assert triage("run", "vorssaint/vorssaint-utils", "--limit", "2") == 0

    lines = capsys.readouterr().out.splitlines()
    assert "· 3 open issues ·" in lines[0]
    assert [line.split()[0] for line in lines[3:5]] == ["#3267", "#3668"]
    assert lines[5] == "Showing 2 of 3 issues; drop --limit to see them all."


@pytest.mark.usefixtures("tracked")
def test_run_rejects_a_stale_window_below_one(
    triage: Triage, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        triage("run", "vorssaint/vorssaint-utils", "--stale-days", "0")

    assert exit_info.value.code == 2
    assert "must be 1 or more" in capsys.readouterr().err


def test_run_of_an_untracked_repo_points_to_add(
    triage: Triage, caplog: pytest.LogCaptureFixture
) -> None:
    assert triage("run", "vorssaint/vorssaint-utils") == 1
    assert "triage add vorssaint/vorssaint-utils" in caplog.text


@pytest.mark.usefixtures("tracked")
def test_sync_carries_on_after_one_repository_fails(
    triage: Triage,
    tmp_path: pathlib.Path,
    fetch_result: github_api.FetchResult,
    fake_sync: FakeSync,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    _other_repo_cache(tmp_path, fetch_result)

    def fetch(
        repo: repo_ref.RepoRef, since: str, comments: int, token: str
    ) -> github_api.FetchResult:
        if repo.full_name == "acme/widgets":
            raise github_api.GitHubError("boom")
        return dataclasses.replace(fetch_result, issues=[])

    monkeypatch.setattr(github_api, "fetch_changed_issues", fetch)

    assert triage("sync") == 1

    assert "vorssaint/vorssaint-utils · no changes" in capsys.readouterr().out
    assert "acme__widgets.db: boom" in caplog.text
    assert "1 of 2 repositories not synced" in caplog.text


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
