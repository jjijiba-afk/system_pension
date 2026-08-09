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
    LT_AT_MILESTONE,
    LT_AT_NRA,
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
    items = assumptions.longterm_items(rule)
    result.benefit_kind = " + ".join(dict.fromkeys(i.kind for i in items))

    by_item = [(item, assumptions.longterm_benefit.milestones(item.column(rule)))
               for item in items]
    by_item = [(item, points) for item, points in by_item if points]
    if not by_item:
        columns = " / ".join(dict.fromkeys(i.column(rule) for i in items))
        result.excluded_reason = f"장기급여 지급률 규정 '{columns}' 을(를) 찾을 수 없음"
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
        # 직군 규칙에서 끈 가정은 0 으로 둔다. 퇴직급여와 같은 규칙을 쓴다.
        increase = 0.0
        if member.apply_base_up:
            increase += assumptions.salary.base_up.rate(t)
        if member.apply_promotion:
            increase += assumptions.salary.promotion.rate(
                salary_rule, age=age_t, service=service_t
            )
        index *= 1.0 + increase

        withdrawal = min(max(assumptions.withdrawal.rate(
            withdrawal_rule, age=age_t, service=service_t), 0.0), 1.0) \
            if member.apply_withdrawal else 0.0
        mortality = (
            assumptions.mortality.qx(member.gender, age_t)
            if member.apply_mortality else 0.0
        )
        probability *= (1.0 - withdrawal) * (1.0 - mortality)
        survival.append(probability)
        wage_index.append(index)

    def index_at(t: float) -> float:
        """``t`` 년 뒤 임금지수. 연 단위 표를 선형으로 이어 본다."""
        low = min(int(t), horizon)
        high = min(low + 1, horizon)
        return wage_index[low] + (wage_index[high] - wage_index[low]) * (t - low)

    def survival_at(t: float) -> float:
        low = min(int(t), horizon)
        high = min(low + 1, horizon)
        return survival[low] + (survival[high] - survival[low]) * (t - low)

    def amount(item, value: float, t: float) -> float:
        """표 값 하나를 그 시점의 금액으로.

        표 값의 뜻이 지급유형에 따라 달라진다.
          휴가      지급일수  → 일 기본급 × 일수, 임금상승률 반영
          평균임금  배수      → 30일 평균임금 × 배수, 임금상승률 반영
          현물      정액(원)  → 평가시점 시세를 현물 상승률로 올린다
          현금      정액(원)  → 규정 금액이 고정이므로 올리지 않는다
        """
        if item.kind == LT_AVERAGE_WAGE:
            return value * member.monthly_wage * index_at(t)
        if item.kind == LT_IN_KIND:
            return value * (1.0 + item.escalation) ** t
        if item.kind == LT_CASH:
            return value
        return value * daily * index_at(t)

    dbo = 0.0
    service_cost = 0.0
    upcoming = 0

    for item, milestones in by_item:
        points = _expand(milestones, item.every_years, past_service + horizon)
        if item.timing == LT_AT_MILESTONE:
            item_dbo, item_cost, count, first = _value_at_milestones(
                points, item, past_service, horizon, member, config,
                assumptions, amount, survival_at,
            )
            upcoming += count
            if first is not None and (
                result.next_milestone is None or first < result.next_milestone
            ):
                result.next_milestone = first
        else:
            item_dbo, item_cost = _value_at_exit(
                points, item, past_service, horizon, assumptions, amount, survival
            )
        dbo += item_dbo
        service_cost += item_cost

    result.dbo = dbo
    result.service_cost = service_cost
    result.interest_cost = dbo * assumptions.discount.level_rate
    result.milestone_count = upcoming
    return result


def _expand(
    milestones: list[tuple[int, float]], every_years: float, limit: float
) -> list[tuple[float, float]]:
    """반복 주기가 있으면 마지막 지급 시점을 ``limit`` 까지 되풀이한다.

    '30년 넘으면 5년마다 한 번 더', 건강검진처럼 2년마다 되풀이하는 규정을
    표 한 줄로 적을 수 있게 한다.
    """
    points: list[tuple[float, float]] = [(float(k), v) for k, v in milestones]
    if every_years <= 0 or not points:
        return points

    last_service, last_value = points[-1]
    # 표가 이미 채운 자리를 다시 만들지 않도록 마지막 시점 다음부터 센다.
    step = every_years
    service = last_service + step
    while service <= limit and len(points) < 200:
        points.append((service, last_value))
        service += step
    return points


