"""Tests for the Git/Cobra-style help layout."""

import pytest

from gh_triage import cli


def _help(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([*args, "--help"])
    assert exit_info.value.code == 0
    return capsys.readouterr().out


def _section_titles(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.endswith(":") and not line.startswith(" ")
    ]


def test_top_level_sections_follow_the_convention(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help(capsys)

    assert text.startswith("Track GitHub repositories")
    assert _section_titles(text) == [
        "Usage:",
        "Commands:",
        "Options:",
        "Examples:",
    ]
    assert text.rstrip().endswith(
        'Use "triage <command> --help" for more information about a command.'
    )


def test_commands_are_listed_flat(capsys: pytest.CaptureFixture[str]) -> None:
    lines = _help(capsys).splitlines()
    start = lines.index("Commands:") + 1

    names = [line.split()[0] for line in lines[start : start + 5]]

    assert names == ["setup", "add", "list", "remove", "completion"]
    assert all(line.startswith("  ") for line in lines[start : start + 5])


@pytest.mark.parametrize(
    ("command", "usage", "sections"),
    [
        (
            "add",
            "triage add [options] <repo>",
            [
                "Usage:",
                "Arguments:",
                "Options:",
                "Global Options:",
                "Repository formats:",
                "Examples:",
            ],
        ),
        (
            "list",
            "triage list [options]",
            ["Usage:", "Options:", "Global Options:", "Examples:"],
        ),
        (
            "remove",
            "triage remove [options] <repo>",
            [
                "Usage:",
                "Arguments:",
                "Options:",
                "Global Options:",
                "Examples:",
            ],
        ),
        (
            "completion",
            "triage completion [options] <shell>",
            [
                "Usage:",
                "Arguments:",
                "Options:",
                "Global Options:",
                "Setup:",
            ],
        ),
    ],
)
def test_command_help_layout(
    command: str,
    usage: str,
    sections: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help(capsys, command)

    assert f"Usage:\n  {usage}\n" in text
    assert [
        title for title in _section_titles(text) if title in sections
    ] == sections


def test_flags_show_their_placeholder_once_and_line_up(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = _help(capsys, "remove").splitlines()

    yes = next(line for line in lines if "--yes" in line)
    data_dir = next(line for line in lines if "--data-dir" in line)

    assert yes.startswith("  -y, --yes ")
    assert data_dir.startswith("      --data-dir <path> ")
    assert yes.index("--yes") == data_dir.index("--data-dir")


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert capsys.readouterr().out.startswith("Track GitHub repositories")


def test_usage_errors_point_to_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["add"])

    assert exit_info.value.code == 2
    assert capsys.readouterr().err.splitlines() == [
        "Error: the following arguments are required: <repo>",
        "Usage: triage add [options] <repo>",
        "Run 'triage add --help' for more information.",
    ]
