"""Small Hebrew wording helpers for the bot's fixed messages.

`count_he` keeps a count and its noun in agreement: "מניה אחת", "שעתיים", "5 מניות"
(never "1 מניות" or "לפני 1 שעות"). `NY_TIME` is the one way the messages name New
York time.
"""
from __future__ import annotations

NY_TIME = "שעון ניו יורק"


def _number(n: float, sep: bool) -> str:
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if isinstance(n, bool) or not isinstance(n, (int, float)):
        return str(n)
    if isinstance(n, int):
        return f"{n:,}" if sep else str(n)
    return f"{n:g}"


def count_he(n: float, one: str, many: str, two: str | None = None, *, sep: bool = False) -> str:
    """`one` is the whole phrase for 1 ("מניה אחת", "שעה", "אחת נכשלה"), `two` an optional
    dual form ("שעתיים"), `many` the plural noun or verb put after the number. `sep`
    groups thousands ("2,352")."""
    if n == 1:
        return one
    if n == 2 and two:
        return two
    return f"{_number(n, sep)} {many}"
