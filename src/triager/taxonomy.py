"""The tags the triager assigns, split by who decides them.

The model decides an issue's type and, depending on the type, the bug
matrix or the enhancement priority. Code decides the flags from facts in
the cache. Values are the strings shown in reports and stored later.
"""

import enum


class IssueType(str, enum.Enum):
    """What kind of issue it is; decided by the model."""

    BUG = "bug"
    ENHANCEMENT = "enhancement"
    QUESTION = "question"
    NEEDS_INFO = "needs-info"


class Severity(str, enum.Enum):
    """How badly a bug hurts the people it affects; decided by the model."""

    HIGH = "high"
    LOW = "low"


class Priority(str, enum.Enum):
    """How soon a bug should be fixed; decided by the model."""

    HIGH = "high"
    LOW = "low"


class MoscowPriority(str, enum.Enum):
    """MoSCoW priority of an enhancement, without won't-have.

    Won't-have is a maintainer's call, so the model never assigns it.
    """

    MUST = "must-have"
    SHOULD = "should-have"
    COULD = "could-have"


class Flag(str, enum.Enum):
    """A fact about an issue that code computes from the cache."""

    STALE = "stale"
    HOT = "hot"
    FIX_IN_PROGRESS = "fix-in-progress"
    FIX_PENDING_CONFIRMATION = "fix-pending-confirmation"
    SAFE_TO_CLOSE = "safe-to-close"
