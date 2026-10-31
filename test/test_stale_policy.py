import textwrap

import pytest

from issue_store import stale_policy

WORKFLOW = ".github/workflows/stale.yml"


def _workflow(inputs: str = "", uses: str = "actions/stale@v9") -> str:
    """Returns a workflow with one stale step; ``inputs`` go under with."""
    with_block = textwrap.indent(textwrap.dedent(inputs), " " * 10)
    return f"""\
on:
  schedule:
    - cron: '0 0 * * *'
jobs:
  stale:
    runs-on: ubuntu-latest
    steps:
      - uses: {uses}
        with:
          repo-token: x
{with_block}"""


def test_reads_the_issue_window_of_actions_stale() -> None:
    files = {WORKFLOW: _workflow("days-before-issue-stale: 90\n")}

    assert stale_policy.find(files) == stale_policy.StalePolicy(90, WORKFLOW)


def test_issue_window_overrides_the_shared_one() -> None:
    files = {
        WORKFLOW: _workflow(
            "days-before-stale: 30\ndays-before-issue-stale: 45\n"
        )
    }

    assert stale_policy.find(files) == stale_policy.StalePolicy(45, WORKFLOW)


def test_actions_stale_defaults_to_sixty_days() -> None:
    assert stale_policy.find({WORKFLOW: _workflow()}) == (
        stale_policy.StalePolicy(60, WORKFLOW)
    )


@pytest.mark.parametrize(
    "inputs",
    [
        "days-before-issue-stale: -1\n",
        "days-before-stale: ${{ vars.STALE_DAYS }}\n",
        "only-labels: needs-info\n",
        "any-of-issue-labels: question\n",
    ],
    ids=["disabled", "expression", "only-labels", "any-of-labels"],
)
def test_ignores_rules_that_are_not_a_repo_wide_window(inputs: str) -> None:
    assert stale_policy.find({WORKFLOW: _workflow(inputs)}) is None


def test_reads_quoted_numbers() -> None:
    files = {WORKFLOW: _workflow("days-before-issue-stale: '21'\n")}

    assert stale_policy.find(files) == stale_policy.StalePolicy(21, WORKFLOW)


def test_ignores_workflows_without_a_stale_step() -> None:
    files = {".github/workflows/ci.yml": _workflow(uses="actions/checkout@v4")}

    assert stale_policy.find(files) is None


def test_reads_probot_stale_and_its_issue_override() -> None:
    config = "daysUntilStale: 60\nissues:\n  daysUntilStale: 30\n"

    assert stale_policy.find({stale_policy.PROBOT_PATH: config}) == (
        stale_policy.StalePolicy(30, stale_policy.PROBOT_PATH)
    )


@pytest.mark.parametrize(
    "config",
    ["daysUntilStale: false\n", "daysUntilStale: 30\nonlyLabels: [waiting]\n"],
    ids=["disabled", "only-labels"],
)
def test_ignores_probot_rules_that_are_not_repo_wide(config: str) -> None:
    assert stale_policy.find({stale_policy.PROBOT_PATH: config}) is None


def test_shortest_window_wins_across_files() -> None:
    files = {
        stale_policy.PROBOT_PATH: "daysUntilStale: 40\n",
        WORKFLOW: _workflow("days-before-issue-stale: 90\n"),
    }

    assert stale_policy.find(files) == (
        stale_policy.StalePolicy(40, stale_policy.PROBOT_PATH)
    )


def test_skips_files_that_are_not_valid_yaml() -> None:
    files = {
        ".github/workflows/broken.yml": "jobs: [unclosed\n",
        WORKFLOW: _workflow("days-before-issue-stale: 90\n"),
    }

    assert stale_policy.find(files) == stale_policy.StalePolicy(90, WORKFLOW)


def test_no_files_means_no_policy() -> None:
    assert stale_policy.find({}) is None
