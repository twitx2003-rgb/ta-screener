"""Hebrew wording for values the data carries in English.

Anything without an entry falls back to the original text, so a new sector or a
new parameter shows up in English rather than disappearing.
"""
from __future__ import annotations

import re

# TradingView's sector classification (the `sector` column of the screener).
SECTORS = {
    "Commercial Services": "שירותים עסקיים",
    "Communications": "תקשורת",
    "Consumer Durables": "מוצרי צריכה בני קיימא",
    "Consumer Non-Durables": "מוצרי צריכה שוטפת",
    "Consumer Services": "שירותי צרכנות",
    "Distribution Services": "הפצה",
    "Electronic Technology": "טכנולוגיה אלקטרונית",
    "Energy Minerals": "אנרגיה",
    "Finance": "פיננסים",
    "Government": "ממשלתי",
    "Health Services": "שירותי בריאות",
    "Health Technology": "טכנולוגיה רפואית",
    "Industrial Services": "שירותים תעשייתיים",
    "Miscellaneous": "שונות",
    "Non-Energy Minerals": "מתכות וכרייה",
    "Process Industries": "תעשיות תהליך",
    "Producer Manufacturing": "ייצור תעשייתי",
    "Retail Trade": "קמעונאות",
    "Technology Services": "שירותי טכנולוגיה",
    "Transportation": "תחבורה",
    "Utilities": "תשתיות",
}

