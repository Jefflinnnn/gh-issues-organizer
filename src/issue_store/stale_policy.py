"""Finds a repository's own repo-wide stale window in its config files.

Two tools define one in a file GitHub can serve: the ``actions/stale``
workflow action and the older Probot Stale app (``.github/stale.yml``). A
rule limited to some labels covers only some issues, so it is not
repo-wide and is ignored here; so are custom workflows, which need reading
rather than parsing.
"""

import dataclasses
import logging
from collections.abc import Mapping
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PROBOT_PATH = ".github/stale.yml"
WORKFLOWS_DIR = ".github/workflows"

# What actions/stale uses when a workflow doesn't set days-before-stale.
_ACTIONS_STALE_DEFAULT_DAYS = 60
# Inputs that limit an actions/stale rule to issues with certain labels.
_ACTIONS_STALE_SCOPE_INPUTS = (
    "only-labels",
    "only-issue-labels",
    "any-of-labels",
    "any-of-issue-labels",
)


@dataclasses.dataclass(frozen=True)
class StalePolicy:
    """A repo-wide stale window found in a repository's files.

    Attributes:
        days: Days without activity after which the repository marks an
            issue stale.
        source: The file that sets it, e.g. ``.github/workflows/stale.yml``.
    """

    days: int
    source: str


def find(files: Mapping[str, str]) -> StalePolicy | None:
    """Returns the shortest repo-wide stale window the files define.

    Args:
        files: Text of the repository's ``.github/stale.yml`` and workflow
            files, keyed by path. Missing files are simply absent.

    Returns:
        The policy, or None when no file defines a repo-wide window.
    """
    found = []
    for path, text in sorted(files.items()):
        document = _load(path, text)
        if document is None:
            continue
        if path == PROBOT_PATH:
            days = _probot_days(document)
        else:
            days = _workflow_days(document)
        if days is not None:
            found.append(StalePolicy(days=days, source=path))
    return min(found, key=lambda policy: policy.days, default=None)


def _load(path: str, text: str) -> Mapping[str, Any] | None:
    """Parses one YAML file, skipping files that aren't a YAML mapping."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        # One broken file in someone else's repository shouldn't stop a
        # fetch; it just can't define a stale window.
        logger.info("Skipping %s: not valid YAML", path)
        return None
    return document if isinstance(document, Mapping) else None


def _probot_days(config: Mapping[str, Any]) -> int | None:
    """Returns the issue stale window from a Probot Stale config."""
    issues = config.get("issues")
    settings = {**config, **(issues if isinstance(issues, Mapping) else {})}
    if settings.get("onlyLabels"):
        return None
    return _positive_days(settings.get("daysUntilStale"))


def _workflow_days(workflow: Mapping[str, Any]) -> int | None:
    """Returns the shortest issue stale window of a workflow's stale steps."""
    windows = []
    jobs = workflow.get("jobs")
    for job in jobs.values() if isinstance(jobs, Mapping) else ():
        steps = job.get("steps") if isinstance(job, Mapping) else None
        for step in steps if isinstance(steps, list) else ():
            if not isinstance(step, Mapping):
                continue
            uses = step.get("uses")
            if not isinstance(uses, str) or not uses.startswith(
                "actions/stale@"
            ):
                continue
            inputs = step.get("with")
            inputs = inputs if isinstance(inputs, Mapping) else {}
            if any(inputs.get(name) for name in _ACTIONS_STALE_SCOPE_INPUTS):
                continue
            days = _positive_days(
                inputs.get(
                    "days-before-issue-stale",
                    inputs.get(
                        "days-before-stale", _ACTIONS_STALE_DEFAULT_DAYS
                    ),
                )
            )
            if days is not None:
                windows.append(days)
    return min(windows, default=None)


def _positive_days(value: Any) -> int | None:
    """Returns a day count, or None for unset, disabled or ``${{ }}``."""
    # Action inputs are strings to GitHub, so "30" is as valid as 30.
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    # bool is an int in Python, and Probot uses false to turn staling off.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None
