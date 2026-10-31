import datetime

import pytest

from triager import facts
from triager import loader
from triager import taxonomy

NOW = datetime.datetime(2026, 10, 31, 12, 0, tzinfo=datetime.timezone.utc)


def _days_ago(days: int) -> datetime.datetime:
    return NOW - datetime.timedelta(days=days)


def _issue(
    number: int = 1,
    *,
    opened_days_ago: int = 0,
    author_type: str | None = "User",
    comments: tuple[tuple[str | None, int], ...] = (),
    comment_count: int | None = None,
    reactions: int = 0,
) -> loader.OpenIssue:
    """Builds an issue; ``comments`` are (author_type, days_ago) pairs."""
    return loader.OpenIssue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/o/r/issues/{number}",
        author_type=author_type,
        created_at=_days_ago(opened_days_ago),
        labels=[],
        comment_count=len(comments) if comment_count is None else comment_count,
        reaction_count=reactions,
        recent_comments=[
            loader.StoredComment(author_type=kind, created_at=_days_ago(days))
            for kind, days in comments
        ],
    )


def _flags(issue: loader.OpenIssue, **kwargs: int) -> list[taxonomy.Flag]:
    return facts.compute([issue], NOW, **kwargs)[0].flags


def test_issue_without_human_activity_for_the_window_is_stale() -> None:
    issue = _issue(opened_days_ago=90, comments=(("User", 60),))

    assert _flags(issue) == [taxonomy.Flag.STALE]


def test_recent_human_comment_keeps_an_old_issue_fresh() -> None:
    issue = _issue(opened_days_ago=200, comments=(("User", 59),))

    assert taxonomy.Flag.STALE not in _flags(issue)


def test_bot_comments_do_not_reset_the_stale_clock() -> None:
    issue = _issue(opened_days_ago=120, comments=(("User", 100), ("Bot", 1)))

    result = facts.compute([issue], NOW)[0]

    assert result.idle_days == 100
    assert taxonomy.Flag.STALE in result.flags


def test_deleted_accounts_count_as_people() -> None:
    issue = _issue(opened_days_ago=120, comments=((None, 2),))

    assert facts.last_human_activity(issue) == _days_ago(2)


def test_bot_opened_issue_counts_from_creation() -> None:
    issue = _issue(opened_days_ago=70, author_type="Bot")

    assert _flags(issue) == [taxonomy.Flag.STALE]


def test_stale_days_is_configurable() -> None:
    issue = _issue(opened_days_ago=70)

    assert _flags(issue, stale_days=90) == []
    assert _flags(issue, stale_days=30) == [taxonomy.Flag.STALE]


def test_stale_days_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        facts.compute([_issue()], NOW, stale_days=0)


def test_top_tenth_by_reactions_plus_comments_is_hot() -> None:
    issues = [_issue(n, reactions=n) for n in range(1, 21)]

    hot = [
        result.issue.number
        for result in facts.compute(issues, NOW)
        if taxonomy.Flag.HOT in result.flags
    ]

    assert hot == [19, 20]


def test_comments_count_toward_engagement() -> None:
    issue = _issue(comment_count=4, reactions=1)

    assert facts.engagement(issue) == 5


def test_ties_at_the_cutoff_are_all_hot() -> None:
    issues = [_issue(n, reactions=10 if n <= 3 else 1) for n in range(1, 11)]

    hot = [
        result.issue.number
        for result in facts.compute(issues, NOW)
        if taxonomy.Flag.HOT in result.flags
    ]

    assert hot == [1, 2, 3]


def test_quiet_repository_has_no_hot_issues() -> None:
    issues = [_issue(n, reactions=2) for n in range(1, 4)]

    assert all(
        taxonomy.Flag.HOT not in result.flags
        for result in facts.compute(issues, NOW)
    )


def test_no_issues_gives_no_facts() -> None:
    assert facts.compute([], NOW) == []


def test_repositorys_shorter_stale_window_wins() -> None:
    assert facts.stale_window(60, 30) == 30


def test_our_window_stands_when_the_repository_is_more_lenient() -> None:
    assert facts.stale_window(60, 90) == 60


def test_our_window_stands_when_the_repository_defines_none() -> None:
    assert facts.stale_window(45, None) == 45
