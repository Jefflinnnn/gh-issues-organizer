"""Tests for showing stored UTC times in local time."""

import time
from collections.abc import Iterator

import pytest

from gh_triage import timefmt

pytestmark = pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="needs time.tzset to switch zones"
)


@pytest.fixture
def los_angeles(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Sets the process timezone to America/Los_Angeles for one test."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.usefixtures("los_angeles")
@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("2026-10-30T05:18:50+00:00", "2026-10-29 22:18 PDT"),
        # 2026-11-01 01:30 happens twice in Los Angeles; UTC tells them apart.
        ("2026-11-01T08:30:00+00:00", "2026-11-01 01:30 PDT"),
        ("2026-11-01T09:30:00+00:00", "2026-11-01 01:30 PST"),
    ],
)
def test_local_display_converts_utc(stored: str, shown: str) -> None:
    assert timefmt.local_display(stored) == shown


def test_local_display_in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        assert (
            timefmt.local_display("2026-10-30T05:18:50+00:00")
            == "2026-10-30 05:18 UTC"
        )
    finally:
        monkeypatch.undo()
        time.tzset()
