"""The text checks every model-written Hebrew text passes (moved from the removed
discussion channels, 2026-09-27): the numbers a text may quote, and no advice, trading
claim or forecast wording."""
from __future__ import annotations

import math
import re

DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?\b")
NUMBER = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])\d+(?:\.\d+)?(?![\w])")
STRUCTURAL = {10.0, 14.0, 20.0, 30.0, 50.0, 52.0, 70.0, 80.0, 100.0, 150.0}
BANNED_WORDS = {"קנו", "תקנו", "מכרו", "תמכרו", "כנסו", "תיכנסו", "היכנסו", "צאו", "סטופ",
                "סטופלוס", "ממליץ", "ממליצה", "ממליצים", "המלצה", "המלצתי", "תשקיעו", "קניתי",
                "מכרתי", "נכנסתי", "שורטתי", "buy", "sell"}
BANNED_PHRASES = ("לקנות עכשיו", "למכור עכשיו", "כדאי לקנות", "כדאי למכור", "בטוח עולה",
                  "בטוח יורד", "בטוח תעלה", "בטוח יעלה", "בטוח תרד", "בטוח ירד", "הולך לעלות",
                  "הולכת לעלות", "הולך לרדת", "הולכת לרדת", "חייב לעלות", "חייבת לעלות",
                  "חייב לרדת", "חייבת לרדת", "stop loss")
PREFIXES = "והשבלכמ"


def fact_numbers(facts: dict[str, dict]) -> list[float]:
    """Every number a post may quote: numeric fact values, and numbers inside text
    facts and labels (thresholds such as '>= 10'), dates excepted."""
    out = []
    for fact in facts.values():
        value = fact.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out.append(abs(float(value)))
        for text in (value if isinstance(value, str) else "", fact.get("label", "")):
            out += [abs(float(n.replace(",", ""))) for n in NUMBER.findall(DATE.sub(" ", text))]
    return out


def banned(text: str) -> list[str]:
    """Advice, trading-claim and forecast wording. Hebrew attaches prepositions and
    conjunctions to words ("וקנו"), so up to two such prefix letters are peeled off."""
    low = text.lower()
    found = [p for p in BANNED_PHRASES if p in low]
    for token in re.findall(r"[א-תA-Za-z]+", low):
        forms, word = {token}, token
        for _ in range(2):
            if len(word) > 3 and word[0] in PREFIXES:
                word = word[1:]
                forms.add(word)
        if forms & BANNED_WORDS:
            found.append(token)
    return found
