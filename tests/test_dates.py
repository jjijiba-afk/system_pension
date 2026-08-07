"""날짜 파싱. 종전 규칙 ``Select Case Len(datetr)`` 이식 결과를 형식별로 검증한다."""

from __future__ import annotations

import datetime as dt

import pytest

from pension.dates import DateParseError, parse_roster_date, pivot_two_digit_year, to_date

BASE_YEAR = 2025


def parse(value: object) -> dt.date | None:
    return parse_roster_date(value, BASE_YEAR)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 길이 3~5 — 연도가 없거나 한 자리
        ("312", dt.date(2000, 3, 12)),
        ("1231", dt.date(2000, 12, 31)),
        ("51231", dt.date(2005, 12, 31)),
        # 길이 6
        ("801231", dt.date(1980, 12, 31)),   # yymmdd, 80 > 25 이므로 1980
        ("051231", dt.date(2005, 12, 31)),   # 05 <= 25 이므로 2005
        ("80.1.3", dt.date(1980, 1, 3)),     # yy.m.d
        ("5.12.3", dt.date(2005, 12, 3)),    # y.mm.d
        ("5.1.31", dt.date(2005, 1, 31)),    # y.m.dd
        # 길이 7
        ("80.12.3", dt.date(1980, 12, 3)),   # yy.mm.d
        ("80.1.31", dt.date(1980, 1, 31)),   # yy.m.dd
        ("80.1.3일", dt.date(1980, 1, 3)),   # yy.m.d일
        ("5.12.3일", dt.date(2005, 12, 3)),  # y.mm.d일
        ("5.1.31일", dt.date(2005, 1, 31)),  # y.m.dd일
        # 길이 8
        ("19801231", dt.date(1980, 12, 31)),  # yyyymmdd
        ("1980.1.3", dt.date(1980, 1, 3)),    # yyyy.m.d
        ("80.12.31", dt.date(1980, 12, 31)),  # yy.mm.dd
        ("80.12.3일", dt.date(1980, 12, 3)),  # yy.mm.d일
        ("80.1.31일", dt.date(1980, 1, 31)),  # yy.m.dd일
        # 길이 9
        ("80.12.31일", dt.date(1980, 12, 31)),
        ("198012-31", dt.date(1980, 12, 31)),
        ("1980-1231", dt.date(1980, 12, 31)),
        ("1980.12.3", dt.date(1980, 12, 3)),
        ("1980.1.31", dt.date(1980, 1, 31)),
        # 길이 10~11
        ("1980-12-31", dt.date(1980, 12, 31)),
        ("1980.12.3일", dt.date(1980, 12, 3)),
        ("1980.1.31일", dt.date(1980, 1, 31)),
        ("1980.12.31일", dt.date(1980, 12, 31)),
    ],
)
def test_supported_formats(raw: str, expected: dt.date) -> None:
    assert parse(raw) == expected


def test_two_digit_year_pivots_on_base_year() -> None:
    """기준연도 뒤 두 자리 이하면 2000년대, 넘으면 1900년대."""
    assert pivot_two_digit_year(25, 2025) == 2025
    assert pivot_two_digit_year(26, 2025) == 1926
    assert pivot_two_digit_year(0, 2025) == 2000
    # 기준일이 바뀌면 경계도 따라 움직인다.
    assert pivot_two_digit_year(26, 2026) == 2026


def test_yy_mm_d_with_suffix_reads_the_day_not_the_separator() -> None:
    """종전 규칙 는 이 형식에서 구분자를 일자로 읽는 버그가 있었다.

    '80.12.3일' 은 4번째부터 두 자리가 월(12), 7번째 한 자리가 일(3)이다.
    원본은 생년월일·중간정산일 블록에서 6번째('.')를 읽어 항상 실패했다.
    """
    assert parse("80.12.3일") == dt.date(1980, 12, 3)


def test_datetime_and_date_pass_through() -> None:
    assert parse(dt.datetime(1980, 12, 31, 9, 30)) == dt.date(1980, 12, 31)
    assert parse(dt.date(1980, 12, 31)) == dt.date(1980, 12, 31)


def test_numeric_cell_uses_digit_layout() -> None:
    """종전 규칙 는 셀 값을 문자열 변수로 받으므로 숫자도 자릿수로 판정한다."""
    assert parse(19801231) == dt.date(1980, 12, 31)
    assert parse(801231) == dt.date(1980, 12, 31)


def test_blank_values_are_none() -> None:
    assert parse(None) is None
    assert parse("") is None
    assert parse("   ") is None


@pytest.mark.parametrize("raw", ["abcdefgh", "1980-13-31", "1980-02-30", "1", "12"])
def test_unparseable_values_raise(raw: str) -> None:
    with pytest.raises(DateParseError):
        parse(raw)


def test_error_carries_the_original_value() -> None:
    with pytest.raises(DateParseError) as info:
        parse("1980-02-30")
    assert "1980-02-30" in str(info.value)


def test_config_dates_do_not_use_length_inference() -> None:
    """설정 셀은 이미 날짜 서식이므로 추론하지 않는다."""
    assert to_date("2025-12-31") == dt.date(2025, 12, 31)
    assert to_date(dt.datetime(2025, 12, 31)) == dt.date(2025, 12, 31)
    assert to_date(None) is None
    with pytest.raises(DateParseError):
        to_date("801231-ish")
