"""기타장기종업원급여 산출."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from pension.assumptions import (
    Assumptions,
    BenefitScale,
    DiscountCurve,
    MortalityTable,
    RateCurve,
    RateTable,
    SalaryScale,
)
from pension.config import CalculationConfig, JobGroupRule
from pension.longterm import value_longterm, value_longterm_member
from pension.models import ActiveMember, RateRules, Roster
from pension.normalize import BenefitPlan, Gender
from pension.pipeline import RunOptions, run_valuation

BASE_DATE = dt.date(2025, 12, 31)


@pytest.fixture
def config() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE_DATE,
        job_group_rules=[JobGroupRule("정규직", "정규직", 60, 60, 2)],
    )


@pytest.fixture
def assumptions() -> Assumptions:
    """할인·임금상승·탈퇴가 모두 없는 가정. 손으로 검산할 수 있다."""
    return Assumptions(
        discount=DiscountCurve(spot=RateCurve({1: 0.0}), flat=0.0),
        salary=SalaryScale(base_up=RateCurve({1: 0.0})),
        withdrawal=RateTable(curves={"기본": RateCurve({0: 0.0})}, default_rule="기본"),
        mortality=MortalityTable(RateCurve({0: 0.0}), RateCurve({0: 0.0})),
        longterm_benefit=BenefitScale(
            curves={"포상": RateCurve({10: 10.0, 20: 20.0, 30: 30.0})},
            statutory_when_missing=False,
        ),
    )


def make_member(*, age: int = 40, past_service: float = 12.0,
                daily: float = 100_000, target: str = "Y") -> ActiveMember:
    member = ActiveMember(seq=1, row=26)
    member.employee_id = "L001"
    member.gender = Gender.MALE
    member.birth_date = dt.date(BASE_DATE.year - age, BASE_DATE.month, BASE_DATE.day)
    member.hire_date = BASE_DATE - dt.timedelta(days=round(past_service * 365.25))
    member.settlement_date = member.hire_date
    member.monthly_wage = daily * 30
    member.daily_base_pay = daily
    member.plan = BenefitPlan.DB
    member.longterm_target = target
    member.age = age
    member.longterm_nra = 60
    member.severance_nra = 60
    member.rules = RateRules(longterm_benefit="포상", longterm_withdrawal="기본",
                             longterm_salary_increase="기본")
    return member


class TestMilestones:
    def test_only_future_milestones_count(self, config, assumptions) -> None:
        """근속 12년이면 10년 포상은 이미 받았으므로 20·30년만 남는다."""
        result = value_longterm_member(make_member(past_service=12.0), config, assumptions)
        assert result.next_milestone == 20
        assert result.milestone_count == 2

    def test_milestones_past_retirement_are_dropped(self, config, assumptions) -> None:
        """55세·근속 16년이면 정년(60세)까지 5년. 20년 포상은 닿고 30년은 못 닿는다."""
        result = value_longterm_member(
            make_member(age=55, past_service=16.0), config, assumptions
        )
        assert result.milestone_count == 1
        assert result.next_milestone == 20

    def test_unreachable_milestones_give_no_obligation(self, config, assumptions) -> None:
        """55세·근속 12년이면 정년까지 17년만 쌓여 20년 포상에도 못 닿는다."""
        result = value_longterm_member(
            make_member(age=55, past_service=12.0), config, assumptions
        )
        assert result.milestone_count == 0
        assert result.dbo == 0.0

    def test_obligation_is_the_attributed_share_of_each_milestone(
        self, config, assumptions
    ) -> None:
        # 할인·상승·탈퇴가 없으므로 각 시점의 급여 × (과거근속 / 도달근속) 합.
        member = make_member(past_service=12.0, daily=100_000)
        result = value_longterm_member(member, config, assumptions)

        past = result.past_service
        expected = (
            20 * 100_000 * (past / 20)   # 20년 포상
            + 30 * 100_000 * (past / 30)  # 30년 포상
        )
        assert result.dbo == pytest.approx(expected, rel=1e-9)

    def test_discounting_reduces_the_obligation(self, config, assumptions) -> None:
        undiscounted = value_longterm_member(make_member(), config, assumptions).dbo
        assumptions.discount = DiscountCurve(spot=RateCurve({1: 0.045}), flat=0.045)
        discounted = value_longterm_member(make_member(), config, assumptions).dbo
        assert 0 < discounted < undiscounted

    def test_withdrawal_reduces_the_obligation(self, config, assumptions) -> None:
        """퇴직급여와 달리 재직해야 받으므로 탈퇴는 언제나 채무를 줄인다."""
        before = value_longterm_member(make_member(), config, assumptions).dbo
        assumptions.withdrawal = RateTable(
            curves={"기본": RateCurve({0: 0.08})}, default_rule="기본"
        )
        after = value_longterm_member(make_member(), config, assumptions).dbo
        assert after < before


class TestExclusions:
    def test_members_flagged_n_are_excluded(self, config, assumptions) -> None:
        result = value_longterm_member(make_member(target="N"), config, assumptions)
        assert result.dbo == 0
        assert "산출대상 아님" in result.excluded_reason

    def test_missing_rule_is_reported_not_silently_zero(self, config, assumptions) -> None:
        member = make_member()
        member.rules.longterm_benefit = "없는규정"
        result = value_longterm_member(member, config, assumptions)
        assert result.dbo == 0
        assert "없는규정" in result.excluded_reason


def test_roster_totals(config, assumptions) -> None:
    roster = Roster(active=[make_member(past_service=12.0), make_member(past_service=25.0)])
    result = value_longterm(roster, config, assumptions)
    assert result.headcount == 2
    assert result.dbo == pytest.approx(sum(m.dbo for m in result.members))


def test_end_to_end_longterm_is_calculated(
    roster_path: Path, assumptions_path: Path, tmp_path: Path
) -> None:
    """픽스처 명부에서 장기급여가 실제로 산출되는지 확인한다."""
    run = run_valuation(RunOptions(
        roster_path=roster_path,
        assumptions_path=assumptions_path,
        output_path=tmp_path / "결과.xlsx",
    ))
    assert run.longterm is not None
    assert run.longterm.headcount == 2  # 명부에서 'Y' 인 두 명
    assert run.longterm.dbo > 0


class TestLongTermServiceIgnoresSettlement:
    """장기근속포상 근속은 **중간정산을 보지 않는다.**

    퇴직금을 중간정산했다고 근속포상 시계가 0 으로 돌아가지 않는다. 중간정산은
    이미 지급한 퇴직금을 정산한 것이지 근속을 끊은 것이 아니다. 종전에는
    퇴직급여와 같은 기산일(중간정산일)을 써서, 중간정산이 있는 회사의
    10년·20년 포상을 통째로 놓쳤다 — 장기급여채무가 크게 과소계상됐다.

    실제 자료요청서들도 이 칸을 따로 받는다
    ('장기근속포상 기산일 (※ 일반적으로 입사일)').
    """

    def _member(self, **kw):
        member = ActiveMember(seq=1, row=26)
        member.employee_id = "A1"
        member.gender = Gender.MALE
        member.birth_date = dt.date(1980, 1, 1)
        member.hire_date = dt.date(2010, 1, 1)      # 근속 16년
        member.settlement_date = dt.date(2022, 1, 1)  # 중간정산 후로는 4년
        member.monthly_wage = 3_000_000
        member.daily_base_pay = 100_000
        member.plan = BenefitPlan.DB
        member.job_group = "정규직"
        member.job_group_index = 0
        member.longterm_target = "Y"
        member.age = 46
        member.longterm_nra = 60
        member.rules = RateRules(longterm_benefit="포상", longterm_withdrawal="기본",
                                 longterm_salary_increase="기본")
        for name, value in kw.items():
            setattr(member, name, value)
        return member

    def test_service_is_counted_from_the_hire_date(self, config, assumptions) -> None:
        result = value_longterm_member(self._member(), config, assumptions)
        # 중간정산일부터 세면 4년이 되어 10년 포상을 아직 못 받은 것이 된다.
        assert result.past_service == pytest.approx(16.0, abs=0.02)

    def test_a_declared_start_date_wins(self, config, assumptions) -> None:
        """명부에 장기급여 기산일이 적혀 있으면 그것을 쓴다."""
        member = self._member(longterm_start_date=dt.date(2005, 1, 1))
        result = value_longterm_member(member, config, assumptions)
        assert result.past_service == pytest.approx(21.0, abs=0.02)

    def test_the_settlement_no_longer_shrinks_the_liability(
        self, config, assumptions
    ) -> None:
        """중간정산이 있든 없든 장기급여채무는 같아야 한다."""
        with_settlement = value_longterm_member(self._member(), config, assumptions)
        without = value_longterm_member(
            self._member(settlement_date=None), config, assumptions)
        assert with_settlement.dbo == pytest.approx(without.dbo)
        assert with_settlement.dbo > 0
