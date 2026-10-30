"""Tests for showing stored UTC times in local time."""

import time
from collections.abc import Callable
from collections.abc import Iterator

import pytest

from gh_triage import timefmt

pytestmark = pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="needs time.tzset to switch zones"
)


@pytest.fixture
def timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], None]]:
    """Returns a function that sets the process timezone for one test."""

    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield use
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ("zone", "stored", "shown"),
    [
        (
            "America/Los_Angeles",
            "2026-10-30T05:18:50+00:00",
            "2026-10-29 22:18 PDT",
        ),
        # 2026-11-01 01:30 happens twice in Los Angeles; UTC tells them apart.
        (
            "America/Los_Angeles",
            "2026-11-01T08:30:00+00:00",
            "2026-11-01 01:30 PDT",
        ),
        (
            "America/Los_Angeles",
            "2026-11-01T09:30:00+00:00",
            "2026-11-01 01:30 PST",
        ),
        ("UTC", "2026-10-30T05:18:50+00:00", "2026-10-30 05:18 UTC"),
    ],
)
def test_local_display_converts_utc(
    timezone: Callable[[str], None], zone: str, stored: str, shown: str
) -> None:
    timezone(zone)
    assert timefmt.local_display(stored) == shown
