"""명부 날짜 문자열 정규화.

원본 종전 규칙(``모듈``/``모듈``)는 생년월일·입사일·중간정산일·전입일·추가지급
기준일·퇴사일·사외자산 지급일마다 동일한 ``Select Case Len(datetr)`` 블록을
복사해 두었다(모듈당 4~5회, 총 900줄 이상). 이 모듈은 그 판정 규칙을 한 곳으로
모은 것이다.

핵심 규칙은 두 가지다.

1. **길이 기반 판정**  문자열 길이(3~11)와 특정 위치의 문자가 숫자인지 여부로
   형식을 결정한다. 구분자가 ``.``/``-``/``/`` 중 무엇인지는 보지 않으며,
   끝의 ``일`` 접미사도 "숫자가 아닌 문자" 로만 취급한다.
2. **두 자리 연도 피벗**  ``yy`` 두 자리만 주어지면 산출기준일 연도의 뒤 두 자리
   (``kyy``)와 비교해 ``yy <= kyy`` 이면 ``2000+yy``, 아니면 ``1900+yy`` 로 본다.
   즉 기준일이 2025-12-31이면 ``25`` 는 2025년, ``26`` 은 1926년이다.

원본 대비 수정한 점은 :func:`parse_roster_date` 의 docstring 과
``docs/vba-mapping.md`` 에 정리했다.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Final

from .errors import DateParseError

__all__ = [
    "EXCEL_EPOCH",
    "parse_roster_date",
    "pivot_two_digit_year",
    "to_date",
]

EXCEL_EPOCH: Final = _dt.date(1899, 12, 30)
"""엑셀 1900 날짜 체계의 기준일. 1900년 윤년 버그를 포함한 오프셋."""

_DIGITS: Final = re.compile(r"^\d+$")

# 명부에 종종 섞여 들어오는 잡문자. 길이 판정 전에 제거한다.
_STRIP_CHARS: Final = " \t 　'\"()[]"


def _is_digit(text: str, pos: int) -> bool:
    """종전 규칙 ``ja(pos) Like "[0-9]"`` 와 같은 판정(pos 는 1-based)."""
    return 1 <= pos <= len(text) and text[pos - 1].isascii() and text[pos - 1].isdigit()


def pivot_two_digit_year(two_digit: int, base_year: int) -> int:
    """두 자리 연도를 네 자리로 확장한다.

    종전 규칙::

        cyy = Left(datetr, 2) + 1 - 1
        If cyy <= kyy Then yy1 = 2000 + cyy Else yy1 = 1900 + cyy

    ``kyy`` 는 산출기준일 연도의 뒤 두 자리다.
    """
    kyy = base_year % 100
    return 2000 + two_digit if two_digit <= kyy else 1900 + two_digit


def _ymd(year: int, month: int, day: int, raw: str) -> _dt.date:
    try:
        return _dt.date(year, month, day)
    except ValueError as exc:  # 2월 30일 등
        raise DateParseError(raw, f"{year}-{month}-{day} 는 존재하지 않는 날짜입니다") from exc


def _num(text: str, start: int, length: int, raw: str) -> int:
    """종전 규칙 ``Mid(text, start, length)`` 를 정수로. 숫자가 아니면 오류."""
    chunk = text[start - 1 : start - 1 + length]
    if not _DIGITS.match(chunk):
        raise DateParseError(raw, f"{start}번째부터 {length}자리가 숫자가 아닙니다({chunk!r})")
    return int(chunk)


def parse_roster_date(value: object, base_year: int) -> _dt.date | None:
    """명부 셀 값 하나를 날짜로 변환한다.

    :param value: 셀 값. ``datetime``/``date``, 엑셀 일련번호(숫자), 문자열을 받는다.
    :param base_year: 산출기준일의 연도. 두 자리 연도 피벗에 쓴다.
    :returns: 해석된 날짜. 빈 값이면 ``None``.
    :raises DateParseError: 어떤 형식으로도 해석할 수 없을 때.

    지원 형식은 원본 종전 규칙 주석에 열거된 것과 같다(``mdd``, ``mmdd``, ``ymmdd``,
    ``yymmdd``, ``yy.m.d``, ``yyyy.mm.dd``, ``yyyy.mm.dd일`` 등 길이 3~11).

    원본과 달라지는 부분이 하나 있다. 길이 8의 ``yy.mm.d일`` 형식에서 종전 규칙 는
    생년월일·중간정산일·전입일·추가지급기준일 블록이 일자를 ``Mid(s, 6, 1)``
    (구분자 ``.``)에서 읽어 항상 실패하고, 입사일 블록만 ``Mid(s, 7, 1)`` 로 옳게
    읽는다. 여기서는 올바른 ``7`` 번째 위치로 통일했다.
    """
    if value is None:
        return None

    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value

    if isinstance(value, bool):  # bool 은 int 의 하위형이라 먼저 걸러낸다.
        raise DateParseError(str(value), "논리값은 날짜가 될 수 없습니다")

    if isinstance(value, (int, float)):
        # 종전 규칙 는 셀 값을 String 변수(datetr)로 받으므로 숫자 셀은 자릿수 기반
        # 판정을 탄다(19801231 → yyyymmdd, 801231 → yymmdd). 날짜 서식이 잡힌
        # 셀은 openpyxl 이 이미 datetime 으로 넘겨주므로 위에서 처리된다.
        if value != int(value):
            raise DateParseError(str(value), "소수는 날짜가 될 수 없습니다")
        value = str(int(value))

    raw = str(value)
    text = raw.strip(_STRIP_CHARS)
    if not text:
        return None

    return _parse_text(text, base_year, raw)


def _parse_text(s: str, base_year: int, raw: str) -> _dt.date:
    """길이 기반 형식 판정. 종전 규칙 ``Select Case leng`` 의 이식."""
    leng = len(s)

    if leng == 3:  # mdd (2000년 기준)
        return _ymd(2000, _num(s, 1, 1, raw), _num(s, 2, 2, raw), raw)

    if leng == 4:  # mmdd (2000년 기준)
        return _ymd(2000, _num(s, 1, 2, raw), _num(s, 3, 2, raw), raw)

    if leng == 5:  # ymmdd
        return _ymd(2000 + _num(s, 1, 1, raw), _num(s, 2, 2, raw), _num(s, 4, 2, raw), raw)

    if leng == 6:
        if _is_digit(s, 2):
            if _is_digit(s, 3):  # yymmdd
                year = pivot_two_digit_year(_num(s, 1, 2, raw), base_year)
                return _ymd(year, _num(s, 3, 2, raw), _num(s, 5, 2, raw), raw)
            # yy.m.d
            year = pivot_two_digit_year(_num(s, 1, 2, raw), base_year)
            return _ymd(year, _num(s, 4, 1, raw), _num(s, 6, 1, raw), raw)
        if _is_digit(s, 4):  # y.mm.d
            return _ymd(2000 + _num(s, 1, 1, raw), _num(s, 3, 2, raw), _num(s, 6, 1, raw), raw)
        # y.m.dd
        return _ymd(2000 + _num(s, 1, 1, raw), _num(s, 3, 1, raw), _num(s, 5, 2, raw), raw)

    if leng == 7:
        if _is_digit(s, 2):
            year = pivot_two_digit_year(_num(s, 1, 2, raw), base_year)
            if _is_digit(s, 5):  # yy.mm.d
                return _ymd(year, _num(s, 4, 2, raw), _num(s, 7, 1, raw), raw)
            if _is_digit(s, 7):  # yy.m.dd
                return _ymd(year, _num(s, 4, 1, raw), _num(s, 6, 2, raw), raw)
            # yy.m.d일
            return _ymd(year, _num(s, 4, 1, raw), _num(s, 6, 1, raw), raw)
        if _is_digit(s, 4):  # y.mm.d일
            return _ymd(2000 + _num(s, 1, 1, raw), _num(s, 3, 2, raw), _num(s, 6, 1, raw), raw)
        # y.m.dd일
        return _ymd(2000 + _num(s, 1, 1, raw), _num(s, 3, 1, raw), _num(s, 5, 2, raw), raw)

    if leng == 8:
        if _is_digit(s, 3):  # yyyy 로 시작
            if _is_digit(s, 4) and _is_digit(s, 5):  # yyyymmdd
                return _ymd(_num(s, 1, 4, raw), _num(s, 5, 2, raw), _num(s, 7, 2, raw), raw)
            # yyyy.m.d
            return _ymd(_num(s, 1, 4, raw), _num(s, 6, 1, raw), _num(s, 8, 1, raw), raw)
        year = pivot_two_digit_year(_num(s, 1, 2, raw), base_year)
        if _is_digit(s, 5):
            if _is_digit(s, 8):  # yy.mm.dd
                return _ymd(year, _num(s, 4, 2, raw), _num(s, 7, 2, raw), raw)
            # yy.mm.d일 — 종전 규칙 는 여기서 Mid(s, 6, 1)(구분자)을 읽는 버그가 있다.
            return _ymd(year, _num(s, 4, 2, raw), _num(s, 7, 1, raw), raw)
        # yy.m.dd일
        return _ymd(year, _num(s, 4, 1, raw), _num(s, 6, 2, raw), raw)

    if leng == 9:
        if _is_digit(s, 5):
            if _is_digit(s, 7):  # yy.mm.dd일
                year = pivot_two_digit_year(_num(s, 1, 2, raw), base_year)
                return _ymd(year, _num(s, 4, 2, raw), _num(s, 7, 2, raw), raw)
            # yyyymm-dd
            return _ymd(_num(s, 1, 4, raw), _num(s, 5, 2, raw), _num(s, 8, 2, raw), raw)
        if _is_digit(s, 7):
            year = _num(s, 1, 4, raw)
            month = _num(s, 6, 2, raw)
            if _is_digit(s, 8):  # yyyy-mmdd
                return _ymd(year, month, _num(s, 8, 2, raw), raw)
            # yyyy.mm.d
            return _ymd(year, month, _num(s, 9, 1, raw), raw)
        # yyyy.m.dd
        return _ymd(_num(s, 1, 4, raw), _num(s, 6, 1, raw), _num(s, 8, 2, raw), raw)

    if leng == 10:
        if _is_digit(s, 7):
            if _is_digit(s, 10):  # yyyy.mm.dd
                return _ymd(_num(s, 1, 4, raw), _num(s, 6, 2, raw), _num(s, 9, 2, raw), raw)
            # yyyy.mm.d일
            return _ymd(_num(s, 1, 4, raw), _num(s, 6, 2, raw), _num(s, 9, 1, raw), raw)
        # yyyy.m.dd일
        return _ymd(_num(s, 1, 4, raw), _num(s, 6, 1, raw), _num(s, 8, 2, raw), raw)

    if leng == 11:  # yyyy.mm.dd일
        if _is_digit(s, 5) or _is_digit(s, 8) or _is_digit(s, 11):
            raise DateParseError(raw, "11자리 형식은 yyyy.mm.dd일 만 지원합니다")
        return _ymd(_num(s, 1, 4, raw), _num(s, 6, 2, raw), _num(s, 9, 2, raw), raw)

    raise DateParseError(raw, f"지원하지 않는 길이({leng}자리)입니다")


def to_date(value: object) -> _dt.date | None:
    """설정용 셀(산출기준일 등)의 날짜 변환.

    명부 셀과 달리 이미 날짜 서식이 잡혀 있는 것이 정상이므로 길이 기반 추론을
    쓰지 않는다. 문자열이면 ``YYYY-MM-DD`` 계열만 받는다.
    """
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return EXCEL_EPOCH + _dt.timedelta(days=int(value))
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise DateParseError(text, "YYYY-MM-DD 형식이 아닙니다")
