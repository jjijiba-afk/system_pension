"""장기급여 지급유형 4종.

근속 포상은 회사마다 주는 것이 다르다. 실제 규정에서 본 것만 해도
'휴가 10일', '현물 포상 + 기념품', '평균임금의 500%', '100만원' 이다.
같은 표에 적힌 숫자가 유형에 따라 일수·배수·금액으로 달라진다.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pension.assumptions import (
    LONGTERM_RULE_SHEET,
    LT_AVERAGE_WAGE,
    LT_CASH,
    LT_IN_KIND,
    LT_VACATION,
    Assumptions,
    BenefitScale,
    DiscountCurve,
    LongTermRule,
    RateCurve,
    load_assumptions,
    write_assumptions,
)
from pension.config import CalculationConfig, JobGroupRule
from pension.longterm import value_longterm_member
from pension.models import ActiveMember, RateRules
from pension.normalize import BenefitPlan

BASE = _dt.date(2025, 12, 31)
RULE = "정규직"


def _assumptions(kind: str, escalation: float = 0.0, value: float = 10.0) -> Assumptions:
    """근속 10년에 ``value`` 를 주는 규정 하나짜리 가정."""
    return Assumptions(
        discount=DiscountCurve(spot=RateCurve({1: 0.05}), flat=0.05),
        longterm_benefit=BenefitScale(
            curves={RULE: RateCurve({10: value})}, statutory_when_missing=False
        ),
        longterm_rules={RULE: [LongTermRule(kind=kind, escalation=escalation)]},
    )


def _member() -> ActiveMember:
    member = ActiveMember(seq=1, row=26)
    member.employee_id = "A1"
    member.job_group = RULE
    member.job_group_index = 0
    member.birth_date = _dt.date(1990, 1, 1)
    member.hire_date = _dt.date(2020, 12, 31)   # 근속 5년
    member.settlement_date = member.hire_date
    member.monthly_wage = 3_000_000
    member.daily_base_pay = 100_000
    member.plan = BenefitPlan.DB
    member.longterm_target = "Y"
    member.age = 35
    member.longterm_nra = 60
    member.rules = RateRules(longterm_benefit=RULE)
    return member


def _config() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE, job_group_rules=[JobGroupRule(RULE, RULE, 60, 60, 2)]
    )


class TestPaymentKinds:
    """같은 표 값 10 이 유형마다 다른 금액이 된다."""

    def test_vacation_uses_daily_base_pay(self) -> None:
        result = value_longterm_member(_member(), _config(), _assumptions(LT_VACATION))
        assert result.benefit_kind == LT_VACATION
        assert result.dbo > 0

    def test_average_wage_uses_monthly_wage(self) -> None:
        member = _member()
        vacation = value_longterm_member(member, _config(), _assumptions(LT_VACATION))
        average = value_longterm_member(member, _config(), _assumptions(LT_AVERAGE_WAGE))

        # 휴가 10일 × 10만원 = 100만원 vs 평균임금 10배 = 3,000만원
        assert average.dbo == pytest.approx(vacation.dbo * 30, rel=0.01)

    def test_cash_is_a_flat_amount(self) -> None:
        """규정 금액이 고정이므로 임금상승률을 타지 않는다."""
        assumptions = _assumptions(LT_CASH, value=1_000_000)
        assumptions.salary.base_up = RateCurve({1: 0.10})   # 10% 인상해도

        member = _member()
        cash = value_longterm_member(member, _config(), assumptions)

        flat = _assumptions(LT_CASH, value=1_000_000)
        assert cash.dbo == pytest.approx(value_longterm_member(member, _config(), flat).dbo)

    def test_in_kind_grows_at_its_own_rate(self) -> None:
        """금 시세는 임금과 따로 움직인다."""
        member = _member()
        flat = value_longterm_member(member, _config(), _assumptions(LT_IN_KIND, 0.0, 1_000_000))
        rising = value_longterm_member(
            member, _config(), _assumptions(LT_IN_KIND, 0.05, 1_000_000)
        )
        assert rising.dbo > flat.dbo

    def test_in_kind_ignores_wage_growth(self) -> None:
        assumptions = _assumptions(LT_IN_KIND, 0.0, 1_000_000)
        assumptions.salary.base_up = RateCurve({1: 0.10})
        member = _member()

        without = _assumptions(LT_IN_KIND, 0.0, 1_000_000)
        assert value_longterm_member(member, _config(), assumptions).dbo == pytest.approx(
            value_longterm_member(member, _config(), without).dbo
        )

    def test_missing_rule_defaults_to_vacation(self) -> None:
        """유형을 안 적으면 기존 동작(휴가)을 유지한다."""
        assumptions = _assumptions(LT_VACATION)
        assumptions.longterm_rules.clear()
        result = value_longterm_member(_member(), _config(), assumptions)
        assert result.benefit_kind == LT_VACATION
        assert result.dbo > 0


class TestWorkbook:
    def _sheets(self) -> dict:
        return {
            "할인율": (["연차", "할인율"], [[1, 0.045]]),
            "지급률": (["근속연수", RULE], [[0, 1.0]]),
            "장기급여지급률": (["근속연수", RULE], [[10, 1_000_000]]),
        }

    def test_round_trip(self, tmp_path) -> None:
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [[RULE, LT_IN_KIND, 0.045, "현물 포상 @ 2025-12-31 시세"]],
        )
        loaded = load_assumptions(path)
        rule = loaded.longterm_rule(RULE)
        assert rule.kind == LT_IN_KIND
        assert rule.escalation == pytest.approx(0.045)
        assert "현물 포상" in rule.note

    def test_unknown_kind_is_reported(self, tmp_path) -> None:
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None, [[RULE, "포인트", None, ""]]
        )
        with pytest.raises(ValueError, match="지급유형"):
            load_assumptions(path)

    def test_escalation_on_a_non_in_kind_kind_is_reported(self, tmp_path) -> None:
        """휴가에 현물 상승률을 적으면 어느 쪽이 의도인지 알 수 없다."""
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None, [[RULE, LT_VACATION, 0.05, ""]]
        )
        with pytest.raises(ValueError, match="현물 상승률을 쓰지 않습니다"):
            load_assumptions(path)

    def test_sheet_is_optional(self, tmp_path) -> None:
        """유형 시트가 없던 기존 파일도 그대로 읽히고, 전부 휴가로 본다."""
        import openpyxl

        path = write_assumptions(tmp_path / "기초율.xlsx", self._sheets(), None, None)
        assert LONGTERM_RULE_SHEET not in openpyxl.load_workbook(path).sheetnames
        assert load_assumptions(path).longterm_rule(RULE).kind == LT_VACATION
