"""PUC 산출 엔진.

해석적으로 답을 알 수 있는 단순한 가정을 넣어, 엔진이 교과서 공식과 맞는지
확인한다.
"""

from __future__ import annotations

import datetime as dt

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
from pension.models import ActiveMember, RateRules, Roster
from pension.normalize import BenefitPlan, Gender
from pension.rollforward import build_rollforward, initial_period
from pension.sensitivity import DEFAULT_SHOCKS, run_sensitivity
from pension.valuation import value_member, value_roster

BASE_DATE = dt.date(2025, 12, 31)


@pytest.fixture
def config() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE_DATE,
        job_group_rules=[
            JobGroupRule("정규직", "정규직", severance_nra=60, longterm_nra=60, over_nra_add_age=2)
        ],
    )


def make_assumptions(
    *, discount: float = 0.05, salary: float = 0.0,
    withdrawal: float = 0.0, mortality: float = 0.0,
) -> Assumptions:
    """모든 연령·근속에 같은 값이 적용되는 평탄한 가정."""
    return Assumptions(
        discount=DiscountCurve(spot=RateCurve({1: discount}), flat=discount),
        salary=SalaryScale(base_up=RateCurve({1: salary})),
        withdrawal=RateTable(curves={"기본": RateCurve({0: withdrawal})}, default_rule="기본"),
        mortality=MortalityTable(RateCurve({0: mortality}), RateCurve({0: mortality})),
        severance_benefit=BenefitScale(statutory_when_missing=True),
    )


def make_member(*, age: int = 50, past_service: float = 10.0, wage: float = 1_000_000,
                nra: int = 60) -> ActiveMember:
    """지정한 연령·근속을 갖도록 생년월일과 중간정산일을 역산한 재직자."""
    birth = dt.date(BASE_DATE.year - age, BASE_DATE.month, BASE_DATE.day)
    start = BASE_DATE - dt.timedelta(days=round(past_service * 365.25))
    member = ActiveMember(seq=1, row=26)
    member.employee_id = "T001"
    member.name = "테스트"
    member.gender = Gender.MALE
    member.birth_date = birth
    member.hire_date = start
    member.settlement_date = start
    member.monthly_wage = wage
    member.plan = BenefitPlan.DB
    member.job_group = "정규직"
    member.job_group_index = 0
    member.age = age
    member.severance_nra = nra
    member.longterm_nra = nra
    member.rules = RateRules(severance_withdrawal="기본", severance_salary_increase="기본")
    return member


