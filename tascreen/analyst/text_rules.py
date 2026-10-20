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


# Style, not safety (hebrew-copy-editor, 2026-09-30): a clumsy text still goes out, so these
# are warnings to log, never a reason to drop a news item.
ANGLICISMS = {"הוקישי", "הוקישית", "הוקישיים", "הוקישיות", "דובי", "דובית", "דוביים", "דוביות",
              "פרימרקט", "מרקט", "ראלי", "הדבר", "מהווה", "מהווים", "מהוות"}
# News-desk style (hebrew-copy-editor, 2026-10-20, from a week of the channel's lines):
# filler, pointers back at earlier messages, and phrases translated word for word.
NEWS_SLIPS = ("לפי הציוץ", "לפי הפוסט", "שדווח קודם", "שדווחה קודם", "שדווחו קודם", "שנשלח קודם",
              "מכה בציפיות", "מכה את הציפיות", "מכות בציפיות", "מכות את הציפיות",
              "כשהסוחרים שוקלים", "כשהמשקיעים שוקלים", "פתיחות הבנייה",
              "מגיע אחרי", "מגיעה אחרי", "מגיעים אחרי", "מגיעות אחרי")
LATIN_OPENING = re.compile(r"[\s\"'(]*[A-Za-z]")
TICKER_TAIL = re.compile(r"[;,]\s*(?:ל-)?[A-Z]{1,5}(?:(?:,\s*|\s+ו-)[A-Z]{1,5})*\.?\s*$")
LOOSE_MINUS = re.compile(r"(?<![\w-])-\d|\d%?-(?=[\s,.;)]|$)")


def style_warnings(text: str) -> list[str]:
    """Hebrew style slips a prompt asks the model to avoid: "Fed" in Latin letters (the
    channel writes הפד), a lowercase English word inside the Hebrew (tickers and company
    names are capitalised, "swaps" is not), anglicisms or translated phrasing, and the
    news-desk slips: a sentence that opens with Latin letters (Telegram then sets the whole
    line left to right), a semicolon, tickers stuck on the end ("; ZQX."), a minus sign
    beside a number (it lands on the wrong side in right-to-left text), and the phrases in
    NEWS_SLIPS. Check one text at a time: the opening and the ending are the text's own."""
    out = []
    for word in re.findall(r"[A-Za-z]+", text):
        if word.lower() == "fed":
            out.append(f"'{word}' in Latin letters (write הפד)")
        elif len(word) > 1 and word.islower():
            out.append(f"English word '{word}'")
    for token in re.findall(r"[א-ת]+", text):
        forms, word = {token}, token
        for _ in range(2):
            if len(word) > 3 and word[0] in PREFIXES:
                word = word[1:]
                forms.add(word)
        if forms & ANGLICISMS:
            out.append(f"anglicism or translated phrasing '{token}'")
    if LATIN_OPENING.match(text):
        out.append("opens with Latin letters (put a Hebrew word first)")
    if ";" in text:
        out.append("semicolon (two sentences, or one joined by ו-)")
    if TICKER_TAIL.search(text):
        out.append("tickers stuck on the end (put each in brackets after its company)")
    if LOOSE_MINUS.search(text):
        out.append("minus sign beside a number (write ירידה של / מינוס)")
    out += [f"news-style slip '{p}'" for p in NEWS_SLIPS if p in text]
    return out
