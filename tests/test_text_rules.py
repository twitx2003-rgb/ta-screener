"""Style warnings on model-written Hebrew (invented companies and numbers only)."""
from __future__ import annotations

from tascreen.analyst.text_rules import banned, style_warnings


def test_clean_hebrew_with_tickers_and_company_names_passes():
    assert style_warnings("מניית ZZZQ של Acmetron זינקה 7% אחרי שהחברה העלתה את התחזית") == []
    assert style_warnings("הפד השאיר את הריבית ללא שינוי, והשוק מתמחר הורדה בדצמבר") == []


def test_fed_in_latin_letters_is_flagged_with_or_without_a_prefix():
    assert style_warnings("נאום של יו\"ר ה-Fed") == ["'Fed' in Latin letters (write הפד)"]
    assert style_warnings("FED מאותת") == ["'FED' in Latin letters (write הפד)"]


def test_a_lowercase_english_word_inside_hebrew_is_flagged():
    assert style_warnings("ה-swaps מתמחרים שתי הורדות") == ["English word 'swaps'"]
    assert style_warnings("Zentrica הרחיבה את מסגרות ה-revolver") == ["English word 'revolver'"]


def test_anglicisms_are_flagged_even_with_prefix_letters():
    found = style_warnings("טון הוקישי בפרימרקט, והראלי נמשך; הדבר מהווה סיכון")
    assert found == [f"anglicism or translated phrasing '{w}'"
                     for w in ("הוקישי", "בפרימרקט", "והראלי", "הדבר", "מהווה")]
    assert style_warnings("המדד בישראל ובדבר הזה") == []


def test_style_is_separate_from_the_advice_check():
    text = "ה-swaps מתמחרים ראלי"
    assert style_warnings(text) and banned(text) == []
