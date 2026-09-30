"""Counts agree with their nouns in the bot's fixed Hebrew messages."""
from __future__ import annotations

from tascreen.hebrew import count_he


def test_zero_one_two_and_many():
    assert count_he(0, "מניה אחת", "מניות") == "0 מניות"
    assert count_he(1, "מניה אחת", "מניות") == "מניה אחת"
    assert count_he(2, "מניה אחת", "מניות") == "2 מניות"
    assert count_he(37, "מניה אחת", "מניות") == "37 מניות"


def test_a_dual_form_and_thousands():
    assert count_he(1, "שעה", "שעות", "שעתיים") == "שעה"
    assert count_he(2, "שעה", "שעות", "שעתיים") == "שעתיים"
    assert count_he(5, "שעה", "שעות", "שעתיים") == "5 שעות"
    assert count_he(1234, "מניה אחת", "מניות", sep=True) == "1,234 מניות"
    assert count_he(1234, "מניה אחת", "מניות") == "1234 מניות"


def test_floats_and_verbs():
    assert count_he(1.0, "דקה", "דקות") == "דקה"
    assert count_he(7.5, "דקה", "דקות") == "7.5 דקות"
    assert count_he(10.0, "דקה", "דקות") == "10 דקות"
    assert count_he(1, "אחת נכשלה", "נכשלו") == "אחת נכשלה"
    assert count_he(3, "אחת נכשלה", "נכשלו") == "3 נכשלו"


def test_the_messages_use_it():
    from tascreen.alerts import news_line, watch_started_message
    from tascreen.config import AlertsSettings
    from tascreen.notify import run_message

    headline = {"title": "A made-up story", "provider": "", "link": "https://example.com/x"}
    assert "(לפני שעה)" in news_line({**headline, "hours": 1.2})
    assert "(לפני שעתיים)" in news_line({**headline, "hours": 2.3})
    assert "(לפני 9 שעות)" in news_line({**headline, "hours": 9})
    started = watch_started_message(1, 0, AlertsSettings(live_interval_minutes=5.0, far_every=3))
    assert "מניה אחת במעקב: אחת עד" in started and "(כל 5 דקות)" in started and "0 רחוקות יותר (כל 15 דקות)" in started
    partial = {"session": "2026-01-02", "due": True, "complete": False, "bars": {"deferred": 1, "failed": 0}}
    text = run_message(partial, "success")
    assert "חסרה מניה אחת" in text and "הריצה הבאה" in text
