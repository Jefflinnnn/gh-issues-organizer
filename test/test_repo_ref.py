import pytest

from gh_triage import repo_ref


@pytest.mark.parametrize(
    "text",
    [
        "vorssaint/vorssaint-utils",
        "https://github.com/vorssaint/vorssaint-utils",
        "https://github.com/vorssaint/vorssaint-utils/",
        "https://github.com/vorssaint/vorssaint-utils.git",
        "https://github.com/vorssaint/vorssaint-utils/issues",
        "https://github.com/vorssaint/vorssaint-utils/issues/3663",
        "http://www.github.com/vorssaint/vorssaint-utils",
        "github.com/vorssaint/vorssaint-utils",
        "git@github.com:vorssaint/vorssaint-utils.git",
        "  vorssaint/vorssaint-utils  ",
    ],
)
def test_parse_accepts_common_forms(text: str) -> None:
    assert repo_ref.parse(text) == repo_ref.RepoRef(
        "vorssaint", "vorssaint-utils"
    )


def test_parse_keeps_dots_in_repo_names() -> None:
    assert repo_ref.parse("owner/my.dotted.repo").name == "my.dotted.repo"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "vorssaint",
        "github.com/vorssaint",
        "https://gitlab.com/vorssaint/vorssaint-utils",
        "owner/repo/extra",
        "owner/..",
        "own.er/repo",
    ],
)
def test_parse_rejects_non_repositories(text: str) -> None:
    with pytest.raises(repo_ref.InvalidRepoRefError):
        repo_ref.parse(text)


def test_db_filename_ignores_case() -> None:
    upper = repo_ref.RepoRef("Vorssaint", "Vorssaint-Utils")
    lower = repo_ref.RepoRef("vorssaint", "vorssaint-utils")
    assert upper.db_filename == lower.db_filename
    assert lower.db_filename == "vorssaint__vorssaint-utils.db"