class TestSingleDecrementCase:
    """탈퇴가 정년 하나뿐이면 손으로 검산할 수 있다."""

    def test_dbo_equals_discounted_attributed_benefit(self, config: CalculationConfig) -> None:
        # 50세, 근속 10년, 정년 60세 → 10년 뒤 정년퇴직만 발생.
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        assumptions = make_assumptions(discount=0.05, salary=0.0)

        result = value_member(member, config, assumptions)

        # 정년 시 총근속 = 과거근속 + 10년. 법정 지급률이므로 급여 = 총근속 × 임금,
        # 귀속비율은 과거근속 / 총근속이다.
        past = result.past_service
        total = past + 10
        expected = total * 1_000_000 * (past / total) / (1.05**10)
        assert result.dbo == pytest.approx(expected, rel=1e-9)

    def test_service_cost_is_one_years_worth_of_the_same_benefit(
        self, config: CalculationConfig
    ) -> None:
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        result = value_member(member, config, make_assumptions(discount=0.05))

        total = result.past_service + 10
        expected = total * 1_000_000 * (1 / total) / (1.05**10)
        assert result.service_cost == pytest.approx(expected, rel=1e-9)

    def test_service_cost_times_past_service_equals_dbo(self, config: CalculationConfig) -> None:
        """귀속비율이 PS/TS 와 1/TS 이므로 DBO = 근무원가 × 과거근속이다."""
        member = make_member(age=50, past_service=10.0, nra=60)
        result = value_member(member, config, make_assumptions())
        assert result.dbo == pytest.approx(result.service_cost * result.past_service, rel=1e-9)

    def test_salary_growth_compounds_into_the_benefit(self, config: CalculationConfig) -> None:
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        assumptions = make_assumptions(discount=0.05, salary=0.03)

        result = value_member(member, config, assumptions)

        past = result.past_service
        total = past + 10
        expected = total * 1_000_000 * (1.03**10) * (past / total) / (1.05**10)
        assert result.dbo == pytest.approx(expected, rel=1e-9)

    def test_a_personal_multiple_is_not_applied_on_its_own(
        self, config: CalculationConfig
    ) -> None:
        """명부의 개인 지급배수는 엔진이 스스로 곱하지 않는다.

        그 칸에 담긴 것이 회사마다 다르다. 배수인 회사, 누적 지급배수(근속 ×
        배수)인 회사, 한도인 회사가 섞여 온다. 실제로 `44` 가 적혀 온 명부가
        있었는데 그대로 곱하면 그 사람 하나가 채무를 통째로 흔든다.
        넘겨짚지 않고 지급규정이 `배수` 로 읽어 쓰게 둔다.
        """
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        plain = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        member.payout_multiple = 44.0
        marked = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        assert marked.dbo == pytest.approx(plain.dbo, rel=1e-9)
        assert marked.service_cost == pytest.approx(plain.service_cost, rel=1e-9)

    def test_an_extra_pay_wage_is_not_added_on_its_own(
        self, config: CalculationConfig
    ) -> None:
        """명부의 추가지급 기본급도 저절로 더해지지 않는다.

        사망 위로금인 회사, 명퇴 가산금인 회사가 섞여 온다. 모든 사유에 정액으로
        얹으면 사망확률이 낮은 만큼 정년·중도 몫이 통째로 부풀어 채무가 배로
        뛴다. 어느 사유에 붙는 돈인지는 [퇴직사유] 표가 정한다.
        """
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        plain = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        member.extra_pay_base_wage = 50_000_000
        marked = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        assert marked.dbo == pytest.approx(plain.dbo, rel=1e-9)

    def test_the_db_share_scales_the_whole_benefit(
        self, config: CalculationConfig
    ) -> None:
        """혼합형의 DC 몫은 확정급여채무가 아니다.

        `DC 1% / DB 99%` 인 회사에서 전액을 채무로 잡으면 1% 만큼 과대계상된다.
        비중이 클수록(DC 50%) 티가 나야 정상이다.
        """
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        full = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        member.db_ratio = 0.5
        half = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        assert half.db_ratio == 0.5
        assert half.dbo == pytest.approx(full.dbo / 2, rel=1e-9)
        assert half.service_cost == pytest.approx(full.service_cost / 2, rel=1e-9)
        # 비중은 급여만 깎는다. 귀속비율은 분자·분모가 같이 줄어 그대로다.
        assert half.past_service == full.past_service

    def test_an_empty_db_share_means_the_whole_benefit(
        self, config: CalculationConfig
    ) -> None:
        """비운 칸을 0 으로 읽으면 그 사람 채무가 통째로 사라진다."""
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        full = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))

        for bad in (0.0, -1.0, 1.5):
            member.db_ratio = bad
            assert value_member(
                member, config, make_assumptions(discount=0.05, salary=0.0)
            ).dbo == pytest.approx(full.dbo, rel=1e-9)

    def test_a_formula_can_read_the_personal_multiple(
        self, config: CalculationConfig
    ) -> None:
        """개인 배수를 쓰는 길은 하나뿐이다 — 지급규정 식에서 `배수` 를 부른다.

        규정이 부른 것만 반영되므로 밖에서 한 번 더 곱히지 않는다.
        """
        from pension.assumptions import Formula

        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        member.rules.severance_benefit = "임원"
        member.payout_multiple = 2.0

        assumptions = make_assumptions(discount=0.05, salary=0.0)
        assumptions.severance_benefit.formulas["임원"] = Formula("t * 배수")
        result = value_member(member, config, assumptions)

        past = result.past_service
        total = past + 10
        expected = total * 2 * 1_000_000 * (past / total) / (1.05**10)
        assert result.dbo == pytest.approx(expected, rel=1e-9)

    def test_interest_cost_is_dbo_times_discount_rate(self, config: CalculationConfig) -> None:
        member = make_member()
        result = value_member(member, config, make_assumptions(discount=0.05))
        assert result.interest_cost == pytest.approx(result.dbo * 0.05, rel=1e-12)


