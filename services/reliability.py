from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from database.session import session_scope
from models.orm import User, OperationalLog
from utils.bootstrap import get_app_user


class OperationalError(RuntimeError):
    pass


def log_operational_event(s, user: User, event_type: str, details: str | None = None) -> None:
    """Record an operational event with UTC timestamp."""
    if user is None:
        raise OperationalError("User is required for operational logging.")
    log = OperationalLog(
        user_id=user.id,
        event_type=event_type,
        details=details,
        timestamp=datetime.now(timezone.utc),
    )
    s.add(log)


def recent_operational_events(s, user: User, days: int = 7) -> list[OperationalLog]:
    """Return operational events for this user within the last N days."""
    if user is None:
        raise OperationalError("User is required for operational logging.")
    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(OperationalLog).where(
        OperationalLog.user_id == user.id,
        OperationalLog.timestamp >= threshold,
    )
    return s.scalars(query).all()


def redact_sensitive_details(details: str | None) -> str | None:
    """Remove credentials, tokens, and other sensitive data from logs."""
    if details is None:
        return None
    for pattern in [
        r"(\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b)",  # Email addresses
        r"(\b[A-Fa-f0-9]{32,}\b)",  # Hex tokens
        r"(\b[A-Za-z0-9_-]{20,}\b)",  # API keys
        r"(\bpassword\b|\bpasswd\b|\bsecret\b|\bkey\b|\btoken\b)",  # Keywords
    ]:
        details = re.sub(pattern, "[REDACTED]", details, flags=re.IGNORECASE)
    return details
