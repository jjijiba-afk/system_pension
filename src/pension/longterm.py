"""기타장기종업원급여(장기근속 포상·휴가) 채무 산출.

퇴직급여와 달리 **재직 중 특정 근속연수에 도달하면** 지급되는 급여다. 그래서
투영 구조가 다르다.

* 급여 시점은 탈퇴 시점이 아니라 **근속 도달 시점** 이다. 지급률 표의 각 행
  (근속 10년 → 10일, 20년 → 20일 …)이 하나의 지급 시점이 된다.
* 그 시점까지 **재직해 있어야** 받으므로 생존확률(재직확률)만 곱한다.
* 귀속은 도달 근속연수에 대한 기준일까지의 근속 비율(``과거근속 / 도달근속``)로
  본다. 이미 지나간 지급 시점은 채무가 아니다(지급이 끝났으므로).

K-IFRS 1019호는 기타장기종업원급여의 보험수리적손익을 즉시 당기손익으로
인식하도록 하므로, 퇴직급여와 분리해 산출한다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .assumptions import (
    LT_AVERAGE_WAGE,
    LT_CASH,
    LT_IN_KIND,
    Assumptions,
)
from .config import CalculationConfig
from .models import ActiveMember, Roster

__all__ = ["LongTermMemberValuation", "LongTermResult", "value_longterm", "value_longterm_member"]


@dataclass(slots=True)
class LongTermMemberValuation:
    """개인별 장기급여 산출 결과."""

    employee_id: str
    name: str
    job_group: str
    age: int
    past_service: float
    daily_base_pay: float

    dbo: float = 0.0
    service_cost: float = 0.0
    interest_cost: float = 0.0
    benefit_kind: str = ""
    """적용한 지급유형(휴가/평균임금/현물/현금)."""
    next_milestone: int | None = None
    """다음 지급 근속연수. 남은 지급 시점이 없으면 ``None``."""
    milestone_count: int = 0
    """앞으로 도달 가능한 지급 시점 수."""
    excluded_reason: str = ""


@dataclass(slots=True)
class LongTermResult:
    base_date: _dt.date
    label: str
    members: list[LongTermMemberValuation] = field(default_factory=list)

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
    def headcount(self) -> int:
        return sum(1 for m in self.members if not m.excluded_reason)


def value_longterm_member(
    member: ActiveMember,
    config: CalculationConfig,
    assumptions: Assumptions,
) -> LongTermMemberValuation:
    """재직자 한 명의 장기종업원급여 채무를 계산한다."""
    daily = member.effective_daily_base_pay()
    result = LongTermMemberValuation(
        employee_id=member.employee_id,
        name=member.name,
        job_group=member.job_group,
        age=member.age,
        past_service=member.service_years(config.base_date),
        daily_base_pay=daily,
    )

    if member.longterm_target != "Y":
        result.excluded_reason = "장기급여 산출대상 아님 (명부 'N')"
        return result
    if member.birth_date is None or member.hire_date is None:
        result.excluded_reason = "생년월일 또는 입사일자 누락"
        return result

    rule = member.rules.longterm_benefit or member.job_group
    kind = assumptions.longterm_rule(rule)
    result.benefit_kind = kind.kind
    milestones = assumptions.longterm_benefit.milestones(rule)
    if not milestones:
        result.excluded_reason = f"장기급여 지급률 규정 '{rule}' 을(를) 찾을 수 없음"
        return result

    past_service = result.past_service
    # 장기급여는 자체 정년연령을 쓴다.
    horizon = max(1, min(member.longterm_nra - member.age, assumptions.max_projection_years))

    withdrawal_rule = member.rules.longterm_withdrawal or member.job_group
    salary_rule = member.rules.longterm_salary_increase or member.job_group

    # 연도별 재직확률과 임금지수를 미리 만들어 둔다(t = 0 은 기준일).
    survival = [1.0]
    wage_index = [1.0]
    probability = 1.0
    index = 1.0
    for t in range(1, horizon + 1):
        age_t = member.age + t - 1
        service_t = past_service + t - 1
        index *= 1.0 + assumptions.salary.rate(
            salary_rule, year=t, age=age_t, service=service_t
        )
        withdrawal = min(max(assumptions.withdrawal.rate(
            withdrawal_rule, age=age_t, service=service_t), 0.0), 1.0)
        mortality = assumptions.mortality.qx(member.gender, age_t)
        probability *= (1.0 - withdrawal) * (1.0 - mortality)
        survival.append(probability)
        wage_index.append(index)

    dbo = 0.0
    service_cost = 0.0
    upcoming = 0

    for target_service, days in milestones:
        if days <= 0:
            continue
        remaining = target_service - past_service
        if remaining <= 0:
            continue  # 이미 지급이 끝난 시점
        t = int(-(-remaining // 1))  # 도달 연도(올림)
        if t > horizon:
            continue  # 정년 전에 도달하지 못한다

        upcoming += 1
        if result.next_milestone is None:
            result.next_milestone = target_service

        # 표 값의 뜻이 지급유형에 따라 달라진다.
        #   휴가      지급일수  → 일 기본급 × 일수, 임금상승률 반영
        #   평균임금  배수      → 30일 평균임금 × 배수, 임금상승률 반영
        #   현물      정액(원)  → 평가시점 시세를 현물 상승률로 올린다
        #   현금      정액(원)  → 규정 금액이 고정이므로 올리지 않는다
        if kind.kind == LT_AVERAGE_WAGE:
            benefit = days * member.monthly_wage * wage_index[t]
        elif kind.kind == LT_IN_KIND:
            benefit = days * (1.0 + kind.escalation) ** t
        elif kind.kind == LT_CASH:
            benefit = days
        else:
            benefit = days * daily * wage_index[t]

        weighted = benefit * survival[t] * assumptions.discount.discount_factor(remaining)

        attribution = min(1.0, past_service / target_service) if target_service > 0 else 0.0
        unit_attribution = 1.0 / target_service if target_service > 0 else 0.0

        dbo += weighted * attribution
        service_cost += weighted * unit_attribution

    result.dbo = dbo
    result.service_cost = service_cost
    result.interest_cost = dbo * assumptions.discount.level_rate
    result.milestone_count = upcoming
    return result


def value_longterm(
    roster: Roster,
    config: CalculationConfig,
    assumptions: Assumptions,
) -> LongTermResult:
    """재직자 전원의 기타장기종업원급여 채무를 산출한다."""
    result = LongTermResult(base_date=config.base_date, label=assumptions.label)
    for member in roster.active:
        if member.hire_date is not None and member.hire_date > config.base_date:
            continue
        result.members.append(value_longterm_member(member, config, assumptions))
    return result
