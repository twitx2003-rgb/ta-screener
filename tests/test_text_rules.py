"""Style warnings on model-written Hebrew (invented companies and numbers only)."""
from __future__ import annotations

from tascreen.analyst.text_rules import banned, style_warnings


def test_clean_hebrew_with_tickers_and_company_names_passes():
    assert style_warnings("מניית ZZZQ של Acmetron זינקה 7% אחרי שהחברה העלתה את התחזית") == []
    assert style_warnings("הפד השאיר את הריבית ללא שינוי, והשוק מתמחר הורדה בדצמבר") == []


def test_fed_in_latin_letters_is_flagged_with_or_without_a_prefix():
    assert style_warnings("נאום של יו\"ר ה-Fed") == ["'Fed' in Latin letters (write הפד)"]
    assert style_warnings("הנגיד ב-FED מאותת") == ["'FED' in Latin letters (write הפד)"]


def test_a_lowercase_english_word_inside_hebrew_is_flagged():
    assert style_warnings("ה-swaps מתמחרים שתי הורדות") == ["English word 'swaps'"]
    assert style_warnings("חברת Zentrica הרחיבה את מסגרות ה-revolver") == ["English word 'revolver'"]


def test_anglicisms_are_flagged_even_with_prefix_letters():
    found = style_warnings("טון הוקישי בפרימרקט, והראלי נמשך. הדבר מהווה סיכון")
    assert found == [f"anglicism or translated phrasing '{w}'"
                     for w in ("הוקישי", "בפרימרקט", "והראלי", "הדבר", "מהווה")]
    assert style_warnings("המדד בישראל ובדבר הזה") == []


def test_style_is_separate_from_the_advice_check():
    text = "ה-swaps מתמחרים ראלי"
    assert style_warnings(text) and banned(text) == []


def test_news_desk_slips_are_flagged():
    # invented lines, each with one slip seen in the channel (2026-10-20)
    assert style_warnings("Zentrica מעלה את התחזית") == ["opens with Latin letters (put a Hebrew word first)"]
    assert style_warnings("זנטריקה מעלה תחזית; המניה עולה") == ["semicolon (two sentences, or one joined by ו-)"]
    tail = ["tickers stuck on the end (put each in brackets after its company)"]
    assert style_warnings("זנטריקה מעלה את התחזית; ZQX.") == ["semicolon (two sentences, or one joined by ו-)"] + tail
    assert style_warnings("ירידה בענף התעופה לפני הדוחות של ZQX, QZV ו-VXQ.") == tail
    assert style_warnings("המכירות של זנטריקה השתנו ב-4.1%- ברבעון") == ["minus sign beside a number (write ירידה של / מינוס)"]
    assert style_warnings("לפי הציוץ, זנטריקה מכה בציפיות") == ["news-style slip 'לפי הציוץ'",
                                                              "news-style slip 'מכה בציפיות'"]


def test_good_news_lines_pass_the_news_desk_checks():
    for line in ("זנטריקה (ZQX) מעלה את התחזית השנתית ב-8%, ל-2.4 מיליארד דולר",
                 "מניית ZQX יורדת ב-3.5% בטרום המסחר, אחרי ירידה של 1.2% אתמול",
                 'עדכון: מדד S&P 500 מוסיף 0.4%, והטווח עומד על 3-4%',
                 'מנכ"לית זנטריקה: "הביקוש חזק מתמיד"'):
        assert style_warnings(line) == [], line