class TestDecrements:
    def test_decrements_pull_payment_forward_and_raise_the_obligation(
        self, config: CalculationConfig
    ) -> None:
        """법정 지급률 + 임금상승 0 이면 탈퇴가 채무를 **키운다**.

        급여는 ``총근속 × 임금``, 귀속비율은 ``과거근속 / 총근속`` 이므로 귀속된
        급여액은 언제 나가든 ``과거근속 × 임금`` 으로 같다. 그러면 남는 차이는
        시점뿐이고, 일찍 나갈수록 덜 할인되어 현가가 커진다.
        """
        member = make_member(age=40, past_service=10.0, nra=60)
        without = value_member(member, config, make_assumptions(withdrawal=0.0)).dbo
        with_withdrawal = value_member(member, config, make_assumptions(withdrawal=0.10)).dbo
        assert with_withdrawal > without

    def test_mortality_behaves_like_withdrawal(self, config: CalculationConfig) -> None:
        member = make_member(age=40, past_service=10.0, nra=60)
        without = value_member(member, config, make_assumptions(mortality=0.0)).dbo
        with_mortality = value_member(member, config, make_assumptions(mortality=0.02)).dbo
        assert with_mortality > without

    def test_decrements_lower_the_obligation_when_salary_outpaces_the_discount(
        self, config: CalculationConfig
    ) -> None:
        """임금상승률이 할인율보다 높으면 반대로 뒤집힌다.

        오래 남을수록 급여 기준임금이 더 크게 오르므로, 일찍 나가는 것이 채무를
        줄인다. 부호가 가정에 따라 갈린다는 사실 자체가 중요하다.
        """
        assumptions_kw = {"discount": 0.03, "salary": 0.06}
        member = make_member(age=40, past_service=10.0, nra=60)
        without = value_member(member, config, make_assumptions(**assumptions_kw)).dbo
        with_withdrawal = value_member(
            member, config, make_assumptions(**assumptions_kw, withdrawal=0.10)
        ).dbo
        assert with_withdrawal < without

    def test_attributed_benefit_is_invariant_to_exit_timing(
        self, config: CalculationConfig
    ) -> None:
        """할인율 0 이면 탈퇴율과 무관하게 DBO = 과거근속 × 임금 이어야 한다."""
        member = make_member(age=40, past_service=10.0, wage=1_000_000, nra=60)
        result = value_member(
            member, config, make_assumptions(discount=0.0, withdrawal=0.10)
        )
        assert result.dbo == pytest.approx(result.past_service * 1_000_000, rel=1e-9)

    def test_exit_probabilities_sum_to_one(self, config: CalculationConfig) -> None:
        """탈퇴확률 합이 1 이어야 급여가 새거나 이중계상되지 않는다.

        지급률을 근속과 무관한 상수로 두고 할인·임금상승을 끄면, 미래급여
        현가는 급여액 그 자체와 같아야 한다.
        """
        member = make_member(age=55, past_service=10.0, wage=1_000_000, nra=60)
        assumptions = make_assumptions(discount=0.0, withdrawal=0.10, mortality=0.01)
        assumptions.severance_benefit = BenefitScale(
            curves={"고정": RateCurve({0: 5.0})}, statutory_when_missing=False
        )
        member.rules.severance_benefit = "고정"

        result = value_member(member, config, assumptions)
        assert result.expected_benefit_pv == pytest.approx(5.0 * 1_000_000, rel=1e-9)


class TestExclusions:
    def test_dc_members_carry_no_defined_benefit_obligation(
        self, config: CalculationConfig
    ) -> None:
        member = make_member()
        member.plan = BenefitPlan.DC
        result = value_member(member, config, make_assumptions())
        assert result.dbo == 0.0
        assert "DC" in result.excluded_reason

    def test_members_without_a_wage_are_excluded(self, config: CalculationConfig) -> None:
        member = make_member(wage=0)
        result = value_member(member, config, make_assumptions())
        assert result.dbo == 0.0
        assert result.excluded_reason


