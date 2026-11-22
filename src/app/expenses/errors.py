from app.errors import BusinessRuleError


class InvalidCategoryName(BusinessRuleError):  # noqa: N818
    """Raised when a category name is blank or exceeds the maximum length."""


class CategoryNotFound(BusinessRuleError):  # noqa: N818
    """Raised when an expense category does not exist."""


class CategoryNameTaken(BusinessRuleError):  # noqa: N818
    """Raised when another category already uses the requested name."""


class CategoryArchived(BusinessRuleError):  # noqa: N818
    """Raised when creating or moving an expense into an archived category."""


class InvalidExpenseAmount(BusinessRuleError):  # noqa: N818
    """Raised when an expense amount is not strictly positive."""


class InvalidExpenseNote(BusinessRuleError):  # noqa: N818
    """Raised when an expense note exceeds the maximum length."""


class InvalidExpensePeriod(BusinessRuleError):  # noqa: N818
    """Raised when the query start date is after the end date."""


class ExpenseNotFound(BusinessRuleError):  # noqa: N818
    """Raised when an expense does not exist or has been deleted."""


class ExpenseDeleted(BusinessRuleError):  # noqa: N818
    """Raised when attempting to upsert a soft-deleted expense."""


class RateNotAvailableForDate(BusinessRuleError):  # noqa: N818
    """Raised when BCV or USDT rate is not available for the expense date."""