def _anniversary_delay(item, member, config, target_service: float) -> float:
    """근속에 닿은 뒤 지급일까지 밀리는 햇수.

    창립기념일 지급 규정은 근속에 닿아도 그 날짜가 와야 준다. 근속 도달일이
    지급일보다 뒤면 이듬해로 넘어가므로 최대 1년 밀린다.
    """
    if not item.anniversary:
        return 0.0
    start = member.settlement_date or member.hire_date
    if start is None:
        return 0.0
    month, day = (int(part) for part in item.anniversary.split("-"))

    reached = start + _dt.timedelta(days=round(target_service * 365.25))
    try:
        payday = reached.replace(month=month, day=day)
    except ValueError:                       # 2/29 같은 날짜
        payday = reached.replace(month=month, day=28)
    if payday < reached:
        payday = payday.replace(year=payday.year + 1)
    return (payday - reached).days / 365.25


def _value_at_milestones(
    points, item, past_service, horizon, member, config, assumptions, amount, survival_at
):
    """근속에 닿는 해에 받는 급여. 이미 지나간 시점은 채무가 아니다."""
    dbo = 0.0
    service_cost = 0.0
    count = 0
    first: float | None = None

    for target_service, value in points:
        if value <= 0 or target_service <= 0:
            continue
        remaining = target_service - past_service
        if remaining <= 0:
            continue  # 이미 지급이 끝난 시점
        remaining += _anniversary_delay(item, member, config, target_service)
        if remaining > horizon:
            continue  # 정년 전에 도달하지 못한다

        count += 1
        if first is None:
            first = target_service

        benefit = amount(item, value, remaining)
        weighted = (
            benefit * survival_at(remaining)
            * assumptions.discount.discount_factor(remaining)
        )
        dbo += weighted * min(1.0, past_service / target_service)
        service_cost += weighted / target_service

    return dbo, service_cost, count, first


def _value_at_exit(points, item, past_service, horizon, assumptions, amount, survival):
    """나갈 때 받는 급여(``퇴직시`` / ``정년시``).

    재직 중에는 주지 않으므로 지급 시점이 탈퇴 시점이다. 금액은 그때까지 쌓인
    자격으로 정해지고, 귀속은 지금 자격과 그때 자격의 비로 잰다.
    """
    def due(service: float) -> list[tuple[float, float]]:
        """근속 ``service`` 로 나갈 때 받을 ``(도달근속, 표 값)`` 들.

        누적이면 도달한 시점을 모두 받는다('유급휴가 소멸기한 없음').
        아니면 그 근속에 해당하는 한 칸만 받는다('20년 이상이면 순금 8돈').
        """
        reached = [(target, v) for target, v in points if target <= service and v > 0]
        if not reached:
            return []
        return reached if item.accumulate else reached[-1:]

    dbo = 0.0
    service_cost = 0.0
    in_service = 1.0            # 그 해 **초** 에 재직해 있을 확률

    for t in range(1, horizon + 1):
        is_final = t == horizon
        # 정년퇴직자에게만 주는 급여면 중도 탈퇴자는 받지 못한다.
        if item.timing == LT_AT_NRA and not is_final:
            in_service = survival[t]
            continue

        timing = float(t) if is_final else t - 0.5
        leaving = in_service if is_final else max(0.0, in_service - survival[t])
        in_service = survival[t]
        if leaving <= 0.0:
            continue

        discount = assumptions.discount.discount_factor(timing)
        for target, value in due(past_service + timing):
            # 귀속은 그 급여를 벌게 한 근무기간(0 → 도달근속)에 고르게 나눈다
            # (문단 72). 도달하는 해에 통째로 잡으면 그때까지 채무가 0 이다가
            # 한 번에 튄다.
            share = min(1.0, past_service / target) if target > 0 else 1.0
            next_share = min(1.0, (past_service + 1.0) / target) if target > 0 else 1.0
            weighted = amount(item, value, timing) * leaving * discount
            dbo += weighted * share
            service_cost += weighted * max(0.0, next_share - share)

    return dbo, service_cost


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
