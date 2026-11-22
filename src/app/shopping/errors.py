from app.errors import BusinessRuleError


class InvalidListName(BusinessRuleError):  # noqa: N818
    """Raised when a shopping list name is blank or exceeds the maximum length."""


class InvalidItemQuantity(BusinessRuleError):  # noqa: N818
    """Raised when a shopping list item quantity is not strictly positive."""


class InvalidItemPrice(BusinessRuleError):  # noqa: N818
    """Raised when a buy price is invalid or missing its currency pair."""


class InvalidDaysAhead(BusinessRuleError):  # noqa: N818
    """Raised when recommendation days_ahead is outside the allowed range."""


class PriceRequired(BusinessRuleError):  # noqa: N818
    """Raised when buying an item that has no explicit or catalog price."""


class NothingBought(BusinessRuleError):  # noqa: N818
    """Raised when closing a shopping list that has no bought items."""


class ShoppingListNotFound(BusinessRuleError):  # noqa: N818
    """Raised when a shopping list does not exist or has been deleted."""


class ListItemNotFound(BusinessRuleError):  # noqa: N818
    """Raised when a shopping list item does not exist on the target list."""


class ProductNotFound(BusinessRuleError):  # noqa: N818
    """Raised when the referenced catalog product does not exist."""


class ProductArchived(BusinessRuleError):  # noqa: N818
    """Raised when adding or switching to an archived catalog product."""


class DuplicateListItem(BusinessRuleError):  # noqa: N818
    """Raised when adding a product that already exists on the shopping list."""


class ItemAlreadyBought(BusinessRuleError):  # noqa: N818
    """Raised when modifying or deleting a bought item in a disallowed way."""


class ListClosed(BusinessRuleError):  # noqa: N818
    """Raised when attempting to modify a closed shopping list."""


class ListDeleted(BusinessRuleError):  # noqa: N818
    """Raised when attempting to modify a soft-deleted shopping list."""


class RateNotAvailableForDate(BusinessRuleError):  # noqa: N818
    """Raised when BCV or USDT rate is not available for the target date."""