class TestRosterTotals:
    def test_totals_add_up_across_members(self, config: CalculationConfig) -> None:
        roster = Roster(active=[make_member(age=40), make_member(age=50), make_member(age=55)])
        result = value_roster(roster, config, make_assumptions())
        assert result.dbo == pytest.approx(sum(m.dbo for m in result.members))
        assert result.headcount == 3

    def test_members_hired_after_the_base_date_are_skipped(
        self, config: CalculationConfig
    ) -> None:
        future = make_member()
        future.hire_date = BASE_DATE + dt.timedelta(days=30)
        roster = Roster(active=[make_member(), future])
        assert value_roster(roster, config, make_assumptions()).headcount == 1


class TestSensitivity:
    def test_discount_rate_moves_the_obligation_inversely(
        self, config: CalculationConfig
    ) -> None:
        roster = Roster(active=[make_member(age=45, past_service=15.0)])
        assumptions = make_assumptions(discount=0.05)
        base = value_roster(roster, config, assumptions).dbo

        result = run_sensitivity(roster, config, assumptions, base_dbo=base)
        cases = {c.name: c for c in result.cases}

        assert cases["할인율 +0.5%p"].change < 0
        assert cases["할인율 -0.5%p"].change > 0

    def test_salary_growth_moves_the_obligation_directly(
        self, config: CalculationConfig
    ) -> None:
        roster = Roster(active=[make_member(age=45, past_service=15.0)])
        assumptions = make_assumptions(discount=0.05, salary=0.03)
        result = run_sensitivity(roster, config, assumptions)
        cases = {c.name: c for c in result.cases}

        assert cases["임금상승률 +0.5%p"].change > 0
        assert cases["임금상승률 -0.5%p"].change < 0

    def test_every_default_shock_produces_a_case(self, config: CalculationConfig) -> None:
        roster = Roster(active=[make_member()])
        result = run_sensitivity(roster, config, make_assumptions())
        assert len(result.cases) == len(DEFAULT_SHOCKS)


class TestRollForward:
    def test_components_reconcile_to_the_closing_balance(self) -> None:
        roll = build_rollforward(
            opening_dbo=1_000_000,
            service_cost=100_000,
            interest_cost=50_000,
            benefits_paid=80_000,
            closing_dbo=1_120_000,
        )
        assert roll.expected_closing_dbo == pytest.approx(1_070_000)
        assert roll.actuarial_gain_loss == pytest.approx(50_000)
        assert roll.closing_dbo == pytest.approx(1_120_000)

    def test_prior_assumption_run_splits_experience_from_assumption_change(self) -> None:
        roll = build_rollforward(
            opening_dbo=1_000_000,
            service_cost=100_000,
            interest_cost=50_000,
            benefits_paid=80_000,
            closing_dbo=1_120_000,
            dbo_with_prior_assumptions=1_090_000,
        )
        assert roll.experience_adjustment == pytest.approx(20_000)   # 1,090,000 - 1,070,000
        assert roll.assumption_change == pytest.approx(30_000)       # 1,120,000 - 1,090,000
        assert roll.closing_dbo == pytest.approx(1_120_000)

    def test_initial_period_starts_from_zero(self) -> None:
        roll = initial_period(closing_dbo=500_000, service_cost=40_000)
        assert roll.opening_dbo == 0.0
        assert roll.closing_dbo == pytest.approx(500_000)

    def test_initial_period_has_no_actuarial_gain_loss(self) -> None:
        """최초 평가의 보험수리적손익은 0 — 비교할 전기 채무가 없다.

        잔액을 경험조정에 밀어 넣으면 첫해에 기말채무만 한 가짜 손익이
        OCI 로 찍힌다. 잔액은 '최초 인식' 줄로 따로 간다.
        """
        roll = initial_period(closing_dbo=500_000, service_cost=40_000)
        assert roll.actuarial_gain_loss == 0.0
        assert roll.experience_adjustment == 0.0
        assert roll.assumption_change == 0.0
        assert roll.initial_recognition == pytest.approx(460_000)

        rows = dict(roll.as_rows())
        assert rows["최초 인식 (전기 산출 없음)"] == pytest.approx(460_000)
        assert rows["기말 확정급여채무"] == pytest.approx(500_000)

    def test_linked_period_has_no_initial_recognition_row(self) -> None:
        """전기를 연결한 증감표에는 최초 인식 줄이 나타나면 안 된다."""
        roll = build_rollforward(
            opening_dbo=1_000_000, service_cost=90_000, interest_cost=40_000,
            benefits_paid=60_000, closing_dbo=1_090_000,
        )
        assert roll.initial_recognition == 0.0
        assert "최초 인식 (전기 산출 없음)" not in dict(roll.as_rows())

    def test_rows_are_ordered_for_disclosure(self) -> None:
        roll = initial_period(500_000, 40_000)
        labels = [label for label, _ in roll.as_rows()]
        assert labels[0] == "기초 확정급여채무"
        assert labels[-1] == "기말 확정급여채무"


