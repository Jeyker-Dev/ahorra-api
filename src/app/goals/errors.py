from app.errors import BusinessRuleError


class InvalidGoalName(BusinessRuleError):  # noqa: N818
    """Raised when a goal name is blank or exceeds the maximum length."""


class InvalidGoalTarget(BusinessRuleError):  # noqa: N818
    """Raised when a goal target amount is not strictly positive."""


class InvalidGoalNote(BusinessRuleError):  # noqa: N818
    """Raised when a goal note exceeds the maximum length."""


class InvalidContributionAmount(BusinessRuleError):  # noqa: N818
    """Raised when a contribution amount is not strictly positive."""


class InvalidContributionNote(BusinessRuleError):  # noqa: N818
    """Raised when a contribution note exceeds the maximum length."""


class GoalNotFound(BusinessRuleError):  # noqa: N818
    """Raised when a goal does not exist or has been deleted."""


class ContributionNotFound(BusinessRuleError):  # noqa: N818
    """Raised when a contribution does not exist on the target goal."""


class GoalNameTaken(BusinessRuleError):  # noqa: N818
    """Raised when another non-deleted goal already uses the requested name."""


class GoalDeleted(BusinessRuleError):  # noqa: N818
    """Raised when attempting to upsert a soft-deleted goal."""


class GoalArchived(BusinessRuleError):  # noqa: N818
    """Raised when creating or moving a contribution onto an archived goal."""


class ContributionDeleted(BusinessRuleError):  # noqa: N818
    """Raised when attempting to upsert a soft-deleted contribution."""


class RateNotAvailableForDate(BusinessRuleError):  # noqa: N818
    """Raised when BCV or USDT rate is not available for the contribution date."""
