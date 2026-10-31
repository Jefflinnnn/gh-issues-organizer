"""Tests for `triage setup` and `triage completion install`."""

import pathlib
from collections.abc import Callable

import pytest

from issue_store import github_api
from triage_cli import cli
from triage_cli import completion

ZSH_LINES = '# triage: Tab completion\neval "$(triage completion zsh)"\n'


@pytest.fixture
def zshrc(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """Points HOME at a temp folder and the shell at zsh."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    return tmp_path / ".zshrc"


@pytest.fixture
def good_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_api, "load_token", lambda: "test-token")
    monkeypatch.setattr(
        github_api,
        "check_token",
        lambda token: github_api.TokenStatus("octocat", 5000, 4999),
    )


# --- completion helpers -------------------------------------------------


@pytest.mark.parametrize(
    ("shell_path", "expected"),
    [
        ("/bin/zsh", "zsh"),
        ("/usr/local/bin/bash", "bash"),
        ("/usr/bin/fish", None),
        ("", None),
    ],
)
def test_detect_shell(shell_path: str, expected: str | None) -> None:
    assert completion.detect_shell({"SHELL": shell_path}) == expected


def test_startup_file_honors_zdotdir(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    assert completion.startup_file("zsh", {}, home) == home / ".zshrc"
    assert completion.startup_file("bash", {}, home) == home / ".bashrc"
    assert (
        completion.startup_file("zsh", {"ZDOTDIR": str(tmp_path)}, home)
        == tmp_path / ".zshrc"
    )


def test_install_keeps_existing_lines(tmp_path: pathlib.Path) -> None:
    startup = tmp_path / ".zshrc"
    startup.write_text("export EDITOR=vim", encoding="utf-8")

    completion.install(startup, "zsh")

    assert startup.read_text(encoding="utf-8") == (
        "export EDITOR=vim\n\n" + ZSH_LINES
    )
    assert completion.is_installed(startup, "zsh")
    assert not completion.is_installed(startup, "bash")


def test_install_creates_a_missing_file(tmp_path: pathlib.Path) -> None:
    startup = tmp_path / ".zshrc"
    completion.install(startup, "zsh")
    assert startup.read_text(encoding="utf-8") == ZSH_LINES


# --- triage completion install ------------------------------------------


def test_completion_install_with_yes(
    zshrc: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["completion", "install", "--yes"]) == 0

    assert zshrc.read_text(encoding="utf-8") == ZSH_LINES
    assert "added to ~/.zshrc" in capsys.readouterr().out


def test_completion_install_twice_adds_once(
    zshrc: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["completion", "install", "--yes"])
    capsys.readouterr()

    assert cli.main(["completion", "install", "--yes"]) == 0

    assert zshrc.read_text(encoding="utf-8").count("triage completion") == 1
    assert "already set up" in capsys.readouterr().out


def test_completion_install_named_shell_and_file(
    zshrc: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    custom = tmp_path / "custom_rc"

    exit_code = cli.main(
        [
            "completion",
            "install",
            "bash",
            "--yes",
            "--startup-file",
            str(custom),
        ]
    )

    assert exit_code == 0
    assert 'eval "$(triage completion bash)"' in custom.read_text(
        encoding="utf-8"
    )
    assert not zshrc.exists()


def test_completion_install_asks_first(
    zshrc: pathlib.Path,
    answer_prompt: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer_prompt("n\n")

    assert cli.main(["completion", "install"]) == 0

    assert not zshrc.exists()
    captured = capsys.readouterr()
    assert "This adds 2 lines to ~/.zshrc" in captured.err
    assert "Turn it on any time with: triage setup" in captured.out


def test_completion_install_without_terminal_needs_yes(
    zshrc: pathlib.Path,
    answer_prompt: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer_prompt("y\n", terminal=False)

    assert cli.main(["completion", "install"]) == 1

    assert not zshrc.exists()
    assert "--yes" in caplog.text


@pytest.mark.parametrize(
    ("shell_path", "message"),
    [("/usr/bin/fish", "not fish"), ("", "Couldn't tell")],
)
def test_completion_install_unsupported_shell(
    zshrc: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    shell_path: str,
    message: str,
) -> None:
    monkeypatch.setenv("SHELL", shell_path)

    assert cli.main(["completion", "install", "--yes"]) == 1

    assert message in caplog.text
    assert "triage setup --shell zsh" in caplog.text
    assert not zshrc.exists()


def test_completion_rejects_a_second_shell(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert cli.main(["completion", "zsh", "bash"]) == 1
    assert "triage completion install zsh" in caplog.text


# --- triage setup -------------------------------------------------------


@pytest.mark.usefixtures("good_token")
def test_setup_with_yes_finishes(
    zshrc: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["setup", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "GitHub token: works for octocat (4,999 of 5,000" in out
    assert "Tab completion: added to ~/.zshrc" in out
    assert "Ready. Try: triage add" in out
    assert zshrc.read_text(encoding="utf-8") == ZSH_LINES


@pytest.mark.usefixtures("good_token")
def test_setup_shell_overrides_an_unsupported_shell(
    zshrc: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/fish")

    assert cli.main(["setup", "--shell", "zsh", "--yes"]) == 0

    assert "Tab completion: added to ~/.zshrc" in capsys.readouterr().out
    assert zshrc.read_text(encoding="utf-8") == ZSH_LINES


@pytest.mark.usefixtures("good_token")
def test_setup_shell_picks_that_shells_startup_file(
    zshrc: pathlib.Path,
) -> None:
    assert cli.main(["setup", "--shell", "bash", "--yes"]) == 0

    bashrc = zshrc.with_name(".bashrc")
    assert 'eval "$(triage completion bash)"' in bashrc.read_text(
        encoding="utf-8"
    )
    assert not zshrc.exists()


@pytest.mark.usefixtures("good_token")
def test_setup_without_a_known_shell_points_to_shell_option(
    zshrc: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/fish")

    assert cli.main(["setup", "--yes"]) == 0

    assert "triage setup --shell zsh" in capsys.readouterr().out
    assert not zshrc.exists()


@pytest.mark.usefixtures("good_token")
def test_setup_again_skips_finished_steps(
    zshrc: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["setup", "--yes"])
    capsys.readouterr()

    assert cli.main(["setup"]) == 0

    assert "already set up" in capsys.readouterr().out
    assert zshrc.read_text(encoding="utf-8").count("triage completion") == 1


@pytest.mark.usefixtures("good_token")
def test_setup_no_leaves_completion_for_later(
    zshrc: pathlib.Path,
    answer_prompt: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer_prompt("n\n")

    assert cli.main(["setup"]) == 0

    assert not zshrc.exists()
    out = capsys.readouterr().out
    assert "Turn it on any time with: triage setup" in out
    assert "Ready." in out


@pytest.mark.usefixtures("good_token")
def test_setup_without_terminal_skips_completion(
    zshrc: pathlib.Path,
    answer_prompt: Callable[..., None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    answer_prompt("", terminal=False)

    assert cli.main(["setup"]) == 0

    assert not zshrc.exists()
    assert "triage setup --yes" in capsys.readouterr().out


@pytest.mark.usefixtures("zshrc")
def test_setup_without_a_token_fails_but_still_offers_completion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def missing() -> str:
        raise github_api.GitHubError("No GitHub token found.")

    monkeypatch.setattr(github_api, "load_token", missing)

    assert cli.main(["setup", "--yes"]) == 1

    out = capsys.readouterr().out
    assert "GitHub token: not found" in out
    assert "Tab completion: added" in out
    assert "Ready." not in out
    assert "run triage setup again" in caplog.text


@pytest.mark.usefixtures("zshrc")
def test_setup_with_a_rejected_token_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def rejected(token: str) -> github_api.TokenStatus:
        raise github_api.GitHubError("GitHub rejected the token (HTTP 401).")

    monkeypatch.setattr(github_api, "load_token", lambda: "bad-token")
    monkeypatch.setattr(github_api, "check_token", rejected)

    assert cli.main(["setup", "--yes"]) == 1
    assert "doesn't work. GitHub rejected the token" in capsys.readouterr().out


@pytest.mark.usefixtures("good_token", "zshrc")
def test_setup_with_an_unsupported_shell_still_finishes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/fish")

    assert cli.main(["setup", "--yes"]) == 0

    out = capsys.readouterr().out
    assert "supports bash and zsh, not fish" in out
    assert "Ready." in out
