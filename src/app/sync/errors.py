from app.errors import BusinessRuleError


class InvalidSyncCursor(BusinessRuleError):  # noqa: N818
    """Raised when a sync cursor is malformed, not UTC ISO-8601, or ahead of until."""
