from app.errors import BusinessRuleError


class InvalidProductName(BusinessRuleError):  # noqa: N818
    """Raised when a product name is blank or exceeds the maximum length."""


class InvalidProductPrice(BusinessRuleError):  # noqa: N818
    """Raised when a product price is invalid or missing its currency pair."""


class InvalidPricePeriod(BusinessRuleError):  # noqa: N818
    """Raised when the price history query start date is after the end date."""


class ProductNotFound(BusinessRuleError):  # noqa: N818
    """Raised when a product does not exist."""


class ProductNameTaken(BusinessRuleError):  # noqa: N818
    """Raised when another product already uses the requested name."""


class RateNotAvailableForDate(BusinessRuleError):  # noqa: N818
    """Raised when BCV or USDT rate is not available for the recorded date."""
