"""The current time, the way this codebase stores it: UTC, with no zone attached.

`datetime.utcnow()` does the same thing but is deprecated from Python 3.12. Every database column here is a naive UTC
timestamp, so this keeps that convention in one named place instead of spreading a deprecated call through the code."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