class TestBenefitFormulaAttribution:
    """귀속은 근속이 아니라 **급여식** 을 따라야 한다(문단 70).

    급여가 근속에 비례하면 두 방식이 같지만, 상한·정액 구간이 있으면 갈린다.
    """

    def _member(self, hire_year: int = 1991):
        import datetime as _dt

        from pension.models import ActiveMember, RateRules
        from pension.normalize import BenefitPlan, EmployeeType, Gender

        member = ActiveMember(
            seq=1, row=1, employee_id="A1", name="홍길동",
            job_group="정규직", job_group_raw="정규직",
            gender=Gender.MALE, birth_date=_dt.date(1988, 1, 1),
            hire_date=_dt.date(hire_year, 1, 1), monthly_wage=5_000_000,
            plan=BenefitPlan.DB, employee_type=EmployeeType.STAFF, rules=RateRules(),
        )
        member.age = 37
        member.severance_nra = 60
        return member

    def _config(self):
        import datetime as _dt

        from pension.config import CalculationConfig, JobGroupRule

        return CalculationConfig(
            base_date=_dt.date(2025, 12, 31),
            job_group_rules=[
                JobGroupRule(source_name="정규직", mapped_name="정규직", severance_nra=60)
            ],
        )

    def test_capped_benefit_is_fully_attributed(self) -> None:
        """30년 상한 규정에서 근속 35년이면 더 일해도 급여가 안 는다.

        근속비로 재면 총근속이 늘수록 귀속비율이 줄어 채무가 과소계상된다.
        """
        from pension.assumptions import Assumptions, BenefitScale, DiscountCurve
        from pension.formula import Formula
        from pension.valuation import value_member

        assumptions = Assumptions(discount=DiscountCurve(flat=0.045))
        assumptions.severance_benefit = BenefitScale(
            formulas={"정규직": Formula("=MIN(t,30)")}
        )
        result = value_member(self._member(), self._config(), assumptions)

        # 상한을 이미 넘겼으니 추가 근무로 늘어날 급여가 없다 → 근무원가 0.
        assert result.service_cost == pytest.approx(0.0)
        # 귀속비율이 1.0 이므로 채무는 '기대급여 현가' 전액이어야 한다.
        assert result.dbo == pytest.approx(result.expected_benefit_pv)

    def test_proportional_benefit_matches_service_ratio(self) -> None:
        """법정(근속 비례)에서는 근속비 방식과 결과가 같아야 한다."""
        from pension.assumptions import Assumptions, DiscountCurve
        from pension.valuation import value_member

        assumptions = Assumptions(discount=DiscountCurve(flat=0.045))
        member = self._member(hire_year=2013)
        result = value_member(member, self._config(), assumptions)

        # 귀속비율이 과거근속/총근속과 같으므로 DBO = 기대급여현가 × 그 비율.
        assert 0 < result.dbo < result.expected_benefit_pv
        assert result.service_cost > 0


class TestServiceFractionInProjection:
    """단수 처리는 미래 시점의 근속에도 적용돼야 한다."""

    def test_truncation_lowers_the_obligation(self) -> None:
        import datetime as _dt

        from pension.assumptions import Assumptions, DiscountCurve
        from pension.config import CalculationConfig, JobGroupRule
        from pension.models import ActiveMember, RateRules
        from pension.normalize import BenefitPlan, EmployeeType, Gender
        from pension.valuation import value_member

        config = CalculationConfig(
            base_date=_dt.date(2025, 12, 31),
            job_group_rules=[
                JobGroupRule(source_name="정규직", mapped_name="정규직", severance_nra=60)
            ],
        )
        assumptions = Assumptions(discount=DiscountCurve(flat=0.045))

        def valued(fraction: str):
            member = ActiveMember(
                seq=1, row=1, employee_id="A1", name="홍길동",
                job_group="정규직", job_group_raw="정규직",
                gender=Gender.MALE, birth_date=_dt.date(1988, 1, 1),
                hire_date=_dt.date(2013, 7, 1), monthly_wage=5_000_000,
                plan=BenefitPlan.DB, employee_type=EmployeeType.STAFF,
                rules=RateRules(), service_fraction=fraction,
            )
            member.age = 37
            member.severance_nra = 60
            return value_member(member, config, assumptions)

        keep = valued("그대로")
        cut = valued("절사")

        # 기준일 근속부터 이미 다르다(12.5년 → 12년).
        assert cut.past_service == pytest.approx(12.0)
        assert keep.past_service > cut.past_service
        # 미래 시점에도 계속 깎이므로 채무가 낮아야 한다.
        assert cut.dbo < keep.dbo


