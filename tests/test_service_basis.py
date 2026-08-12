"""근속기간 산정방법과 지급액 반올림.

회사 규정마다 다른 것을 실제 케이스에서 확인했다.

* '근로기준법에 따른 산정법 - 일수'        → 일할
* '근속기간 1년 이상 (월할 계산)'          → 월할
* '1년이 되지 않는 단수개월은 절사'        → 연할 + 절사
* 'ROUND(평균임금 × 근속년월 × 지급률, -1)' → 10원 단위 반올림
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pension.actuarial import (
    FRACTION_DOWN,
    FRACTION_HALF,
    FRACTION_KEEP,
    FRACTION_UP,
    SERVICE_ANNUAL,
    SERVICE_DAILY,
    SERVICE_MONTHLY,
    round_amount,
    service_years,
)

HIRE = _dt.date(2020, 3, 15)
BASE = _dt.date(2025, 12, 31)


class TestServiceBasis:
    def test_daily_uses_365_like_the_statute(self) -> None:
        """근로자퇴직급여보장법 산식은 계속근로 총일수 ÷ 365 다."""
        days = (BASE - HIRE).days
        assert service_years(HIRE, BASE, basis=SERVICE_DAILY) == pytest.approx(days / 365)

    def test_monthly_counts_completed_months(self) -> None:
        # 2020-03-15 → 2025-12-31 은 69개월 하고 16일
        assert service_years(HIRE, BASE, basis=SERVICE_MONTHLY) == pytest.approx(69 / 12)

    def test_monthly_drops_an_incomplete_month(self) -> None:
        # 하루가 모자라면 그 달은 세지 않는다.
        assert service_years(
            _dt.date(2025, 1, 20), _dt.date(2025, 2, 19), basis=SERVICE_MONTHLY
        ) == 0.0

    def test_annual_counts_completed_years_only(self) -> None:
        assert service_years(HIRE, BASE, basis=SERVICE_ANNUAL) == 5.0

    def test_annual_drops_the_fraction(self) -> None:
        """케이스 5 규정: '3년 10개월인 경우에는 3년'."""
        start = _dt.date(2022, 3, 1)
        end = _dt.date(2026, 1, 1)   # 3년 10개월
        assert service_years(start, end, basis=SERVICE_ANNUAL) == 3.0

    def test_end_before_start_is_zero(self) -> None:
        assert service_years(BASE, HIRE) == 0.0

    def test_same_day_is_zero(self) -> None:
        assert service_years(HIRE, HIRE) == 0.0


class TestFraction:
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [(FRACTION_KEEP, 5.8), (FRACTION_DOWN, 5.0), (FRACTION_UP, 6.0), (FRACTION_HALF, 6.0)],
    )
    def test_modes(self, mode, expected) -> None:
        start = _dt.date(2020, 1, 1)
        end = start + _dt.timedelta(days=round(5.8 * 365))
        assert service_years(start, end, basis=SERVICE_DAILY, fraction=mode) == pytest.approx(
            expected, abs=0.01
        )

    def test_half_rounds_down_below_the_midpoint(self) -> None:
        start = _dt.date(2020, 1, 1)
        end = start + _dt.timedelta(days=round(5.4 * 365))
        assert service_years(start, end, fraction=FRACTION_HALF) == 5.0

    def test_leave_shifts_the_start_before_the_fraction(self) -> None:
        """휴직은 **기산일을 미는 것** 이라 단수 판정까지 함께 움직인다.

        연 단위로 빼면 월할·연할 기준에서 단수가 어긋난다. 일수로 밀어야
        어느 기준을 쓰든 같은 답이 나온다.
        """
        start = _dt.date(2020, 1, 1)
        end = start + _dt.timedelta(days=round(5.6 * 365))
        assert service_years(start, end, fraction=FRACTION_DOWN) == 5.0
        # 219일(0.6년)을 밀면 5년에서 딱 걸린다.
        assert service_years(start, end, leave_days=250, fraction=FRACTION_DOWN) == 4.0

    def test_leave_longer_than_the_service_is_not_negative(self) -> None:
        assert service_years(HIRE, BASE, leave_days=99_999) == 0.0

    def test_leave_days_are_days_not_years(self) -> None:
        """365 를 넣으면 한 해가 줄어야 한다 — 연수로 읽으면 365년이 준다."""
        start = _dt.date(2015, 1, 1)
        end = _dt.date(2025, 1, 1)
        full = service_years(start, end)
        assert service_years(start, end, leave_days=365) == pytest.approx(
            full - 1.0, abs=0.01)


class TestRoundAmount:
    @pytest.mark.parametrize(
        ("value", "unit", "mode", "expected"),
        [
            (12_345, 10, FRACTION_HALF, 12_350),
            (12_344, 10, FRACTION_HALF, 12_340),
            (12_345, 10, FRACTION_DOWN, 12_340),
            (12_341, 10, FRACTION_UP, 12_350),
            (12_345, 100, FRACTION_HALF, 12_300),
            (12_355, 1000, FRACTION_HALF, 12_000),
        ],
    )
    def test_rounds_to_the_unit(self, value, unit, mode, expected) -> None:
        assert round_amount(value, unit, mode) == expected

    @pytest.mark.parametrize("unit", [0, 1])
    def test_no_unit_leaves_the_value_alone(self, unit) -> None:
        assert round_amount(12_345.67, unit) == 12_345.67

    def test_zero_stays_zero(self) -> None:
        assert round_amount(0, 10) == 0


class TestMemberUsesTheRule:
    def _member(self, basis: str, fraction: str):
        from pension.models import ActiveMember

        member = ActiveMember(seq=1, row=26)
        member.hire_date = HIRE
        member.settlement_date = HIRE
        member.service_basis = basis
        member.service_fraction = fraction
        return member

    def test_daily(self) -> None:
        member = self._member(SERVICE_DAILY, FRACTION_KEEP)
        assert member.service_years(BASE) == pytest.approx((BASE - HIRE).days / 365)

    def test_annual_truncated(self) -> None:
        member = self._member(SERVICE_ANNUAL, FRACTION_DOWN)
        assert member.service_years(BASE) == 5.0

    def test_settlement_date_starts_the_clock(self) -> None:
        member = self._member(SERVICE_DAILY, FRACTION_KEEP)
        member.settlement_date = _dt.date(2024, 12, 31)
        assert member.service_years(BASE) == pytest.approx(365 / 365, abs=0.01)


class TestBenefitRounding:
    """지급액 반올림이 실제 산출에 반영되는지."""

    def test_rounds_the_accrued_benefit(self) -> None:
        from pension.assumptions import Assumptions, DiscountCurve, RateCurve
        from pension.config import CalculationConfig, JobGroupRule
        from pension.models import ActiveMember
        from pension.normalize import BenefitPlan
        from pension.valuation import value_member

        rule = JobGroupRule(
            "정규직", "정규직", 60, 60, 2,
            benefit_rounding_unit=1000, benefit_rounding_mode=FRACTION_DOWN,
        )
        config = CalculationConfig(base_date=BASE, job_group_rules=[rule])
        assumptions = Assumptions(
            discount=DiscountCurve(spot=RateCurve({1: 0.045}), flat=0.045)
        )

        member = ActiveMember(seq=1, row=26)
        member.job_group_raw = "정규직"
        member.job_group = "정규직"
        member.job_group_index = 0
        member.birth_date = _dt.date(1985, 1, 1)
        member.hire_date = HIRE
        member.settlement_date = HIRE
        member.monthly_wage = 3_333_333
        member.plan = BenefitPlan.DB
        member.age = 40
        member.severance_nra = 60

        result = value_member(member, config, assumptions)
        assert result.rounding_unit == 1000
        assert result.accrued_benefit % 1000 == 0
