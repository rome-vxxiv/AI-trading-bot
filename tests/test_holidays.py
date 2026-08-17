"""Exchange holiday calendar loading + lookups."""

from datetime import date

from capital_agent.sessions.holidays import HolidayCalendar, load_holidays


def test_missing_file_returns_empty_calendar(tmp_path):
    cal = load_holidays(tmp_path / "nope.yaml")
    assert cal.markets == {}
    assert cal.is_holiday("US", date(2026, 1, 1)) is False


def test_load_holidays_parses_dates(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text(
        "year: 2026\n"
        "US:\n"
        "  - 2026-01-01\n"
        "  - 2026-12-25\n"
        "UK:\n"
        "  - 2026-01-01\n",
        encoding="utf-8",
    )
    cal = load_holidays(p)
    assert cal.is_holiday("US", date(2026, 1, 1)) is True
    assert cal.is_holiday("US", date(2026, 12, 25)) is True
    assert cal.is_holiday("US", date(2026, 6, 1)) is False
    assert cal.is_holiday("UK", date(2026, 1, 1)) is True
    assert cal.is_holiday("DE", date(2026, 1, 1)) is False  # market not in file


def test_is_holiday_false_for_none_market():
    cal = HolidayCalendar(markets={"US": {date(2026, 1, 1)}})
    assert cal.is_holiday(None, date(2026, 1, 1)) is False


def test_load_holidays_skips_bad_date_strings(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text(
        "US:\n"
        "  - 2026-01-01\n"
        "  - 'not-a-date'\n",
        encoding="utf-8",
    )
    cal = load_holidays(p)
    assert cal.markets["US"] == {date(2026, 1, 1)}


def test_load_holidays_ignores_year_key(tmp_path):
    p = tmp_path / "holidays.yaml"
    p.write_text("year: 2026\nUS:\n  - 2026-01-01\n", encoding="utf-8")
    cal = load_holidays(p)
    assert "year" not in cal.markets
    assert list(cal.markets.keys()) == ["US"]