class TestRosterFieldsThatWereIgnored:
    """명부에 적혀 오지만 산출이 쓰지 않던 항목들."""

    def test_declared_nra_overrides_the_group_rule(self) -> None:
        """명부에 개인별 정년이 적혀 있으면 직군 규정보다 우선한다."""
        from pension.actuarial import normal_retirement_age
        from pension.config import JobGroupRule

        rule = JobGroupRule(
            source_name="임원", mapped_name="임원",
            severance_nra=60, over_nra_add_age=2,
        )
        assert normal_retirement_age(50, rule) == 60
        assert normal_retirement_age(50, rule, declared_nra=63) == 63
        # 개인 정년을 이미 넘겼으면 그 기준으로 가산연령을 더한다.
        assert normal_retirement_age(64, rule, declared_nra=63) == 66
        # 임금피크가 더 이르면 그쪽이 이긴다.
        assert normal_retirement_age(50, rule, declared_nra=63, wage_peak_age=56) == 56

    def test_hired_after_base_date_leaves_a_trace(self, tmp_path) -> None:
        """기준일 이후 입사자는 빠지되, 왜 빠졌는지 남아야 한다."""
        import datetime as _dt

        from pension.assumptions import Assumptions, DiscountCurve
        from pension.config import CalculationConfig, JobGroupRule
        from pension.models import ActiveMember, RateRules, Roster
        from pension.normalize import BenefitPlan, EmployeeType, Gender
        from pension.valuation import value_roster

        config = CalculationConfig(
            base_date=_dt.date(2025, 12, 31),
            job_group_rules=[
                JobGroupRule(source_name="정규직", mapped_name="정규직", severance_nra=60)
            ],
        )
        future = ActiveMember(
            seq=1, row=1, employee_id="A1", name="내년입사",
            job_group="정규직", job_group_raw="정규직",
            gender=Gender.MALE, birth_date=_dt.date(1990, 1, 1),
            hire_date=_dt.date(2026, 3, 1), monthly_wage=5_000_000,
            plan=BenefitPlan.DB, employee_type=EmployeeType.STAFF, rules=RateRules(),
        )
        future.age = 35
        future.severance_nra = 60

        result = value_roster(Roster(active=[future]), config,
                              Assumptions(discount=DiscountCurve(flat=0.045)))
        assert result.headcount == 0
        assert result.dbo == 0
        assert "입사일이 산출기준일보다 늦음" in result.exclusion_summary()


