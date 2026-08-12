"""예측단위적립방식(PUC)에 의한 확정급여채무 산출.

K-IFRS 1019호 '종업원급여' 가 요구하는 예측단위적립방식(Projected Unit Credit)
으로 확정급여채무(DBO), 당기근무원가, 이자원가를 계산한다.

산출 구조
---------
재직자 한 명에 대해 산출기준일부터 정년까지 1년 단위로 미래를 투영한다.
각 연도 ``t`` 마다

* **임금 투영** — 30일 평균임금에 Base-up 과 승급률을 복리로 곱한다.
* **탈퇴 확률** — 중도퇴직률 ``w`` 와 사망률 ``q`` 를 함께 적용하되
  (합계는 ``1 - (1-w)(1-q)``) **사유별로 갈라 둔다**. 회사 규정이 중도퇴직·
  사망·정년퇴직에 다른 지급률을 주는 일이 흔해서, 뭉뚱그리면 어느 규정을
  적용할지 정할 수 없다. 탈퇴는 연중앙(``t - 0.5``)에 일어난 것으로 보고,
  정년까지 남은 사람은 마지막 시점에 전원 퇴직한다.
* **급여 산정** — ``지급률(총근속) × 투영임금``.
* **귀속** — PUC 이므로 급여 중 기준일까지의 근속에 해당하는 몫만 부채로 잡는다
  (``과거근속 / 총근속``).

이를 확률과 현가계수로 가중합하면 DBO 가 된다::

    DBO = Σ_t  급여(t) × (과거근속 / 총근속(t)) × 탈퇴확률(t) × v(t)

당기근무원가는 같은 식에서 귀속비율만 "1년치"(``1 / 총근속``)로 바꾼 값이다.

중간정산·전입 처리
------------------
근속연수는 중간정산일(있으면)부터 센다. 읽는 단계에서 중간정산일이 비면
입사일로 채워 두므로 :attr:`~pension.models.ActiveMember.settlement_date`
하나만 보면 된다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Final

from .actuarial import FRACTION_HALF, apply_fraction, round_amount
from .assumptions import (
    ATTRIB_IMMEDIATE,
    CAUSE_DEATH,
    CAUSE_NORMAL,
    CAUSE_VOLUNTARY,
    Assumptions,
    CauseBenefit,
)
from .config import CalculationConfig
from .models import ActiveMember, Roster
from .normalize import BenefitPlan, text

#: 사유별 규정이 없을 때 쓰는 빈 규정. 기본 지급률을 그대로 쓴다는 뜻이다.
_NO_CAUSE: Final = CauseBenefit()

__all__ = [
    "MemberValuation",
    "ValuationResult",
    "single_equivalent_rate",
    "value_member",
    "value_roster",
]


def single_equivalent_rate(
    cash_flows: dict[float, float], target_pv: float, *, tolerance: float = 1e-10
) -> float:
    """``target_pv`` 와 같은 현재가치를 내는 단일 이자율(수익률곡선기법).

    ``Σ CF_t / (1+r)^t = target_pv`` 를 ``r`` 에 대해 푼다. 현재가치는 이자율에
    대해 단조감소하므로 이분법이면 충분하고, 뉴턴법처럼 발산할 여지가 없다.

    현금흐름이 모두 양수이므로 해가 하나뿐이라는 것도 보장된다. 다만 채무가
    0 이거나(전원 DC) 현금흐름이 없으면 정의되지 않으므로 0 을 돌려준다.
    """
    flows = [(t, amount) for t, amount in cash_flows.items() if amount and t > 0]
    if not flows or target_pv <= 0:
        return 0.0

    def present_value(rate: float) -> float:
        return sum(amount / (1.0 + rate) ** t for t, amount in flows)

    low, high = -0.99, 1.0
    # 목표 현가가 구간 밖이면 풀 수 없다(이론상 나오지 않지만 방어한다).
    if present_value(high) > target_pv or present_value(low) < target_pv:
        return 0.0

    for _ in range(200):
        mid = (low + high) / 2
        if present_value(mid) > target_pv:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return (low + high) / 2


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

    # ── 명부에서 그대로 옮겨 오는 항목 ────────────────────────────
    # 개인별 결과만 따로 받아 원가배분·검산에 쓰는 경우가 많아, 명부를 다시 열지
    # 않아도 되도록 함께 싣는다.
    employee_type: str = ""
    cost_code: str = ""
    """원가구분코드(제조원가/판관비 등). 부서·원가 단위 배분에 쓴다."""
    plan: str = ""
    """퇴직급여 제도구분."""
    birth_date: _dt.date | None = None
    hire_date: _dt.date | None = None
    settlement_date: _dt.date | None = None
    """중간정산일(의 익일). 근속 기산일이다."""
    retirement_age: int = 0
    """적용한 퇴직급여 정년연령."""
    benefit_rule: str = ""
    """적용한 지급률 규정명."""
    withdrawal_rule: str = ""
    """적용한 중도(사망)퇴직률 규정명."""
    progressive_service: float = 0.0
    """누진 배수를 보전한 구간의 근속연수. 0 이면 구간을 나누지 않았다."""
    progressive_rate: float = 0.0
    """그 구간에 적용한 연 배수."""

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
    cash_flows: dict[float, float] = field(default_factory=dict)
    """``{지급시점(년): 기대 급여지급액}``. **할인 전** 금액이다.

    단일할인율을 역산할 때 쓴다(IAS 19.85). 곡선으로 할인한 채무와 같은 값을
    내는 하나의 이자율을 찾으려면 시점별 현금흐름이 있어야 한다.
    """
    benefit_flows: dict[float, float] = field(default_factory=dict)
    """``{지급시점(년): 기대 지급총액}``. 할인 전, **귀속 전** 금액이다.

    :attr:`cash_flows` 는 기준일까지 귀속된 몫만 담지만(가득반영), 이것은 그때
    실제로 나갈 돈 전체다. 만기분석 공시(문단 147(c))의 '퇴직급여 지급 예상액'
    열이 이 값이다.
    """

    min_service_years: float = 0.0
    """적용한 가입자격(최소 근속연수). 0 이면 제한 없음."""
    rounding_unit: int = 0
    """적용한 지급액 반올림 단위(원). 0 이면 반올림하지 않았다."""
    extra_payment: float = 0.0
    """명부에 적혀 온 추가지급 기본급(원). **산출에 더해진 값이 아니다.**

    회사마다 이 칸에 담는 것이 달라(사망 위로금·유족 일시금·명퇴 가산금)
    엔진이 스스로 얹지 않는다. 실제로 얹히는 가산은 [퇴직사유] 표의 가산액이다.
    적혀 온 값을 결과에 남겨 두는 것은, 규정에 옮겨 적었는지 대조할 자리가
    있어야 하기 때문이다.
    """
    db_ratio: float = 1.0
    """적용한 DB 비중. 혼합형이 아니면 1 이다.

    1 이 아니면 이 사람의 급여가 그만큼만 채무로 잡혔다는 뜻이다. 결과만 보고는
    왜 절반인지 알 수 없으므로 적용값을 남긴다.
    """

    by_cause: dict[str, dict[str, float]] = field(default_factory=dict)
    """퇴직사유별 몫 — ``{사유: {"dbo": …, "service_cost": …, "benefit_pv": …}}``.

    사유마다 지급률이 다른 규정에서는 합계만으로는 검산이 안 된다. 어느 사유가
    채무를 얼마나 만들었는지 보이지 않으면, 지급률 한 칸을 잘못 넣어도 총액이
    조금 움직일 뿐이라 알아채지 못한다. DBO 산출표를 사유별로 나눠 두는 이유가
    이것이다.
    """

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

    def by_cause(self) -> dict[str, dict[str, float]]:
        """퇴직사유별 채무·근무원가 합계.

        ``{사유: {"dbo": …, "service_cost": …, "benefit_pv": …, "n": 인원}}``.
        ``dbo`` 합은 :attr:`dbo` 와 같다 — 사유를 나눈 것이지 다시 계산한 것이
        아니다. 사유마다 지급률이 다른 규정에서 지급률 한 칸을 잘못 넣으면
        총액은 조금 움직일 뿐이라, 사유별로 갈라 놓아야 눈에 띈다.
        """
        found: dict[str, dict[str, float]] = {}
        for member in self.members:
            for cause, share in member.by_cause.items():
                into = found.setdefault(
                    cause, {"dbo": 0.0, "service_cost": 0.0, "benefit_pv": 0.0, "n": 0})
                into["dbo"] += share["dbo"]
                into["service_cost"] += share["service_cost"]
                into["benefit_pv"] += share["benefit_pv"]
                into["n"] += 1
        # 정년 → 중도 → 사망 차례. 산출표를 볼 때 늘 이 순서로 읽는다.
        order = {CAUSE_NORMAL: 0, CAUSE_VOLUNTARY: 1, CAUSE_DEATH: 2}
        return dict(sorted(found.items(), key=lambda kv: (order.get(kv[0], 9), kv[0])))

    def cash_flows(self) -> dict[float, float]:
        """전체 기대 급여지급액을 시점별로 합친다. 할인 전 금액이다."""
        total: dict[float, float] = {}
        for m in self.members:
            for timing, amount in m.cash_flows.items():
                total[timing] = total.get(timing, 0.0) + amount
        return dict(sorted(total.items()))

    def benefit_cash_flows(self) -> dict[float, float]:
        """시점별 기대 지급총액(할인 전, 귀속 전). 만기분석 공시용이다."""
        total: dict[float, float] = {}
        for m in self.members:
            for timing, amount in m.benefit_flows.items():
                total[timing] = total.get(timing, 0.0) + amount
        return dict(sorted(total.items()))

    def single_discount_rate(self) -> float:
        """수익률곡선기법으로 역산한 **단일할인율**.

        곡선으로 할인한 채무와 **같은 현재가치** 를 내는 하나의 이자율이다.
        K-IFRS 1019 문단 85 는 "급여지급의 예상 시기와 금액을 반영하는 단일
        가중평균 할인율" 을 쓸 수 있다고 하는데, 그 단일 이자율이 이것이다.

        만기별로 다른 이자율로 할인해 놓고 주석에는 하나만 적어야 하므로,
        임의로 한 만기의 이자율을 고르는 대신 채무 자체가 정하게 한다.
        """
        return single_equivalent_rate(self.cash_flows(), self.dbo)

    def exclusion_summary(self) -> dict[str, int]:
        """산출에서 빠진 사유별 인원.

        DC 전환을 마친 회사는 확정급여채무가 0 으로 나오는 것이 정상이다. 그때
        이유를 함께 보여 주지 않으면 산출이 잘못된 것으로 오해하기 쉽다.
        """
        counts: dict[str, int] = {}
        for m in self.members:
            if m.excluded_reason:
                counts[m.excluded_reason] = counts.get(m.excluded_reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

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


def _service_window(member: ActiveMember) -> tuple[float, float]:
    """이 줄이 담당하는 지급 구간을 **근속연수 눈금** 으로 옮긴다.

    구간이 없으면 ``(0, 무한)`` 이라 종전과 같다. 날짜를 근속 눈금으로 바꾸는
    이유는, 이후 계산이 전부 '기산일로부터 몇 년' 으로 돌기 때문이다.
    """
    if not member.has_period:
        return 0.0, float("inf")

    start = member.settlement_date or member.hire_date
    if start is None:
        return 0.0, float("inf")

    def years_at(when: _dt.date | None, default: float) -> float:
        if when is None:
            return default
        # 기준일 기준 근속에서, 그 날짜까지 남은/지난 햇수를 뺀다. 근속 산정은
        # 회사 규칙(일할/월할)을 따르므로 같은 자로 재야 눈금이 맞는다.
        if when <= start:
            return 0.0
        return member.raw_service_years(when)

    window_start = years_at(member.period_start, 0.0)
    # 종료일은 **그 날까지 포함** 이다. 다음 줄이 이튿날부터 시작하므로, 하루를
    # 더해 두어야 두 구간이 빈틈없이 이어진다.
    last_day = member.period_end + _dt.timedelta(days=1) if member.period_end else None
    window_end = years_at(last_day, float("inf"))
    if window_end < window_start:
        return 0.0, 0.0
    return window_start, window_end


def _projection_years(member: ActiveMember, assumptions: Assumptions) -> int:
    """정년까지 남은 연수. 최소 1년은 투영한다."""
    remaining = member.severance_nra - member.age
    return max(1, min(remaining, assumptions.max_projection_years))


def value_member(
    member: ActiveMember,
    config: CalculationConfig,
    assumptions: Assumptions,
    trace: list[dict] | None = None,
) -> MemberValuation:
    """재직자 한 명의 DBO·근무원가·이자원가를 계산한다.

    DC 가입자는 확정기여제도이므로 확정급여채무가 생기지 않는다. 부담금 납입으로
    의무가 끝나기 때문이며, 결과에는 ``excluded_reason`` 을 달아 남긴다.

    :param trace: 리스트를 주면 연차·퇴직사유별 계산 근거가 한 줄씩 담긴다.
        감사인이 한 사람의 채무를 손으로 재계산할 수 있는 수준의 상세다.
        결과 숫자에는 어떤 영향도 없다.
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
        employee_type=member.employee_type.value,
        cost_code=member.cost_code,
        plan=member.plan.value if member.plan else "",
        birth_date=member.birth_date,
        hire_date=member.hire_date,
        settlement_date=member.settlement_date,
        retirement_age=member.severance_nra,
        benefit_rule=member.rules.severance_benefit,
        withdrawal_rule=member.rules.severance_withdrawal,
        progressive_service=max(0.0, member.progressive_service),
        progressive_rate=member.progressive_rate,
    )

    if member.excluded_group:
        result.excluded_reason = f"'{member.job_group}' 직군은 퇴직급여 대상이 아님 (규정상 제외)"
        return result
    if member.plan is BenefitPlan.DC:
        result.excluded_reason = "DC 가입자 (확정기여제도는 확정급여채무 없음)"
        return result
    if member.birth_date is None or member.hire_date is None:
        result.excluded_reason = "생년월일 또는 입사일자 누락"
        return result
    if member.monthly_wage <= 0:
        result.excluded_reason = "30일 평균임금 없음"
        return result

    rule = member.rules.severance_benefit or member.job_group
    withdrawal_rule = member.rules.severance_withdrawal or member.job_group
    salary_rule = member.rules.severance_salary_increase or member.job_group

    years = _projection_years(member, assumptions)
    result.projection_years = years

    # 미래 시점의 근속에도 회사의 단수 처리 규칙을 다시 적용해야 한다.
    # 기준일 근속에만 절사하고 이후로는 소수 근속을 그대로 더하면, '1년 미만
    # 단수는 버린다' 는 회사에서 미래 급여가 반년치가량 부풀어 오른다.
    # 단수 처리 전 원래 근속을 따로 들고 있다가 시점마다 다시 깎는다.
    raw_past_service = member.raw_service_years(config.base_date)

    # ── 지급 구간 ────────────────────────────────────────────────
    # 같은 사번이 여러 줄로 나뉘어 오는 명부가 있다(임원 세법한도의 2019/2020
    # 분할 등). 이 줄이 담당하는 구간만 세고, 나머지는 다른 줄이 센다.
    window_start, window_end = _service_window(member)
    closed = window_end <= raw_past_service

    def covered(raw_service: float) -> float:
        """총근속 ``raw_service`` 중 이 줄이 담당하는 몫."""
        return max(0.0, min(raw_service, window_end) - window_start)

    past_service = apply_fraction(covered(raw_past_service), member.service_fraction)
    result.past_service = past_service

    def service_at(elapsed: float) -> float:
        """기준일로부터 ``elapsed`` 년 뒤 시점의 근속(단수 처리 반영)."""
        return apply_fraction(covered(raw_past_service + elapsed), member.service_fraction)

    # 수식 방식 지급률 규정이 참조하는 변수들. 표 방식이면 무시된다.
    context = {
        "N": float(member.severance_nra),
        "S": member.monthly_wage,
        "직군": member.job_group,
        "제도": member.plan.value if member.plan else "",
        "임직원": member.employee_type.value,
        "배수": member.payout_multiple,
        "추가급": max(0.0, member.extra_pay_base_wage),
    }

    minimum = member.min_service_years
    result.min_service_years = minimum

    # 지급액 반올림 규칙(예: 10원 단위). 직군 규칙에서 받아 온다.
    rounding_unit = 0
    rounding_mode = FRACTION_HALF
    found = config.find_job_group(
        member.job_group_raw, member.employee_type.value, member.employee_type_raw
    )
    if found is not None:
        rounding_unit = found[1].benefit_rounding_unit
        rounding_mode = found[1].benefit_rounding_mode
    result.rounding_unit = rounding_unit

    # 명부의 추가지급 기본급과 개인 지급배수는 **엔진이 자동으로 얹지 않는다.**
    # 회사마다 그 칸에 담는 것이 다르기 때문이다 — 사람마다 다른 위로금인 곳도
    # 있고, 전원에게 같은 한도를 적어 두는 곳도 있고, 누적 지급배수를 적어 두는
    # 곳도 있다. 자동으로 더하거나 곱하면 뒤의 두 경우에서 채무가 통째로 틀린다.
    #
    # 대신 **지급률 규정이 읽어 쓴다.** 수식 방식에서 `추가급`·`배수` 로 꺼내
    # 쓰거나, [퇴직사유] 표의 가산 규정·가산액으로 건다. 무엇을 어떻게 얹을지는
    # 규정이 정하는 일이지 명부 칸이 정할 일이 아니다.
    extra_payment = max(0.0, member.extra_pay_base_wage)
    result.extra_payment = extra_payment

    # 혼합형(DC 일부 + DB 일부)의 DB 몫. `DC 1% / DB 99%` 면 0.99 다.
    # DC 로 나간 몫은 낸 순간 회사 손을 떠나므로 확정급여채무가 아니다. 규정이
    # 내는 급여에만 걸고, 위로금 같은 정액 추가지급에는 걸지 않는다 — 제도
    # 분할과 무관하게 전액을 회사가 주기 때문이다.
    db_share = member.db_ratio if 0 < member.db_ratio <= 1 else 1.0
    result.db_ratio = db_share

    causes = assumptions.exit_causes

    # 누진(호봉)제를 쓰다가 연봉제로 바꾼 회사는 전환 전 근속분의 누진 배수를
    # 보전해 준다. 중간정산을 하지 않았으니 근속은 이어지고 **배수만** 구간에서
    # 갈린다. 명부의 '누진적용 근속연수 / 누진적용 율' 이 그 구간을 말해 준다.
    frozen_service = max(0.0, member.progressive_service)
    frozen_rate = member.progressive_rate
    split_benefit = frozen_service > 0.0 and frozen_rate > 0.0

    def multiple_at(service: float, age: float, rule_name: str = "") -> float:
        """근속 ``service`` 년까지 쌓인 지급배수. 가입자격 문턱은 보지 않는다.

        귀속비율을 재는 자다. 가입자격을 못 채운 구간을 0 으로 깎으면 요건 직전
        직원의 채무가 통째로 0 이 되는데, 그것은 틀리다 — 문단 72 는 급여를 받게
        되는 근무가 **시작된 때** 부터 귀속하라고 한다. 요건 미달로 못 받는 것은
        그 시나리오의 급여액이 0 이 되는 것으로 이미 반영된다.
        """
        name = rule_name or rule
        if not split_benefit:
            return assumptions.severance_benefit.multiple(
                name, service, x=age, **context
            )

        # 누진 구간까지는 보전 배수로, 그 뒤는 규정대로 이어 쌓는다.
        if service <= frozen_service:
            return service * frozen_rate
        scale = assumptions.severance_benefit
        after = scale.multiple(name, service, x=age, **context) - scale.multiple(
            name, frozen_service, x=age, **context
        )
        return frozen_service * frozen_rate + max(0.0, after)

    def parts_at(
        cause: CauseBenefit, service: float, age: float, wage: float
    ) -> tuple[float, float]:
        """``(기본 급여, 가산 급여)``. 가입자격 문턱과 반올림 전이다.

        둘로 나누는 이유는 귀속 방식이 다르기 때문이다. 기본 급여는 급여식이
        내는 배수를 따라 쌓이고, 가산은 사유에 따라 즉시 귀속될 수 있다.
        """
        service = max(service, cause.min_service)
        base = multiple_at(service, age, cause.benefit_rule) * wage
        extra = cause.extra_amount
        if cause.extra_rule:
            extra += multiple_at(service, age, cause.extra_rule) * wage
        return base * db_share, extra * db_share

    def benefit_at(
        service: float, age: float, wage: float, cause: CauseBenefit = _NO_CAUSE
    ) -> float:
        """퇴직 시점 지급액.

        가입자격(최소 근속연수)을 못 채우고 나가면 지급 대상이 아니다. 대상에서
        빼는 것이 아니라 **급여식에서 0** 으로 두는 것이 맞다. 지금 근속이 짧아도
        정년까지 남아 요건을 채우면 그때는 지급 대상이 되므로, 그 몫은 그대로
        부채에 잡혀야 하기 때문이다.
        """
        if minimum > 0 and max(service, cause.min_service) < minimum:
            return 0.0
        base, extra = parts_at(cause, service, age, wage)
        return round_amount(base + extra, rounding_unit, rounding_mode)

    def attribution_at(
        total_service: float, age: float, cause: CauseBenefit = _NO_CAUSE
    ) -> tuple[float, float]:
        """(기준일까지 귀속비율, 당기 1년치 귀속비율).

        급여식이 근속에 비례하지 않으면 ``과거근속 ÷ 총근속`` 이 틀린다.
        30년 상한 규정에서 근속 35년인 사람은 더 일해도 급여가 늘지 않으므로
        이미 전액이 귀속돼 있어야 하는데, 근속비로 재면 총근속이 늘수록 오히려
        귀속비율이 줄어 채무가 과소계상된다(문단 70: 추가 근무가 유의적인 급여
        증가를 낳지 않는 시점에 귀속을 멈춘다).

        그래서 근속이 아니라 **급여식이 내는 배수** 로 잰다. 배수가 근속에
        비례하는 법정 퇴직금에서는 두 방식이 정확히 같은 값을 낸다.
        임금은 분자·분모에 똑같이 곱해지므로 배수만 보면 된다.
        """
        if total_service <= 0:
            return 0.0, 0.0

        total_multiple = multiple_at(
            max(total_service, cause.min_service), age, cause.benefit_rule
        )
        if total_multiple <= 0:
            # 배수가 0 이거나 음수인 규정(가감 규정 등)은 근속비로 되돌린다.
            return min(1.0, past_service / total_service), 1.0 / total_service

        earned = multiple_at(
            max(past_service, cause.min_service), age, cause.benefit_rule
        )
        # 당기 1년치는 '한 해 더 일했을 때 배수가 얼마나 느는가'.
        next_year = multiple_at(
            max(min(past_service + 1.0, total_service), cause.min_service),
            age, cause.benefit_rule,
        )

        attributed = min(1.0, max(0.0, earned / total_multiple))
        unit = max(0.0, (next_year - earned) / total_multiple)
        return attributed, unit

    def weigh(
        cause_name: str, total_service: float, exit_age: float, wage: float
    ) -> tuple[float, float]:
        """``(귀속된 급여, 당기 1년치 급여)``. 확률·할인 전 금액이다.

        기본 급여와 가산 급여를 따로 귀속한 뒤 합친다. 반올림은 실제 지급액에
        거는 것이므로, 합계에 한 번 걸고 그 비율만큼 두 몫을 함께 조정한다.
        """
        cause = causes.get(rule, cause_name)
        service = max(total_service, cause.min_service)
        if minimum > 0 and service < minimum:
            return 0.0, 0.0

        base, extra = parts_at(cause, total_service, exit_age, wage)
        raw = base + extra
        if raw <= 0:
            return 0.0, 0.0
        paid = round_amount(raw, rounding_unit, rounding_mode)
        scale = paid / raw

        share, unit_share = attribution_at(total_service, exit_age, cause)
        attributed = base * share
        unit = base * unit_share

        if extra:
            if cause.attribution_basis(cause_name) == ATTRIB_IMMEDIATE:
                # 근속을 더 쌓아도 늘지 않는 급여다. **오늘 근속으로 재어** 그만큼
                # 전액 귀속한다. 근속과 무관한 정액이면 언제나
                # 전액이고, '10년 미만 3개월분 / 이상 5개월분' 처럼 근속에 따라
                # 계단이 있으면 지금 올라선 칸까지만 잡힌다.
                _, earned = parts_at(cause, past_service, exit_age, wage)
                _, next_year = parts_at(
                    cause, min(past_service + 1.0, total_service), exit_age, wage
                )
                # 퇴직 시점에 실제로 받을 금액을 넘길 수는 없다.
                earned = min(earned, extra)
                attributed += earned
                unit += max(0.0, min(next_year, extra) - earned)
            else:
                attributed += extra * share
                unit += extra * unit_share

        return attributed * scale, unit * scale

    # 기준일 현재 즉시 퇴직 시 지급액. 귀속비율 1.0 에 해당한다.
    # 추계액은 '지금 자발적으로 나가면 얼마' 이므로 중도퇴직 규정으로 잰다.
    result.accrued_benefit = benefit_at(
        past_service, float(member.age), member.monthly_wage,
        causes.get(rule, CAUSE_VOLUNTARY),
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

        # 임금은 해당 연도 초에 인상된다고 본다. 직군 규칙에서 끈 항목은 0 이다.
        # 이미 끝난 지급 구간은 올리지 않는다 — 그 줄의 임금이 곧 그 시점의
        # 기준임금이라, 지금 다시 올리면 법이 정한 기준을 넘긴다.
        increase = 0.0
        if not closed:
            if member.apply_base_up:
                increase += assumptions.salary.base_up.rate(t)
            if member.apply_promotion:
                increase += assumptions.salary.promotion.rate(
                    salary_rule, age=age_t, service=service_t
                )
        wage *= 1.0 + increase

        withdrawal = (
            assumptions.withdrawal.rate(withdrawal_rule, age=age_t, service=service_t)
            if member.apply_withdrawal
            else 0.0
        )
        mortality = (
            assumptions.mortality.qx(member.gender, age_t)
            if member.apply_mortality
            else 0.0
        )
        withdrawal = min(max(withdrawal, 0.0), 1.0)

        # 중도·사망은 연중에 일어난다고 보아 그 해 한가운데에 둔다. 두 원인을
        # 갈라 놓는 것은, 사유별로 지급률이 다르면 뭉뚱그린 ``1-(1-w)(1-q)``
        # 로는 어느 규정을 적용할지 정할 수 없기 때문이다. 연중 균등발생을
        # 가정하면 두 몫의 합은 원래 확률 그대로다.
        exits = [
            (CAUSE_VOLUNTARY, t - 0.5,
             survival * withdrawal * (1.0 - mortality / 2.0)),
            (CAUSE_DEATH, t - 0.5,
             survival * mortality * (1.0 - withdrawal / 2.0)),
        ]
        if t == years:
            # 마지막 해라고 중도퇴직·사망이 멈추는 것이 아니다. 그 해를 넘긴
            # 사람만 정년을 맞는다. 마지막 해를 통째로 정년으로 두면 정년
            # 지급률이 더 높은 회사에서 그만큼 채무가 부풀고, 마지막 해의
            # 중도퇴직 급부가 통째로 사라진다.
            exits.append((CAUSE_NORMAL, float(t),
                          survival * (1.0 - withdrawal) * (1.0 - mortality)))

        for cause_name, timing, exit_probability in exits:
            if exit_probability <= 0.0:
                continue
            # 퇴직 시점의 연령·근속으로 평가한다. 정년 임박자 감액 같은 규정이
            # 기준일이 아니라 실제 퇴직 시점을 보고 판단해야 하기 때문이다.
            total_service = service_at(timing)
            exit_age = member.age + timing
            discount = assumptions.discount.discount_factor(timing)
            attributed, unit = weigh(cause_name, total_service, exit_age, wage)
            benefit = benefit_at(
                total_service, exit_age, wage, causes.get(rule, cause_name)
            )

            part_dbo = attributed * exit_probability * discount
            part_cost = unit * exit_probability * discount
            part_pv = benefit * exit_probability * discount
            dbo += part_dbo
            service_cost += part_cost
            benefit_pv += part_pv

            # 사유별 몫을 따로 쌓아 둔다. 합은 위의 총액과 같다.
            share = result.by_cause.setdefault(
                cause_name, {"dbo": 0.0, "service_cost": 0.0, "benefit_pv": 0.0})
            share["dbo"] += part_dbo
            share["service_cost"] += part_cost
            share["benefit_pv"] += part_pv
            weighted_time += attributed * exit_probability * discount * timing
            # 할인 전 현금흐름. 단일할인율 역산과 만기분석 공시에 쓴다.
            flow = attributed * exit_probability
            if flow:
                result.cash_flows[timing] = result.cash_flows.get(timing, 0.0) + flow
            paid_flow = benefit * exit_probability
            if paid_flow:
                result.benefit_flows[timing] = (
                    result.benefit_flows.get(timing, 0.0) + paid_flow
                )

            if trace is not None:
                trace.append({
                    "t": t, "timing": timing, "age": exit_age,
                    "service": total_service, "wage": wage,
                    "withdrawal": withdrawal, "mortality": mortality,
                    "survival": survival, "cause": cause_name,
                    "exit_probability": exit_probability,
                    "benefit": benefit, "attributed": attributed, "unit": unit,
                    "discount": discount,
                    "dbo": attributed * exit_probability * discount,
                    "service_cost": unit * exit_probability * discount,
                })

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
            # 기준일 이후 입사자는 산출 대상이 아니다. 조용히 빼면 인원이 왜
            # 줄었는지 알 수 없으므로 제외 사유를 남긴다.
            skipped = MemberValuation(
                employee_id=member.employee_id, name=member.name,
                job_group=member.job_group, gender=member.gender.value,
                age=member.age, past_service=0.0, projection_years=0,
                monthly_wage=member.monthly_wage,
                employee_type=member.employee_type.value,
                cost_code=member.cost_code, birth_date=member.birth_date,
                hire_date=member.hire_date,
                excluded_reason="입사일이 산출기준일보다 늦음",
            )
            result.members.append(skipped)
            continue
        result.members.append(value_member(member, config, assumptions))

    # 이자원가는 개인별로 1년 이자율을 써 두었다. 곡선을 쓴 경우 대표 이자율은
    # 채무 전체에서 역산한 단일할인율이므로, 다 모은 뒤 그것으로 다시 잡는다.
    if assumptions.discount.flat is None:
        rate = result.single_discount_rate()
        for member_result in result.members:
            member_result.interest_cost = member_result.dbo * rate
    return result
