"""Computes the flags that come from facts in the cache, not the model.

Each run computes them fresh from the stored issues and the current time,
so a flag such as ``stale`` can't go out of date in storage.
"""

import dataclasses
import datetime
import math
from collections.abc import Sequence

from triager import loader
from triager import taxonomy

DEFAULT_STALE_DAYS = 60
# The busiest tenth of a repository's open issues counts as hot.
HOT_FRACTION = 0.10
# Below this, an issue isn't hot even in a quiet repository where it ranks
# near the top.
HOT_MIN_ENGAGEMENT = 3

_BOT = "Bot"


@dataclasses.dataclass(frozen=True)
class IssueFacts:
    """The computed facts about one open issue.

    Attributes:
        issue: The issue they describe.
        last_human_activity: When a person last opened or commented on it.
        idle_days: Whole days from ``last_human_activity`` to now.
        engagement: Reactions plus comments.
        flags: The flags that apply, in ``taxonomy.Flag`` order.
    """

    issue: loader.OpenIssue
    last_human_activity: datetime.datetime
    idle_days: int
    engagement: int
    flags: list[taxonomy.Flag]


def compute(
    issues: Sequence[loader.OpenIssue],
    now: datetime.datetime,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> list[IssueFacts]:
    """Computes the facts and flags for every open issue of a repository.

    Args:
        issues: All the repository's stored open issues. ``hot`` ranks each
            issue against the others, so pass the full set.
        now: The current time, timezone-aware.
        stale_days: Days without human activity after which an issue is
            stale.

    Returns:
        One entry per issue, in the order given.

    Raises:
        ValueError: ``stale_days`` is less than 1.
    """
    if stale_days < 1:
        raise ValueError(f"stale_days must be at least 1, got {stale_days}")
    hot_threshold = _hot_threshold([engagement(issue) for issue in issues])
    results = []
    for issue in issues:
        last_active = last_human_activity(issue)
        idle_days = (now - last_active).days
        score = engagement(issue)
        flags: list[taxonomy.Flag] = []
        if idle_days >= stale_days:
            flags.append(taxonomy.Flag.STALE)
        if score >= hot_threshold:
            flags.append(taxonomy.Flag.HOT)
        results.append(
            IssueFacts(
                issue=issue,
                last_human_activity=last_active,
                idle_days=idle_days,
                engagement=score,
                flags=flags,
            )
        )
    return results


def stale_window(requested_days: int, repo_days: int | None) -> int:
    """Returns how many idle days make an issue stale.

    A repository that marks issues stale sooner than we would never lets
    one sit long enough for our window, so its own window is the maximum.

    Args:
        requested_days: Our window: the default or the user's choice.
        repo_days: The repo-wide window the repository's own files define,
            or None if they define none.

    Returns:
        The shorter of the two windows.
    """
    if repo_days is None:
        return requested_days
    return min(requested_days, repo_days)


def last_human_activity(issue: loader.OpenIssue) -> datetime.datetime:
    """Returns when a person last opened or commented on an issue.

    Bot comments, such as a stale-bot's warning, don't count. Neither do
    label or title edits, which also change GitHub's ``updatedAt``. An
    author of unknown type counts as a person: that's a deleted account,
    which is rarely a bot, or a row stored before types were recorded.

    Only the stored comments are seen. If every stored comment is from a
    bot, older human comments are missed and the issue looks idle since it
    was opened.

    Args:
        issue: The issue to check.

    Returns:
        The latest human comment's time, or when the issue was opened.
    """
    human_comments = [
        comment.created_at
        for comment in issue.recent_comments
        if comment.author_type != _BOT
    ]
    # A bot-opened issue still counts from its creation: someone has to
    # look at it.
    return max([issue.created_at, *human_comments])


def engagement(issue: loader.OpenIssue) -> int:
    """Returns an issue's reactions plus comments.

    Args:
        issue: The issue to score.

    Returns:
        The engagement score.
    """
    return issue.reaction_count + issue.comment_count


def _hot_threshold(scores: Sequence[int]) -> int:
    """Returns the lowest engagement that makes an issue hot.

    Ties at the cut-off are all hot, so the hot share can exceed
    ``HOT_FRACTION`` when several issues share a score.
    """
    if not scores:
        return HOT_MIN_ENGAGEMENT
    hot_count = math.ceil(len(scores) * HOT_FRACTION)
    cutoff = sorted(scores, reverse=True)[hot_count - 1]
    return max(cutoff, HOT_MIN_ENGAGEMENT)
