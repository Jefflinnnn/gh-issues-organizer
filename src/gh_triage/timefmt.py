"""Shows stored UTC timestamps in the user's local time.

The cache stores UTC because GitHub's timestamps are UTC and UTC never
repeats an hour at a daylight-saving change. Converting happens only when
a time is shown.
"""

import datetime


def local_display(utc_iso: str) -> str:
    """Formats an ISO 8601 UTC timestamp in the system's local timezone.

    The local timezone comes from the operating system, so setting the
    ``TZ`` environment variable changes it.

    Args:
        utc_iso: A timestamp with an offset, e.g.
            ``2026-10-30T05:18:50+00:00``.

    Returns:
        The time as ``YYYY-MM-DD HH:MM`` plus the zone abbreviation, e.g.
        ``2026-10-29 22:18 PDT``.
    """
    moment = datetime.datetime.fromisoformat(utc_iso).astimezone()
    return moment.strftime("%Y-%m-%d %H:%M %Z").rstrip()
