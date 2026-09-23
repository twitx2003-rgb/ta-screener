"""Reading provider rows without guessing.

A guessed field is how a strike gets reported as a price: every value taken from
a vendor payload goes through `pick`, which names the keys actually present when
the one we expect is missing.
"""
from __future__ import annotations

from typing import Any, Sequence

from .errors import ProviderError


def pick(row: dict[str, Any], candidates: Sequence[str], *, context: str,
         allow_null: bool = False) -> Any:
    """Return the value of the first candidate key the row actually has.

    Missing and null are different failures:

    - a **missing** key means the payload shape changed or we mapped it wrong —
      always an error;
    - a **null** value means the vendor has nothing for this row, which is
      legitimate for some fields and a data error for others. `allow_null` says
      which.
    """
    if not isinstance(row, dict):
        raise ProviderError(f"{context}: expected an object, got {type(row).__name__}")
    for key in candidates:
        if key in row:
            value = row[key]
            if value is None and not allow_null:
                raise ProviderError(
                    f"{context}: '{key}' is present but null, and a null is not "
                    f"acceptable for it (row {_row_ident(row)})"
                )
            return value
    raise ProviderError(
        f"{context}: none of {list(candidates)} present in response row. "
        f"Actual keys: {sorted(row)}"
    )


def _row_ident(row: dict[str, Any]) -> dict[str, Any]:
    """The few fields that identify a row in an error message."""
    return {k: row[k] for k in ("symbol", "name", "ticker", "t") if k in row}