class TestScaleServiceAdjustment:
    """가산·차감근속연수는 **지급률 근속만** 밀고 당긴다.

    군경력 인정·연단위 절사 같은 규정을 입사일 수정으로 흉내 내면 할당(귀속)
    근속까지 함께 움직여 틀린다. 배수에 상수를 더하는 방식은 누진제로 바뀌는
    순간 어긋난다. 그래서 근속연수 축에서 옮기되, 할당은 실제 근속을 지킨다.
    """

    def _pair(self, config, **fields):
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        plain = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))
        for name, value in fields.items():
            setattr(member, name, value)
        moved = value_member(member, config, make_assumptions(discount=0.05, salary=0.0))
        return plain, moved

    def test_added_years_raise_the_multiple_only(self, config: CalculationConfig) -> None:
        plain, moved = self._pair(config, service_add_years=2.5)
        # 법정(배수=근속)이라 즉시퇴직 지급액은 (근속+2.5)×임금 이어야 한다.
        past = plain.past_service
        assert plain.accrued_benefit == pytest.approx(past * 1_000_000)
        assert moved.accrued_benefit == pytest.approx((past + 2.5) * 1_000_000)
        # 할당(귀속) 근속은 그대로다.
        assert moved.past_service == plain.past_service
        assert moved.dbo > plain.dbo

    def test_deducted_years_lower_the_multiple(self, config: CalculationConfig) -> None:
        plain, moved = self._pair(config, service_deduct_years=1.0)
        assert moved.accrued_benefit == pytest.approx(
            (plain.past_service - 1.0) * 1_000_000)
        assert moved.dbo < plain.dbo

    def test_deduct_beyond_service_floors_at_zero(
        self, config: CalculationConfig
    ) -> None:
        """차감이 근속을 넘어도 배수는 0 에서 멈춘다 — 음수 급여는 없다."""
        _plain, moved = self._pair(config, service_deduct_years=50.0)
        assert moved.accrued_benefit == 0.0
        assert moved.dbo == 0.0


class TestAllocationMethod:
    """직군 규칙의 '할당 방식' — 급여식(기본) / 근속비례.

    참고 산출 시스템의 공식 DBO 는 근속기간할당(B/D × D0)이다. 배수가 근속에
    비례하는 법정 퇴직금에서는 두 방식이 같은 값을 내지만, 상한·가산근속이
    있으면 갈린다. 어느 쪽으로 잴지는 규정이 정한다.
    """

    def _config(self, method: str) -> CalculationConfig:
        return CalculationConfig(
            base_date=BASE_DATE,
            job_group_rules=[JobGroupRule(
                "정규직", "정규직", severance_nra=60, longterm_nra=60,
                allocation_method=method,
            )],
        )

    def _member(self):
        member = make_member(age=50, past_service=10.0, wage=1_000_000, nra=60)
        member.job_group_raw = "정규직"
        return member

    def test_statutory_gives_the_same_answer_either_way(self) -> None:
        formula = value_member(self._member(), self._config(""),
                               make_assumptions(discount=0.05, salary=0.0))
        prorata = value_member(self._member(), self._config("근속비례"),
                               make_assumptions(discount=0.05, salary=0.0))
        assert prorata.dbo == pytest.approx(formula.dbo, rel=1e-9)
        assert prorata.service_cost == pytest.approx(formula.service_cost, rel=1e-9)

    def test_a_capped_scale_splits_the_two_methods(self) -> None:
        """상한 규정에서는 급여식이 더 많이 귀속한다 (문단 70 취지).

        근속 15년 상한이면 과거근속 10년의 급여식 귀속은 10/15 인데,
        근속비례는 10/20 이다. 근속비례를 고르면 그 뜻대로 나와야 한다.
        """
        from pension.assumptions import Formula

        def valued(method: str):
            assumptions = make_assumptions(discount=0.05, salary=0.0)
            assumptions.severance_benefit.formulas["정규직"] = Formula("MIN(t, 15)")
            return value_member(self._member(), self._config(method), assumptions)

        formula, prorata = valued(""), valued("근속비례")
        # 정년 시 총근속 ≈ 20, 급여 = 15 × 임금. 급여식 귀속 10/15 > 근속비 10/20.
        assert prorata.dbo < formula.dbo
        past = prorata.past_service
        total = past + 10
        benefit = 15.0 * 1_000_000
        assert prorata.dbo == pytest.approx(
            benefit * (past / total) / 1.05**10, rel=1e-9)

    def test_the_adjusted_scale_service_matches_the_reference_shape(self) -> None:
        """가산근속 + 근속비례 = 참고 시스템의 모양.

        배수는 (근속+가산)으로 찾고, 귀속은 실제 근속비로 — 참고 워크북
        (B(t)/D(t) × D0, G = D + 가산)과 같은 구조가 되어야 한다.
        """
        member = self._member()
        member.service_add_years = 1.5
        result = value_member(member, self._config("근속비례"),
                              make_assumptions(discount=0.05, salary=0.0))
        past = result.past_service
        total = past + 10
        benefit = (total + 1.5) * 1_000_000          # 배수 = 근속 + 1.5
        expected = benefit * (past / total) / 1.05**10
        assert result.dbo == pytest.approx(expected, rel=1e-9)