# TradingView's industry classification (the `industry` column), for the weekly money-flow map
# (tascreen/flow.py, owner 2026-10-28).
INDUSTRIES = {
    "Advertising/Marketing Services": "פרסום ושיווק",
    "Aerospace & Defense": "תעופה וביטחון",
    "Agricultural Commodities/Milling": "סחורות חקלאיות",
    "Air Freight/Couriers": "שילוח אווירי ושליחויות",
    "Airlines": "חברות תעופה",
    "Alternative Power Generation": "אנרגיה חלופית",
    "Aluminum": "אלומיניום",
    "Apparel/Footwear": "ביגוד והנעלה",
    "Apparel/Footwear Retail": "רשתות ביגוד והנעלה",
    "Auto Parts: OEM": "חלקי רכב",
    "Beverages: Alcoholic": "משקאות אלכוהוליים",
    "Beverages: Non-Alcoholic": "משקאות קלים",
    "Biotechnology": "ביוטכנולוגיה",
    "Broadcasting": "שידור",
    "Building Products": "מוצרי בנייה",
    "Cable/Satellite TV": "טלוויזיה בכבלים ולוויין",
    "Casinos/Gaming": "קזינו והימורים",
    "Chemicals: Agricultural": "כימיקלים לחקלאות",
    "Chemicals: Major Diversified": "כימיקלים מגוונים",
    "Chemicals: Specialty": "כימיקלים מיוחדים",
    "Commercial Printing/Forms": "דפוס מסחרי",
    "Computer Communications": "תקשורת מחשבים",
    "Computer Peripherals": "ציוד היקפי למחשב",
    "Computer Processing Hardware": "חומרת מחשבים ושרתים",
    "Construction Materials": "חומרי בנייה",
    "Containers/Packaging": "אריזות",
    "Contract Drilling": "קידוח בקבלנות",
    "Data Processing Services": "עיבוד נתונים",
    "Department Stores": "כלבו",
    "Discount Stores": "רשתות הנחות",
    "Drugstore Chains": "רשתות פארם",
    "Electric Utilities": "חברות חשמל",
    "Electrical Products": "מוצרי חשמל",
    "Electronic Components": "רכיבים אלקטרוניים",
    "Electronic Equipment/Instruments": "ציוד ומכשור אלקטרוני",
    "Electronic Production Equipment": "ציוד לייצור שבבים",
    "Electronics Distributors": "הפצת אלקטרוניקה",
    "Electronics/Appliance Stores": "רשתות אלקטרוניקה",
    "Electronics/Appliances": "מוצרי חשמל לבית",
    "Engineering & Construction": "הנדסה ובנייה",
    "Environmental Services": "שירותי סביבה",
    "Finance/Rental/Leasing": "מימון והשכרה",
    "Financial Conglomerates": "תאגידים פיננסיים",
    "Financial Publishing/Services": "שירותי מידע פיננסי",
    "Food Distributors": "הפצת מזון",
    "Food Retail": "רשתות מזון",
    "Food: Major Diversified": "מזון מגוון",
    "Food: Meat/Fish/Dairy": "בשר, דגים וחלב",
    "Food: Specialty/Candy": "מזון מיוחד וממתקים",
    "Forest Products": "מוצרי עץ",
    "Gas Distributors": "חלוקת גז",
    "Home Furnishings": "ריהוט לבית",
    "Home Improvement Chains": "רשתות שיפוץ לבית",
    "Homebuilding": "בניית בתים",
    "Hospital/Nursing Management": "בתי חולים וסיעוד",
    "Hotels/Resorts/Cruise lines": "מלונות, נופש ושייט",
    "Household/Personal Care": "מוצרי בית וטיפוח",
    "Industrial Conglomerates": "תאגידים תעשייתיים",
    "Industrial Machinery": "מכונות תעשייה",
    "Industrial Specialties": "מוצרי תעשייה מיוחדים",
    "Information Technology Services": "שירותי מחשוב",
    "Insurance Brokers/Services": "סוכנויות ביטוח",
    "Integrated Oil": "נפט משולב",
    "Internet Retail": "מסחר מקוון",
    "Internet Software/Services": "שירותי אינטרנט",
    "Investment Banks/Brokers": "בנקי השקעות וברוקרים",
    "Investment Managers": "מנהלי השקעות",
    "Investment Trusts/Mutual Funds": "קרנות השקעה",
    "Life/Health Insurance": "ביטוח חיים ובריאות",
    "Major Banks": "בנקים גדולים",
    "Major Telecommunications": "תקשורת גדולות",
    "Managed Health Care": "ביטוח בריאות מנוהל",
    "Marine Shipping": "ספנות",
    "Medical Distributors": "הפצה רפואית",
    "Medical Specialties": "מכשור רפואי",
    "Medical/Nursing Services": "שירותי רפואה וסיעוד",
    "Metal Fabrication": "עיבוד מתכת",
    "Miscellaneous": "שונות",
    "Miscellaneous Commercial Services": "שירותים עסקיים שונים",
    "Miscellaneous Manufacturing": "ייצור שונה",
    "Motor Vehicles": "רכב",
    "Movies/Entertainment": "קולנוע ובידור",
    "Multi-Line Insurance": "ביטוח כללי",
    "Office Equipment/Supplies": "ציוד משרדי",
    "Oil & Gas Pipelines": "צינורות נפט וגז",
    "Oil & Gas Production": "הפקת נפט וגז",
    "Oil Refining/Marketing": "זיקוק ושיווק נפט",
    "Oilfield Services/Equipment": "שירותים לשדות נפט",
    "Other Consumer Services": "שירותי צרכנות אחרים",
    "Other Metals/Minerals": "מתכות ומחצבים אחרים",
    "Other Transportation": "תחבורה אחרת",
    "Packaged Software": "תוכנה",
    "Personnel Services": "השמה וכוח אדם",
    "Pharmaceuticals: Generic": "תרופות גנריות",
    "Pharmaceuticals: Major": "תרופות - חברות גדולות",
    "Pharmaceuticals: Other": "תרופות אחרות",
    "Precious Metals": "מתכות יקרות",
    "Property/Casualty Insurance": "ביטוח רכוש",
    "Publishing: Books/Magazines": "הוצאה לאור",
    "Publishing: Newspapers": "עיתונות",
    "Pulp & Paper": "נייר ותאית",
    "Railroads": "רכבות",
    "Real Estate Development": "ייזום נדל\"ן",
    "Real Estate Investment Trusts": "קרנות נדל\"ן",
    "Recreational Products": "מוצרי פנאי",
    "Regional Banks": "בנקים אזוריים",
    "Restaurants": "מסעדות",
    "Semiconductors": "מוליכים למחצה",
    "Services to the Health Industry": "שירותים לתעשיית הבריאות",
    "Specialty Insurance": "ביטוח מיוחד",
    "Specialty Stores": "חנויות מתמחות",
    "Specialty Telecommunications": "תקשורת מתמחה",
    "Steel": "פלדה",
    "Telecommunications Equipment": "ציוד תקשורת",
    "Tobacco": "טבק",
    "Tools & Hardware": "כלי עבודה",
    "Trucking": "הובלה במשאיות",
    "Trucks/Construction/Farm Machinery": "משאיות וכלים כבדים",
    "Water Utilities": "חברות מים",
    "Wholesale Distributors": "סיטונאות",
    "Wireless Telecommunications": "תקשורת סלולרית",
}

