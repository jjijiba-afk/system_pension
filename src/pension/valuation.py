"""예측단위적립방식(PUC)에 의한 확정급여채무 산출.

K-IFRS 1019호 '종업원급여' 가 요구하는 예측단위적립방식(Projected Unit Credit)
으로 확정급여채무(DBO), 당기근무원가, 이자원가를 계산한다.

산출 구조
---------
재직자 한 명에 대해 산출기준일부터 정년까지 1년 단위로 미래를 투영한다.
각 연도 ``t`` 마다

* **임금 투영** — 30일 평균임금에 Base-up 과 승급률을 복리로 곱한다.
* **탈퇴 확률** — 중도퇴직률 ``w`` 와 사망률 ``q`` 를 함께 적용하고
  (``1 - (1-w)(1-q)``), 탈퇴는 연중앙(``t - 0.5``)에 일어난 것으로 본다.
  정년까지 남은 사람은 마지막 시점에 전원 퇴직한다.
* **급여 산정** — ``지급률(총근속) × 투영임금``.
* **귀속** — PUC 이므로 급여 중 기준일까지의 근속에 해당하는 몫만 부채로 잡는다
  (``과거근속 / 총근속``).

이를 확률과 현가계수로 가중합하면 DBO 가 된다::

    DBO = Σ_t  급여(t) × (과거근속 / 총근속(t)) × 탈퇴확률(t) × v(t)

당기근무원가는 같은 식에서 귀속비율만 "1년치"(``1 / 총근속``)로 바꾼 값이다.

중간정산·전입 처리
------------------
근속연수는 중간정산일(있으면)부터 센다. 종전 규칙 가 중간정산일을 입사일로 갈음해
채워 두므로 :attr:`~pension.models.ActiveMember.settlement_date` 하나만 보면 된다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .assumptions import Assumptions
from .config import CalculationConfig
from .models import ActiveMember, Roster
from .normalize import BenefitPlan

__all__ = [
    "MemberValuation",
    "ValuationResult",
    "value_member",
    "value_roster",
]


@dataclass(slots=True)
class MemberValuation:
    """개인별 산출 결과."""

    employee_id: str
    name: str
    job_group: str
    gender: str
    age: int
    past_service: float
    """기준일까지의 근속연수(가산·차감 반영)."""
    projection_years: int
    """정년까지 남은 투영 연수."""
    monthly_wage: float

    dbo: float = 0.0
    """확정급여채무."""
    service_cost: float = 0.0
    """당기근무원가."""
    interest_cost: float = 0.0
    """차기 이자원가(= DBO × 할인율)."""
    accrued_benefit: float = 0.0
    """기준일 현재 퇴직 시 지급액(퇴직급여추계액). 명부 값이 아니라 지급률로 재계산한 값."""
    expected_benefit_pv: float = 0.0
    """미래 급여의 총 현가(귀속 전). 부채비율 점검용."""
    duration: float = 0.0
    """가중평균 잔존만기(년). 할인율 회사채 만기 선택 근거로 쓴다."""

    excluded_reason: str = ""
    """산출 대상에서 뺀 이유. 비어 있으면 정상 산출."""

    @property
    def funded_ratio_base(self) -> float:
        """추계액 대비 DBO 비율. 이상치 점검용."""
        return self.dbo / self.accrued_benefit if self.accrued_benefit else 0.0


@dataclass(slots=True)
class ValuationResult:
    """명부 전체 산출 결과."""

    base_date: _dt.date
    label: str
    members: list[MemberValuation] = field(default_factory=list)

    @property
    def dbo(self) -> float:
        return sum(m.dbo for m in self.members)

    @property
    def service_cost(self) -> float:
        return sum(m.service_cost for m in self.members)

    @property
    def interest_cost(self) -> float:
        return sum(m.interest_cost for m in self.members)

    @property
    def accrued_benefit(self) -> float:
        """퇴직급여추계액 합계(지급률 기준 재계산)."""
        return sum(m.accrued_benefit for m in self.members)

    @property
    def headcount(self) -> int:
        return sum(1 for m in self.members if not m.excluded_reason)

    @property
    def duration(self) -> float:
        """DBO 가중평균 잔존만기."""
        total = self.dbo
        if not total:
            return 0.0
        return sum(m.dbo * m.duration for m in self.members) / total

    def by_job_group(self) -> dict[str, tuple[int, float, float]]:
        """직군별 (인원, DBO, 당기근무원가)."""
        buckets: dict[str, list[float]] = {}
        for m in self.members:
            if m.excluded_reason:
                continue
            row = buckets.setdefault(m.job_group or "(미분류)", [0.0, 0.0, 0.0])
            row[0] += 1
            row[1] += m.dbo
            row[2] += m.service_cost
        return {k: (int(v[0]), v[1], v[2]) for k, v in sorted(buckets.items())}


def _projection_years(member: ActiveMember, assumptions: Assumptions) -> int:
    """정년까지 남은 연수. 최소 1년은 투영한다."""
    remaining = member.severance_nra - member.age
    return max(1, min(remaining, assumptions.max_projection_years))


def value_member(
    member: ActiveMember,
    config: CalculationConfig,
    assumptions: Assumptions,
) -> MemberValuation:
    """재직자 한 명의 DBO·근무원가·이자원가를 계산한다.

    DC 가입자는 확정기여제도이므로 확정급여채무가 생기지 않는다. 부담금 납입으로
    의무가 끝나기 때문이며, 결과에는 ``excluded_reason`` 을 달아 남긴다.
    """
    result = MemberValuation(
        employee_id=member.employee_id,
        name=member.name,
        job_group=member.job_group,
        gender=member.gender.value,
        age=member.age,
        past_service=member.service_years(config.base_date),
        projection_years=0,
        monthly_wage=member.monthly_wage,
    )

    if member.plan is BenefitPlan.DC:
        result.excluded_reason = "DC 가입자 (확정기여제도는 확정급여채무 없음)"
        return result
    if member.birth_date is None or member.hire_date is None:
        result.excluded_reason = "생년월일 또는 입사일자 누락"
        return result
    if member.monthly_wage <= 0:
        result.excluded_reason = "30일 평균임금 없음"
        return result

    rule = member.rules.severance_benefit
    withdrawal_rule = member.rules.severance_withdrawal
    salary_rule = member.rules.severance_salary_increase

    past_service = result.past_service
    years = _projection_years(member, assumptions)
    result.projection_years = years

    # 기준일 현재 즉시 퇴직 시 지급액. 귀속비율 1.0 에 해당한다.
    result.accrued_benefit = (
        assumptions.severance_benefit.multiple(rule, past_service) * member.monthly_wage
    )

    survival = 1.0  # 기준일부터 t년 초까지 재직해 있을 확률
    wage = member.monthly_wage
    dbo = 0.0
    service_cost = 0.0
    benefit_pv = 0.0
    weighted_time = 0.0

    for t in range(1, years + 1):
        age_t = member.age + t - 1
        service_t = past_service + t - 1

        # 임금은 해당 연도 초에 인상된다고 본다.
        wage *= 1.0 + assumptions.salary.rate(
            salary_rule, year=t, age=age_t, service=service_t
        )

        withdrawal = assumptions.withdrawal.rate(
            withdrawal_rule, age=age_t, service=service_t
        )
        mortality = assumptions.mortality.qx(member.gender, age_t)
        withdrawal = min(max(withdrawal, 0.0), 1.0)

        is_final = t == years
        if is_final:
            # 정년 도달자는 전원 퇴직한다.
            exit_probability = survival
            timing = float(t)
            total_service = past_service + t
        else:
            exit_probability = survival * (1.0 - (1.0 - withdrawal) * (1.0 - mortality))
            timing = t - 0.5
            total_service = past_service + t - 0.5

        if exit_probability > 0.0:
            benefit = assumptions.severance_benefit.multiple(rule, total_service) * wage
            discount = assumptions.discount.discount_factor(timing)
            attribution = min(1.0, past_service / total_service) if total_service > 0 else 0.0
            # 근무원가는 "1년치 근속이 더 쌓이는 몫". 총근속이 0 이면 귀속할 것이 없다.
            unit_attribution = (1.0 / total_service) if total_service > 0 else 0.0

            weighted = benefit * exit_probability * discount
            dbo += weighted * attribution
            service_cost += weighted * unit_attribution
            benefit_pv += weighted
            weighted_time += weighted * attribution * timing

        survival *= (1.0 - withdrawal) * (1.0 - mortality)
        if survival <= 0.0:
            # 전원 탈퇴했으므로 이후 연도는 기여할 것이 없다.
            break

    result.dbo = dbo
    result.service_cost = service_cost
    result.expected_benefit_pv = benefit_pv
    result.interest_cost = dbo * assumptions.discount.level_rate
    result.duration = (weighted_time / dbo) if dbo else 0.0
    return result


def value_roster(
    roster: Roster,
    config: CalculationConfig,
    assumptions: Assumptions,
) -> ValuationResult:
    """재직자 전원의 퇴직급여 확정급여채무를 산출한다."""
    result = ValuationResult(base_date=config.base_date, label=assumptions.label)
    for member in roster.active:
        if member.hire_date is not None and member.hire_date > config.base_date:
            continue  # 기준일 이후 입사자는 산출 대상이 아니다.
        result.members.append(value_member(member, config, assumptions))
    return result
