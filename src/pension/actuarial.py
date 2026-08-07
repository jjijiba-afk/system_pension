"""연령·근속·정년연령 계산.

VBA 가 명부 읽기 루프 안에 인라인으로 흩어 놓은 계산식을 함수로 분리했다.
"""

from __future__ import annotations

import datetime as _dt

from .config import JobGroupRule

__all__ = ["attained_age", "longterm_retirement_age", "normal_retirement_age", "service_years"]


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


def service_years(start: _dt.date, end: _dt.date, *, added: float = 0.0, deducted: float = 0.0) -> float:
    """근속연수(년). 가산·차감 연수를 반영하며 음수는 0 으로 자른다."""
    if end < start:
        return 0.0
    return max(0.0, (end - start).days / 365.25 + added - deducted)


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
