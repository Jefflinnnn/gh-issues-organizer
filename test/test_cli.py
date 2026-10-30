"""Tests for the CLI with GitHub replaced by the recorded response."""

import dataclasses
import pathlib

import pytest

from gh_triage import cli
from gh_triage import github_api
from gh_triage import repo_ref

DB_NAME = "vorssaint__vorssaint-utils.db"


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


@pytest.mark.parametrize(
    "args",
    [
        ["-v", "add", "vorssaint/vorssaint-utils"],
        ["add", "-v", "vorssaint/vorssaint-utils"],
        ["add", "vorssaint/vorssaint-utils", "-v"],
    ],
)
@pytest.mark.usefixtures("fake_github")
def test_shared_options_work_before_or_after_the_command(
    tmp_path: pathlib.Path, args: list[str]
) -> None:
    assert cli.main(["--data-dir", str(tmp_path), *args]) == 0
    assert [p.name for p in tmp_path.iterdir()] == [DB_NAME]


@pytest.mark.usefixtures("fake_github")
def test_data_dir_after_the_command_is_used(tmp_path: pathlib.Path) -> None:
    exit_code = cli.main(
        ["add", "vorssaint/vorssaint-utils", "--data-dir", str(tmp_path)]
    )

    assert exit_code == 0
    assert [p.name for p in tmp_path.iterdir()] == [DB_NAME]


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert "usage: triage" in output
    assert "triage add https://github.com/" in output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["-h"], "examples:"),
        (["add", "-h"], "git@github.com:"),
        (["list", "-h"], "triage list"),
    ],
)
def test_help_shows_examples(
    args: list[str], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args)

    assert exit_info.value.code == 0
    assert expected in capsys.readouterr().out


def test_missing_repo_points_to_examples(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["add"])

    assert exit_info.value.code == 2
    assert "Run 'triage add -h' for examples." in capsys.readouterr().err


class _FakeStdin:
    """A stand-in for sys.stdin that looks like a terminal."""

    def __init__(self, answer: str, is_terminal: bool = True) -> None:
        self._answer = answer
        self._is_terminal = is_terminal

    def isatty(self) -> bool:
        return self._is_terminal

    def readline(self) -> str:
        return self._answer


@pytest.fixture
def tracked(tmp_path: pathlib.Path, fake_github: object) -> pathlib.Path:
    """Tracks the fixture repo and returns its cache file."""
    assert _run(tmp_path, "add", "vorssaint/vorssaint-utils") == 0
    return tmp_path / DB_NAME


def test_remove_with_yes_deletes_the_file(
    tmp_path: pathlib.Path,
    tracked: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()

    assert _run(tmp_path, "remove", "vorssaint/vorssaint-utils", "--yes") == 0

    assert not tracked.exists()
    assert "Removed vorssaint/vorssaint-utils" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["y\n", "YES\n"])
def test_remove_deletes_after_yes(
    tmp_path: pathlib.Path,
    tracked: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin(answer))

    assert (
        _run(tmp_path, "remove", "https://github.com/Vorssaint/Vorssaint-Utils")
        == 0
    )

    assert not tracked.exists()
    assert (
        "3 stored issues? You'll need 'triage add'" in capsys.readouterr().err
    )


@pytest.mark.parametrize("answer", ["\n", "n\n", "nope\n", ""])
def test_remove_keeps_the_file_unless_confirmed(
    tmp_path: pathlib.Path,
    tracked: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin(answer))

    assert _run(tmp_path, "remove", "vorssaint/vorssaint-utils") == 0

    assert tracked.exists()
    assert "Nothing removed." in capsys.readouterr().out


def test_remove_without_a_terminal_needs_yes(
    tmp_path: pathlib.Path,
    tracked: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("y\n", is_terminal=False))

    assert _run(tmp_path, "remove", "vorssaint/vorssaint-utils") == 1

    assert tracked.exists()
    assert "--yes" in caplog.text


def test_remove_an_untracked_repo_fails(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert _run(tmp_path, "remove", "nobody/nothing", "--yes") == 1
    assert "not tracked" in caplog.text


def test_remove_deletes_an_unreadable_file(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / DB_NAME
    broken.write_bytes(b"not a database")

    assert _run(tmp_path, "remove", "vorssaint/vorssaint-utils", "--yes") == 0

    assert not broken.exists()
    assert "Removed vorssaint/vorssaint-utils" in capsys.readouterr().out


@pytest.mark.usefixtures("tracked")
def test_remove_then_add_tracks_again(
    tmp_path: pathlib.Path, fake_github: object
) -> None:
    _run(tmp_path, "remove", "vorssaint/vorssaint-utils", "--yes")

    assert _run(tmp_path, "add", "vorssaint/vorssaint-utils") == 0
    assert (tmp_path / DB_NAME).exists()
