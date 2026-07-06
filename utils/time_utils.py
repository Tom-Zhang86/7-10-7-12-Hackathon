from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 datetime saved by this project."""

    return datetime.fromisoformat(value)


def format_seconds(total_seconds: int) -> str:
    """Format seconds as a compact human-readable duration."""

    hours, remainder = divmod(max(total_seconds, 0), 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
