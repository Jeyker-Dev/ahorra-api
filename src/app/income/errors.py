from app.errors import BusinessRuleError


class InvalidIncomeName(BusinessRuleError):  # noqa: N818
    """Raised when an income rule name is blank or exceeds the maximum length."""


class InvalidIncomeAmount(BusinessRuleError):  # noqa: N818
    """Raised when an income amount is not strictly positive."""


class InvalidIncomeDays(BusinessRuleError):  # noqa: N818
    """Raised when income rule days are empty or outside 1..31."""


class InvalidIncomePeriod(BusinessRuleError):  # noqa: N818
    """Raised when an income rule end_date precedes its start_date."""


class IncomeRuleNotFound(BusinessRuleError):  # noqa: N818
    """Raised when an income rule does not exist."""


class IncomeRuleEnded(BusinessRuleError):  # noqa: N818
    """Raised when attempting to modify an income rule that has already ended."""


class OccurrenceNotFound(BusinessRuleError):  # noqa: N818
    """Raised when an income occurrence does not exist."""


class OccurrenceNotReceived(BusinessRuleError):  # noqa: N818
    """Raised when undoing a receipt on an occurrence that is not received."""


class RateNotAvailableForDate(BusinessRuleError):  # noqa: N818
    """Raised when no BCV exchange rate is in force on the receipt date."""
