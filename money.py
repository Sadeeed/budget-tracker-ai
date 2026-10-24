"""Deterministic monetary arithmetic in integer minor currency units (paisa).

1 PKR = 100 paisa.
All financial arithmetic and database persistence must use integer minor units.
Never use floating point numbers for financial calculations.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Union


MINOR_UNIT_FACTOR = 100  # 1 PKR = 100 paisa


def parse_to_minor(val: Union[str, int, float, Decimal]) -> int:
    """Parse a user or file value into integer minor units (paisa).

    Converts via Decimal to avoid floating-point inaccuracies.
    Rejects negative amounts with a ValueError, as amounts in BudgetPilot
    must be non-negative (transaction type differentiates expense vs refund).
    """
    if val is None:
        raise ValueError("Amount cannot be None.")

    if isinstance(val, (int, Decimal)):
        dec = Decimal(val)
    elif isinstance(val, float):
        # Convert float via string representation to preserve decimal digits
        dec = Decimal(str(val))
    elif isinstance(val, str):
        # Clean currency symbols, commas, whitespace
        cleaned = re.sub(r"[^\d.-]", "", val.strip())
        if not cleaned:
            raise ValueError(f"Cannot parse monetary value from empty or invalid string: {val!r}")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as e:
            raise ValueError(f"Invalid monetary string: {val!r}") from e
    else:
        raise TypeError(f"Unsupported amount type: {type(val)}")

    if dec < 0:
        raise ValueError(
            f"Negative amount ({dec}) is not allowed. "
            "Use a positive amount with an appropriate transaction type (e.g. 'refund')."
        )

    # Multiply by 100 and quantize to nearest integer minor unit
    minor_dec = (dec * MINOR_UNIT_FACTOR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor_dec)


def minor_to_decimal(minor: int) -> Decimal:
    """Convert integer minor units back to Decimal with 2 decimal places."""
    if not isinstance(minor, int):
        raise TypeError(f"minor must be an int, got {type(minor)}")
    return (Decimal(minor) / Decimal(MINOR_UNIT_FACTOR)).quantize(Decimal("0.01"))


def format_pkr(minor: int, show_symbol: bool = True) -> str:
    """Format an integer minor unit into standard PKR display string (e.g., 'Rs 12,500.00')."""
    if not isinstance(minor, int):
        raise TypeError(f"minor must be an int, got {type(minor)}")

    is_negative = minor < 0
    abs_minor = abs(minor)
    dec = (Decimal(abs_minor) / Decimal(MINOR_UNIT_FACTOR)).quantize(Decimal("0.01"))
    formatted_num = f"{dec:,.2f}"

    prefix = "Rs " if show_symbol else ""
    if is_negative:
        return f"-{prefix}{formatted_num}"
    return f"{prefix}{formatted_num}"


def format_currency(minor: int, currency: str = "PKR") -> str:
    """Format an integer minor unit with a specific currency code."""
    if currency.upper() == "PKR":
        return format_pkr(minor)
    dec = minor_to_decimal(minor)
    return f"{currency} {dec:,.2f}"
