from app.errors import BusinessRuleError


class RateUnavailable(BusinessRuleError):  # noqa: N818
    """Raised when no upstream source could produce an exchange rate."""


class QuoteUnavailable(BusinessRuleError):  # noqa: N818
    """Raised when an individual external quote source fails."""


class RateNotFound(BusinessRuleError):  # noqa: N818
    """Raised when an exchange rate record does not exist."""


class RateNotInReview(BusinessRuleError):  # noqa: N818
    """Raised when confirming or correcting a rate whose status is not in_review."""


class InvalidRateValue(BusinessRuleError):  # noqa: N818
    """Raised when an exchange rate value is not strictly positive."""