STATUS = {"forming": "בבנייה", "breakout": "פריצה", "busted": "פריצה כושלת", "signal": "אות נר"}
STATUS_HELP = {
    "forming": "התבנית עדיין נבנית: אין סגירה מחוץ לה",
    "breakout": "סגירה מחוץ לתבנית בכיוון הפריצה",
    "busted": "הייתה פריצה והמחיר חזר וחצה את הצד השני",
    "signal": "תבנית נרות שהסתיימה בימי המסחר האחרונים",
}
DIRECTION = {"bullish": "שורי", "bearish": "דובי", "either": "כיוון לא ידוע עדיין"}
FAMILY = {"chart": "תבנית גרף", "candle": "תבנית נרות"}
ORIGIN = {"site": "Bulkowski", "ours": "בחירה שלנו", "": "מבנה התבנית"}
ORIGIN_HELP = {
    "site": "המספר מופיע בדף של התבנית באתר של Bulkowski",
    "ours": "Bulkowski מתאר את הכלל במילים; המספר הוא בחירה שלנו",
    "": "כלל מבני של התבנית, בלי סף מספרי",
}
VOLUME_TREND = {"down": "יורד", "up": "עולה", "": "—"}
OUTCOME = {"target": "הגיע ליעד", "failed": "נכשל", "expired": "ללא הכרעה", "open": "עדיין פתוח",
           "no_data": "אין נתונים"}
OUTCOME_CLASS = {"target": "up", "failed": "down", "expired": "", "open": "accent", "no_data": ""}
SOURCE = {"live": "מהסריקות היומיות", "backfill": "מילוי לאחור (רטרואקטיבי)"}

