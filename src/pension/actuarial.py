"""연령·근속·정년연령 계산.

VBA 가 명부 읽기 루프 안에 인라인으로 흩어 놓은 계산식을 함수로 분리했다.
"""

from __future__ import annotations

import datetime as _dt
import math

from .config import JobGroupRule

__all__ = [
    "FRACTION_MODES",
    "SERVICE_BASES",
    "attained_age",
    "longterm_retirement_age",
    "normal_retirement_age",
    "round_amount",
    "service_years",
]

# ── 근속기간 산정방법 ────────────────────────────────────────────
# 회사 규정마다 다르다. 실제 사례에서 확인한 것들:
#   · '근로기준법에 따른 산정법 - 일수'        → 일할
#   · '근속기간 1년 이상 (월할 계산)'          → 월할
#   · '1년이 되지 않는 단수개월은 절사'        → 연할
SERVICE_DAILY = "일할"
"""근속일수 ÷ 365. 근로자퇴직급여보장법의 법정 산식과 같다."""
SERVICE_MONTHLY = "월할"
"""근속개월수 ÷ 12."""
SERVICE_ANNUAL = "연할"
"""완성된 해만 센다(단수 개월 버림)."""

SERVICE_BASES = (SERVICE_DAILY, SERVICE_MONTHLY, SERVICE_ANNUAL)

DAYS_PER_YEAR = 365.0
"""일할 계산의 분모. 근로기준법 산식이 365 를 쓴다."""

# ── 단수 처리 ────────────────────────────────────────────────────
FRACTION_KEEP = "그대로"
FRACTION_DOWN = "절사"
FRACTION_UP = "절상"
FRACTION_HALF = "반올림"

FRACTION_MODES = (FRACTION_KEEP, FRACTION_DOWN, FRACTION_UP, FRACTION_HALF)


def attained_age(birth_date: _dt.date, as_of: _dt.date) -> int:
    """만 연령.

    VBA::

        If mm > mm2 Or (mm = mm2 And dd >= dd2) Then six = 0 Else six = 1
        age = yy - yy2 - six

    통상의 만 나이와 한 가지 다르다. 생일 **당일** 을 이미 지난 것으로 보므로
    (``dd >= dd2``) 생일 당일에 나이가 한 살 오른다. 이 시스템의 기준일은 보통
    12월 31일이고 생일이 12월 31일인 사람만 영향을 받지만, 원본 산출값과의
    일치를 위해 그대로 둔다.
    """
    birthday_passed = as_of.month > birth_date.month or (
        as_of.month == birth_date.month and as_of.day >= birth_date.day
    )
    return as_of.year - birth_date.year - (0 if birthday_passed else 1)


def _completed_years(start: _dt.date, end: _dt.date) -> int:
    """만 근속 햇수. 기념일이 아직 안 왔으면 한 해를 뺀다."""
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(0, years)


def _completed_months(start: _dt.date, end: _dt.date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def apply_fraction(value: float, mode: str) -> float:
    """근속연수의 단수 처리."""
    if mode == FRACTION_DOWN:
        return float(math.floor(value))
    if mode == FRACTION_UP:
        return float(math.ceil(value))
    if mode == FRACTION_HALF:
        return float(math.floor(value + 0.5))
    return value


def service_years(
    start: _dt.date,
    end: _dt.date,
    *,
    added: float = 0.0,
    deducted: float = 0.0,
    basis: str = SERVICE_DAILY,
    fraction: str = FRACTION_KEEP,
) -> float:
    """근속연수(년).

    :param basis: ``일할`` / ``월할`` / ``연할``.
    :param fraction: 단수 처리 — ``그대로`` / ``절사`` / ``절상`` / ``반올림``.
        가산·차감 연수를 더한 **뒤** 적용한다. 군경력 가산이 단수에 영향을 주기
        때문이다.
    """
    if end < start:
        return 0.0

    if basis == SERVICE_MONTHLY:
        raw = _completed_months(start, end) / 12.0
    elif basis == SERVICE_ANNUAL:
        raw = float(_completed_years(start, end))
    else:
        raw = (end - start).days / DAYS_PER_YEAR

    return max(0.0, apply_fraction(raw + added - deducted, fraction))


def round_amount(value: float, unit: int = 0, mode: str = FRACTION_HALF) -> float:
    """지급액 반올림.

    규정에 ``ROUND(평균임금 × 근속년월 × 지급률, -1)`` 처럼 10원 단위로 맞추라고
    적힌 회사가 있다. ``unit`` 이 0 이나 1 이면 그대로 둔다.
    """
    if unit <= 1 or value == 0:
        return value
    scaled = value / unit
    if mode == FRACTION_DOWN:
        rounded = math.floor(scaled)
    elif mode == FRACTION_UP:
        rounded = math.ceil(scaled)
    else:
        rounded = math.floor(scaled + 0.5)
    return rounded * unit


def normal_retirement_age(
    age: int,
    rule: JobGroupRule,
    *,
    wage_peak_age: int | None = None,
    is_executive: bool = False,
) -> int:
    """퇴직급여 정년연령.

    VBA::

        If c_impi(jc) > age(jc) Then
            t_y(jc) = c_impi(jc)                       ' 임금피크 연령
        ElseIf age(jc) >= nra(jkn_j) Then
            t_y(jc) = age(jc) + add_age(jkn_j)         ' 정년 초과자
        Else
            t_y(jc) = nra(jkn_j)
        End If

    임금피크 연령이 현재 연령보다 크면 그 값을 정년으로 쓰고, 이미 정년을 넘긴
    사람은 현재 연령에 직군별 가산연수를 더해 잔여 근무기간을 확보한다.

    임원은 정년이 따로 없거나 다른 경우가 많아 직군 규칙의 임원 값을 먼저 본다.
    """
    nra = rule.severance_nra
    add_age = rule.over_nra_add_age
    if is_executive:
        if rule.executive_nra:
            nra = rule.executive_nra
        if rule.executive_over_nra_add_age:
            add_age = rule.executive_over_nra_add_age

    if wage_peak_age is not None and wage_peak_age > age:
        return wage_peak_age
    if age >= nra:
        return age + add_age
    return nra


def longterm_retirement_age(age: int, rule: JobGroupRule) -> int:
    """장기급여 정년연령.

    퇴직급여와 달리 임금피크 연령을 보지 않는다(VBA 동일).
    """
    if age >= rule.longterm_nra:
        return age + rule.over_nra_add_age
    return rule.longterm_nra
