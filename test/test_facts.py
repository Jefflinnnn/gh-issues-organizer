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
    author: str | None = "reporter",
    author_type: str | None = "User",
    comments: tuple[tuple[str | None, int], ...] = (),
    comment_count: int | None = None,
    reactions: int = 0,
    replies: tuple[tuple[str, int], ...] = (),
    pulls: tuple[loader.StoredPullRequest, ...] = (),
) -> loader.OpenIssue:
    """Builds an issue.

    ``comments`` are (author_type, days_ago) pairs from unnamed authors;
    ``replies`` are (login, days_ago) pairs from people.
    """
    stored = [
        loader.StoredComment(
            author=None, author_type=kind, created_at=_days_ago(days)
        )
        for kind, days in comments
    ] + [
        loader.StoredComment(
            author=login, author_type="User", created_at=_days_ago(days)
        )
        for login, days in replies
    ]
    stored.sort(key=lambda comment: comment.created_at)
    return loader.OpenIssue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.com/o/r/issues/{number}",
        author=author,
        author_type=author_type,
        created_at=_days_ago(opened_days_ago),
        labels=[],
        comment_count=len(stored) if comment_count is None else comment_count,
        reaction_count=reactions,
        recent_comments=stored,
        linked_pull_requests=list(pulls),
    )


def _pull(
    state: str, *, merged_days_ago: int | None = None, draft: bool = False
) -> loader.StoredPullRequest:
    merged_at = None if merged_days_ago is None else _days_ago(merged_days_ago)
    return loader.StoredPullRequest(
        number=100, state=state, is_draft=draft, merged_at=merged_at
    )


def _flags(
    issue: loader.OpenIssue, stale_days: int = facts.DEFAULT_STALE_DAYS
) -> list[taxonomy.Flag]:
    return facts.compute([issue], NOW, stale_days=stale_days)[0].flags


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


def test_open_linked_pull_request_means_a_fix_is_in_progress() -> None:
    result = facts.compute([_issue(pulls=(_pull("OPEN"),))], NOW)[0]

    assert taxonomy.Flag.FIX_IN_PROGRESS in result.flags
    assert not result.draft_fix


def test_draft_only_fix_is_marked() -> None:
    issue = _issue(pulls=(_pull("OPEN", draft=True),))

    assert facts.compute([issue], NOW)[0].draft_fix


def test_closed_unmerged_pull_request_sets_no_fix_flag() -> None:
    assert _flags(_issue(pulls=(_pull("CLOSED"),))) == []


def test_shipped_fix_awaits_a_silent_reporter() -> None:
    issue = _issue(pulls=(_pull("MERGED", merged_days_ago=10),))

    flags = facts.compute([issue], NOW, releases=[_days_ago(5)])[0].flags

    assert flags == [taxonomy.Flag.FIX_PENDING_CONFIRMATION]


def test_reporter_reply_after_the_release_ends_the_wait() -> None:
    issue = _issue(
        pulls=(_pull("MERGED", merged_days_ago=10),),
        replies=(("reporter", 2),),
    )

    assert not facts.awaits_reporter(issue, [_days_ago(5)])


def test_other_peoples_replies_do_not_end_the_wait() -> None:
    issue = _issue(
        pulls=(_pull("MERGED", merged_days_ago=10),),
        replies=(("someone-else", 2), ("reporter", 7)),
    )

    assert facts.awaits_reporter(issue, [_days_ago(5)])


def test_merged_fix_waits_for_the_next_release() -> None:
    issue = _issue(pulls=(_pull("MERGED", merged_days_ago=3),))

    assert facts.fix_shipped_at(issue, [_days_ago(30)]) is None
    assert not facts.awaits_reporter(issue, [_days_ago(30)])


def test_first_release_after_the_merge_ships_the_fix() -> None:
    issue = _issue(pulls=(_pull("MERGED", merged_days_ago=10),))

    shipped = facts.fix_shipped_at(
        issue, [_days_ago(20), _days_ago(8), _days_ago(1)]
    )

    assert shipped == _days_ago(8)


def test_merge_ships_the_fix_without_releases() -> None:
    issue = _issue(pulls=(_pull("MERGED", merged_days_ago=10),))

    assert facts.fix_shipped_at(issue, []) == _days_ago(10)


def test_deleted_reporter_still_awaits_confirmation() -> None:
    issue = _issue(author=None, pulls=(_pull("MERGED", merged_days_ago=10),))

    assert facts.awaits_reporter(issue, [])