PARAMS = {
    # general
    "atr_period": "אורך ה-ATR",
    "pivot_atr_multiple": "תנודה מינימלית לנקודת מפנה (ביחידות ATR)",
    "trend_lookback_sessions": "טווח המגמה שלפני התבנית (ימי מסחר)",
    "lookback_sessions": "כמה אחורה מחפשים תבניות (ימי מסחר)",
    "recent_breakout_sessions": "כמה זמן מוצגת פריצה (ימי מסחר)",
    "forming_max_age_sessions": "תבנית בבנייה מוצגת עד כמה ימים מנקודת המפנה האחרונה",
    "touch_tolerance_atr": "סבולת מגע בקו (ביחידות ATR)",
    "touch_tolerance_height": "סבולת מגע בקו (חלק מגובה התבנית)",
    "inside_tolerance_atr": "חריגה מותרת מהקווים בתוך התבנית (ATR)",
    "coverage_sessions": "חלון בדיקת שלמות הנתונים (ימי מסחר)",
    "min_recent_coverage": "שלמות נתונים מינימלית",
    "flat_line_max_drift": "סטייה מרבית של קו אופקי (חלק מהגובה)",
    "converge_max_end_width": "רוחב מרבי בסוף משולש או טריז (חלק מהרוחב בהתחלה)",
    # candles, general
    "avg_body_sessions": "גוף נר ממוצע: על פני כמה ימי מסחר",
    "tall_body_factor": "נר גבוה: גוף של לפחות פי כך מהממוצע",
    "small_body_factor": "נר קטן: גוף של עד פי כך מהממוצע",
    "doji_max_body_frac": "דוג'י: גוף של עד חלק זה מטווח היום",
    "long_shadow_body_multiple": "צל ארוך: לפחות פי כך מהגוף",
    "little_shadow_max_frac": "צל קטן: עד חלק זה מטווח היום",
    "near_extreme_frac": "סגירה ליד השיא או השפל: בתוך חלק זה מהטווח",
    "marubozu_max_shadow_frac": "מרובוזו: צל של עד חלק זה מהטווח",
    "trend_sessions": "מגמה לפני הנר: על פני כמה ימי מסחר",
    "recent_sessions": "נר מוצג עד כמה ימי מסחר אחרי שהסתיים",
    # chart patterns
    "max_bottom_diff_pct": "הפרש מרבי בין השפלים (%)",
    "max_top_diff_pct": "הפרש מרבי בין הפסגות (%)",
    "min_rise_between_pct": "עלייה מינימלית בין השפלים (%)",
    "min_decline_between_pct": "ירידה מינימלית בין הפסגות (%)",
    "min_separation_sessions": "מרחק מינימלי בין הנקודות (ימי מסחר)",
    "max_separation_sessions": "מרחק מרבי בין הנקודות (ימי מסחר)",
    "min_swing_pct": "תנודה מינימלית בין הנקודות (%)",
    "max_shoulder_diff_pct": "הפרש מרבי בין הכתפיים (%)",
    "min_head_excess_pct": "בכמה הראש בולט מעבר לכתפיים (%)",
    "max_time_asymmetry": "יחס מרבי בין מרחקי הכתפיים מהראש",
    "min_touches_major": "מגעים מינימליים בקו אחד",
    "min_touches_minor": "מגעים מינימליים בקו השני",
    "min_duration_sessions": "משך מינימלי (ימי מסחר)",
    "max_duration_sessions": "משך מרבי (ימי מסחר)",
    "max_width_change": "שינוי רוחב מרבי (קווים מקבילים)",
    "max_end_width_share": "רוחב מרבי בסוף (חלק מהרוחב בהתחלה)",
    "pole_max_sessions": "משך מרבי של התורן (ימי מסחר)",
    "pole_min_move_pct": "תנועה מינימלית של התורן (%)",
    "pole_min_atr_per_session": "תלילות מינימלית של התורן (ATR ליום)",
    "max_retrace_of_pole": "תיקון מרבי (חלק מהתורן)",
    "max_consolidation_sessions": "משך מרבי של ההתכנסות (ימי מסחר)",
    "min_rise_pct": "עלייה מינימלית (%)",
    "max_rise_sessions": "משך מרבי של העלייה (ימי מסחר)",
    "max_retrace_pct": "תיקון מרבי (%)",
    "min_cup_sessions": "משך מינימלי של הספל (ימי מסחר)",
    "max_cup_sessions": "משך מרבי של הספל (ימי מסחר)",
    "min_depth_pct": "עומק מינימלי (%)",
    "max_depth_pct": "עומק מרבי (%)",
    "max_lip_diff_pct": "הפרש מרבי בין שפות הספל (%)",
    "min_bottom_share": "חלק מינימלי של הזמן בשליש התחתון (צורת U)",
    "min_handle_sessions": "משך מינימלי של הידית (ימי מסחר)",
}

_WORDS = {"all": "כולן", "outside a line": "מחוץ לאחד הקווים", "white": "לבן", "black": "שחור",
          "info": "לידיעה"}
_TIMES_BODY = re.compile(r"^(>=|<=|>|<) ?([\d.]+)x body$")


def sector(name) -> str:
    return SECTORS.get(name, name) if isinstance(name, str) and name else "—"


def check_text(value) -> str:
    """A check's value or threshold, with the few English words in Hebrew.

    Numeric expressions ('>= 3', '102.7..105', 'open < 88.06, close > 88.08') are
    shown left to right; only the words open/close are translated."""
    if value is None:
        return "טרם"
    if isinstance(value, bool):
        return "כן" if value else "לא"
    if not isinstance(value, str):
        return str(value)
    if value in _WORDS:
        return _WORDS[value]
    found = _TIMES_BODY.match(value)
    if found:
        op, n = found.groups()
        return f"פי {n} מהגוף {'לפחות' if op.startswith('>') else 'לכל היותר'}"
    return re.sub(r"\bopen\b", "פתיחה", re.sub(r"\bclose\b", "סגירה", value))
