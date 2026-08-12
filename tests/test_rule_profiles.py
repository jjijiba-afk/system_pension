"""회사별 지급규정 설정 — 임원 정년·직군 제외·위로금.

실제 케이스 6건의 `일반사항` 6번 항목을 반영하며 추가된 기능들이다.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pension.actuarial import normal_retirement_age
from pension.assumptions import Assumptions, DiscountCurve, RateCurve
from pension.config import CalculationConfig, JobGroupRule
from pension.errors import IssueLog
from pension.models import ActiveMember
from pension.normalize import BenefitPlan, EmployeeType
from pension.validation import validate_active
from pension.valuation import value_member

BASE = _dt.date(2025, 12, 31)


def _rule(**kw) -> JobGroupRule:
    defaults = dict(source_name="정규직", mapped_name="정규직",
                    severance_nra=60, longterm_nra=60, over_nra_add_age=2)
    defaults.update(kw)
    return JobGroupRule(**defaults)


def _member(rule: JobGroupRule, **kw) -> ActiveMember:
    member = ActiveMember(seq=1, row=26)
    member.employee_id = "A1"
    member.job_group_raw = rule.source_name
    member.job_group = rule.mapped_name
    member.job_group_index = 0
    member.birth_date = _dt.date(1980, 1, 1)
    member.hire_date = _dt.date(2005, 1, 1)
    member.settlement_date = member.hire_date
    member.monthly_wage = 5_000_000
    member.plan = BenefitPlan.DB
    member.min_service_years = rule.min_service_years
    member.excluded_group = rule.excluded
    for key, value in kw.items():
        setattr(member, key, value)
    return member


class TestExecutiveRetirementAge:
    """임원은 정년이 따로 없거나 다른 경우가 많다.

    실제 명부에서 임원의 직군이 '정규직' 으로 적혀 있어, 직군만으로는 임원을
    분리할 수 없었다. 그래서 직군 규칙 안에 임원 값을 따로 둔다.
    """

    def test_executive_uses_its_own_nra(self) -> None:
        rule = _rule(severance_nra=60, executive_nra=39, executive_over_nra_add_age=2)
        # 55세 임원: 임원 정년 39세를 이미 넘겼으므로 현재 연령 + 2
        assert normal_retirement_age(55, rule, is_executive=True) == 57
        # 같은 나이의 직원은 정년 60세
        assert normal_retirement_age(55, rule, is_executive=False) == 60

    def test_executive_falls_back_to_staff_values(self) -> None:
        rule = _rule(severance_nra=60, over_nra_add_age=2)
        assert normal_retirement_age(50, rule, is_executive=True) == 60

    def test_no_executive_retirement_age_means_default_plus_add(self) -> None:
        """규정에 '없음' 이면 기본 정년 60세 + 가산 2세로 처리한다."""
        rule = _rule(executive_nra=60, executive_over_nra_add_age=2)
        assert normal_retirement_age(58, rule, is_executive=True) == 60
        assert normal_retirement_age(62, rule, is_executive=True) == 64

    def test_wage_peak_still_wins_when_ahead(self) -> None:
        rule = _rule(executive_nra=60, executive_over_nra_add_age=2)
        assert normal_retirement_age(50, rule, wage_peak_age=58, is_executive=True) == 58

    def test_validation_applies_executive_rule(self) -> None:
        rule = _rule(executive_nra=39, executive_over_nra_add_age=2)
        config = CalculationConfig(base_date=BASE, job_group_rules=[rule])
        member = _member(rule, employee_type=EmployeeType.EXECUTIVE)
        validate_active([member], config, IssueLog())
        assert member.severance_nra == member.age + 2


class TestExcludedJobGroup:
    """'계약직은 퇴직금 대상에서 제외' 같은 직군 단위 제외."""

    def test_excluded_group_produces_no_liability(self) -> None:
        rule = _rule(source_name="계약직", mapped_name="계약직", excluded=True)
        config = CalculationConfig(base_date=BASE, job_group_rules=[rule])
        member = _member(rule)
        member.age = 45
        member.severance_nra = 60

        assumptions = Assumptions(
            discount=DiscountCurve(spot=RateCurve({1: 0.045}), flat=0.045)
        )
        result = value_member(member, config, assumptions)

        assert result.dbo == 0.0
        assert result.accrued_benefit == 0.0
        assert "제외" in result.excluded_reason

    def test_included_group_is_unaffected(self) -> None:
        rule = _rule(excluded=False)
        config = CalculationConfig(base_date=BASE, job_group_rules=[rule])
        member = _member(rule)
        member.age = 45
        member.severance_nra = 60

        assumptions = Assumptions(
            discount=DiscountCurve(spot=RateCurve({1: 0.045}), flat=0.045)
        )
        assert value_member(member, config, assumptions).dbo > 0.0

    def test_excluded_flag_reads_several_spellings(self) -> None:
        import openpyxl

        from pension.config import read_config

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Input"
        ws["C3"] = _dt.datetime(2025, 12, 31)
        for offset, token in enumerate(("Y", "제외", "N", "")):
            row = 12 + offset
            ws.cell(row, 2, f"직군{offset}")
            ws.cell(row, 3, f"직군{offset}")
            ws.cell(row, 18, token)

        config = read_config(wb)
        assert [r.excluded for r in config.job_group_rules] == [True, True, False, False]


class TestSeveranceBonus:
    """'위로금 금액이 적혀 있으면 대상자' — 명부의 추가지급 기본급."""

    def _valued(self, extra: float):
        rule = _rule()
        config = CalculationConfig(base_date=BASE, job_group_rules=[rule])
        member = _member(rule, extra_pay_base_wage=extra)
        member.age = 45
        member.severance_nra = 60
        assumptions = Assumptions(
            discount=DiscountCurve(spot=RateCurve({1: 0.045}), flat=0.045)
        )
        return value_member(member, config, assumptions)

    def test_bonus_is_added_to_the_benefit(self) -> None:
        without = self._valued(0.0)
        with_bonus = self._valued(10_000_000)

        assert with_bonus.extra_payment == 10_000_000
        assert with_bonus.accrued_benefit == pytest.approx(
            without.accrued_benefit + 10_000_000
        )
        assert with_bonus.dbo > without.dbo

    def test_blank_means_not_eligible(self) -> None:
        result = self._valued(0.0)
        assert result.extra_payment == 0.0

    def test_negative_is_ignored(self) -> None:
        assert self._valued(-5_000_000).extra_payment == 0.0


class TestExclusionSummary:
    """DC 전환을 마친 회사는 채무가 0 이다. 이유를 함께 보여 줘야 한다."""

    def test_summarises_reasons(self) -> None:
        from pension.valuation import MemberValuation, ValuationResult

        result = ValuationResult(base_date=BASE, label="당기")
        for index in range(3):
            member = MemberValuation(
                employee_id=f"A{index}", name="", job_group="정규직", gender="남자",
                age=40, past_service=10.0, projection_years=20, monthly_wage=0.0,
            )
            member.excluded_reason = "DC 가입자 (확정기여제도는 확정급여채무 없음)"
            result.members.append(member)

        assert result.headcount == 0
        assert result.dbo == 0.0
        summary = result.exclusion_summary()
        assert summary == {"DC 가입자 (확정기여제도는 확정급여채무 없음)": 3}

    def test_empty_when_everyone_is_valued(self) -> None:
        from pension.valuation import ValuationResult

        assert ValuationResult(base_date=BASE, label="당기").exclusion_summary() == {}
