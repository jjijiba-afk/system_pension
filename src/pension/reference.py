"""참고 산출 시스템의 셈법을 **그대로** 옮긴 것.

우리 엔진(:mod:`pension.valuation`)과는 별개다. 여기 있는 것은 상용 시스템이
내는 숫자를 재현하기 위한 자이지, 우리가 쓰는 방식이 아니다. 둘을 한 파일에
섞으면 어느 쪽 관행인지 금세 헷갈리므로 갈라 둔다.

**왜 필요한가.** 같은 명부·같은 가정인데 채무가 다르게 나오면, 어느 쪽이
틀렸는지가 아니라 **어디서 갈렸는지** 를 먼저 알아야 한다. 총액만 놓고 보면
영영 알 수 없다 — 할인 시점, 임금 시점, 귀속 분모, 정년 처리가 저마다 조금씩
다르고 그것들이 곱해져 총액 하나로 뭉쳐 오기 때문이다. 연차별로 같은 자를
대고 한 줄씩 맞대야 짚인다.

받은 산출 표본을 셀 단위로 뜯어 옮겼고, 시험(``test_reference_reconciliation``)
이 표본의 1,400여 셀과 한 칸씩 대조한다.

관행 요약 — 우리 엔진과 갈리는 지점
    연 눈금
        근속은 ``(기준일 − 기산일 + 1) / 365.25`` 로 재고, 이후 해마다 정확히
        1 씩 더한다. 회사의 일할·월할 규칙을 다시 태우지 않는다.
    할인 시점
        중도·사망은 연 중앙(``t + 0.5``), 정년은 정확히 ``t``.
    급여 시점
        중도·사망 급부를 **당해와 차기 두 해의 평균** 으로 본다. 우리 엔진은
        그 해 초 임금 한 항으로 잰다.
    정년
        정년에 닿는 해는 **통째로 정년** 이다. 그 해의 중도·사망은 보지 않는다.
    잔존
        ``잔존 = 재직 − 중도 − 사망``. 명예퇴직은 잔존을 깎지 않는다.
    귀속
        근속기간할당은 ``기왕근속 ÷ 그 시점 근속``, 지급률할당은
        ``기산출 지급률 ÷ 그 시점 지급률``. 둘 중 하나를 스위치로 고른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Sequence

__all__ = [
    "ALLOCATION_SERVICE", "ALLOCATION_MULTIPLE", "ALLOCATIONS",
    "SeveranceCase", "SeveranceYear", "severance_table",
    "LongTermCase", "LongTermYear", "long_term_table",
    "ExtraPayCase", "ExtraPayYear", "extra_pay_table",
    "totals", "duration_of",
]

#: 귀속(할당) 방식.
ALLOCATION_SERVICE: Final = "근속기간"
"""기왕근속 ÷ 그 시점 근속. 급여가 근속에 비례할 때 쓰는 흔한 방식."""
ALLOCATION_MULTIPLE: Final = "지급률"
"""기산출 지급률 ÷ 그 시점 지급률. 누진·상한이 있는 규정에서 갈린다."""

ALLOCATIONS: Final = (ALLOCATION_SERVICE, ALLOCATION_MULTIPLE)


def _ratio(numerator: float, denominator: float) -> float:
    """분모가 0 이면 0. 원본의 ``IF(x=0, 0, …)`` 방어와 같은 뜻이다."""
    return 0.0 if not numerator else numerator / denominator


def _at(values: Sequence[float], index: int) -> float:
    """표 밖을 물으면 0. 원본이 빈 칸을 0 으로 읽는 것과 같다."""
    return values[index] if 0 <= index < len(values) else 0.0


# ── 퇴직급여 ────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class SeveranceCase:
    """퇴직급여 한 사람분 입력.

    연차별 표(``age`` 이하)는 ``t = 0`` 부터 정년에 닿는 해까지다. 지급률 표만
    한 칸 더 길어도 된다 — 급부를 차기 연도 값으로도 재기 때문이다.
    """

    discount_rate: float
    base_up: float
    nra: int
    """정년 연령. ``age[t]`` 가 이 값이면 그 해를 정년으로 본다."""
    wage: float
    """기준일 30일 평균임금."""
    alloc_service: float
    """귀속(할당) 근속. 중간정산을 했으면 그날부터 잰다."""
    total_service: float
    """총근속. 명예퇴직 급부의 귀속 분모로 쓴다."""
    age: Sequence[float]
    promotion: Sequence[float]
    withdrawal: Sequence[float]
    mortality: Sequence[float]
    early: Sequence[float] = ()
    multiple_normal: Sequence[float] = ()
    multiple_voluntary: Sequence[float] = ()
    multiple_death: Sequence[float] = ()
    multiple_early: Sequence[float] = ()
    allocation: str = ALLOCATION_SERVICE

    @property
    def years(self) -> int:
        return len(self.age)


@dataclass(slots=True)
class SeveranceYear:
    """한 연차의 계산 결과. 이름은 원본 시트의 열 뜻을 그대로 옮겼다."""

    t: int
    age: float
    alloc_service: float          # D — 귀속 근속
    total_service: float          # E — 총근속
    wage: float                   # S
    discount: float               # I
    at_work: float                # N — 그 해 초 재직확률
    voluntary: float              # O — 중도퇴직
    death: float                  # P — 사망
    early: float                  # Q — 명예퇴직
    surviving: float              # R — 그 해 말 잔존
    retirement: bool = False
    """정년에 닿는 해인지. 할인 시점과 만기 가중이 이 하나로 갈린다."""
    dbo_normal: float = 0.0       # Y  / AD
    dbo_voluntary: float = 0.0    # Z  / AE
    dbo_death: float = 0.0        # AA / AF
    dbo_early: float = 0.0        # AB / AG
    cost_normal: float = 0.0      # AI
    cost_voluntary: float = 0.0   # AJ
    cost_death: float = 0.0       # AK
    cost_early: float = 0.0       # AL

    @property
    def dbo(self) -> float:
        """AC / AH — 이 연차가 채무에 보태는 몫."""
        return self.dbo_normal + self.dbo_voluntary + self.dbo_death + self.dbo_early

    @property
    def service_cost(self) -> float:
        """AM — 당기근무원가 몫."""
        return (self.cost_normal + self.cost_voluntary
                + self.cost_death + self.cost_early)

    @property
    def timing(self) -> float:
        """만기를 잴 때 이 몫이 놓이는 시점. 정년만 정확히 ``t`` 다."""
        return float(self.t) if self.retirement else self.t + 0.5

    @property
    def cash_flow(self) -> float:
        """AP — 할인 전 금액. 채무를 할인계수로 되돌린 것이다."""
        return _ratio(self.dbo, self.discount)


def severance_table(case: SeveranceCase) -> list[SeveranceYear]:
    """퇴직급여를 연차별로 편다.

    :param case: 한 사람분 입력.
    :return: ``t = 0`` 부터 정년행까지. 합계는 :func:`totals` 로 낸다.
    """
    years = case.years
    rows: list[SeveranceYear] = []

    # 임금·근속·잔존을 먼저 한 번에 편다. 급부가 차기 연도 값을 보므로,
    # 한 줄씩 계산하면서 앞을 참조할 수가 없다.
    wages = [case.wage]
    for t in range(1, years + 1):
        wages.append(wages[-1] * (1 + case.base_up) * (1 + _at(case.promotion, t - 1)))
    alloc = [case.alloc_service + t for t in range(years + 1)]
    total = [case.total_service + t for t in range(years + 1)]

    at_work = [0.0] * (years + 1)
    at_work[0] = 1.0
    voluntary = [0.0] * (years + 1)
    death = [0.0] * (years + 1)
    early = [0.0] * (years + 1)
    surviving = [0.0] * (years + 1)
    for t in range(years):
        voluntary[t] = at_work[t] * _at(case.withdrawal, t)
        death[t] = at_work[t] * _at(case.mortality, t)
        early[t] = at_work[t] * _at(case.early, t)
        # 명예퇴직은 잔존에서 빼지 않는다 — 원본이 그렇게 세고, 명퇴 대상이
        # 아닌 회사에서는 어차피 0 이다.
        surviving[t] = at_work[t] - voluntary[t] - death[t]
        if t + 1 < years:
            at_work[t + 1] = surviving[t]

    by_multiple = case.allocation == ALLOCATION_MULTIPLE
    first_voluntary = _at(case.multiple_voluntary, 0)
    first_death = _at(case.multiple_death, 0)
    first_normal = _at(case.multiple_normal, 0)
    # 지급률할당의 당기 1년치는 '지급률이 한 해에 얼마나 느는가' 다. 원본이
    # 첫 두 해의 차이를 그대로 모든 해에 쓴다(누진이 일정하다는 가정).
    step_voluntary = _at(case.multiple_voluntary, 1) - first_voluntary
    step_death = _at(case.multiple_death, 1) - first_death
    step_normal = _at(case.multiple_normal, 1) - first_normal

    for t in range(years):
        retiring = case.age[t] == case.nra
        power = t if retiring else t + 0.5
        discount = 1.0 / (1.0 + case.discount_rate) ** power
        row = SeveranceYear(
            t=t, age=case.age[t], alloc_service=alloc[t], total_service=total[t],
            wage=wages[t], discount=discount, at_work=at_work[t],
            voluntary=voluntary[t], death=death[t], early=early[t],
            surviving=surviving[t], retirement=retiring,
        )

        if retiring:
            # 정년에 닿는 해는 통째로 정년이다. 남아 있는 사람 전부가 이 날
            # 나가므로 그 해의 중도·사망은 세지 않는다.
            benefit = wages[t] * _at(case.multiple_normal, t) * at_work[t]
            if by_multiple:
                unit = _ratio(benefit, _at(case.multiple_normal, t))
                row.dbo_normal = unit * discount * first_normal
                row.cost_normal = unit * discount * step_normal
            else:
                unit = benefit / alloc[t]
                row.dbo_normal = unit * discount * case.alloc_service
                row.cost_normal = unit * discount
            rows.append(row)
            continue

        # 중도·사망은 연 중앙에 나간다고 보고, 급부를 **당해와 차기 두 해의
        # 평균** 으로 잰다. 반년 뒤의 급여를 한 항으로 집어내는 대신 양 끝을
        # 평균해 가운데를 짚는 방식이다.
        def averaged(multiples: Sequence[float], denominators: list[float]) -> float:
            here = _ratio(wages[t] * _at(multiples, t), denominators[t])
            ahead = _ratio(wages[t + 1] * _at(multiples, t + 1), denominators[t + 1])
            return (here + ahead) / 2.0

        def averaged_by_multiple(multiples: Sequence[float]) -> float:
            here = _ratio(wages[t] * _at(multiples, t), _at(multiples, t))
            ahead = _ratio(wages[t + 1] * _at(multiples, t + 1), _at(multiples, t + 1))
            return (here + ahead) / 2.0

        # 명예퇴직은 두 방식에서 똑같이 총근속으로 귄다.
        early_unit = averaged(case.multiple_early, total)
        row.dbo_early = early_unit * early[t] * discount * case.total_service
        row.cost_early = (
            _ratio(wages[t + 1] * _at(case.multiple_early, t + 1), total[t + 1]) / 2.0
            * early[t] * discount
            if t == 0 else early_unit * early[t] * discount
        )

        if by_multiple:
            row.dbo_voluntary = (averaged_by_multiple(case.multiple_voluntary)
                                 * voluntary[t] * discount * first_voluntary)
            row.dbo_death = (averaged_by_multiple(case.multiple_death)
                             * death[t] * discount * first_death)
            if t == 0:
                # 첫 해는 차기 연도 한 항만 절반으로 센다 — 기준일 이후 남은
                # 기간의 몫이라는 뜻이다.
                ahead_v = _ratio(wages[1] * _at(case.multiple_voluntary, 1),
                                 _at(case.multiple_voluntary, 1)) / 2.0
                ahead_d = _ratio(wages[1] * _at(case.multiple_death, 1),
                                 _at(case.multiple_death, 1)) / 2.0
                row.cost_voluntary = ahead_v * voluntary[t] * discount * step_voluntary
                row.cost_death = ahead_d * death[t] * discount * step_death
            else:
                row.cost_voluntary = (averaged_by_multiple(case.multiple_voluntary)
                                      * voluntary[t] * discount * step_voluntary)
                row.cost_death = (averaged_by_multiple(case.multiple_death)
                                  * death[t] * discount * step_death)
        else:
            row.dbo_voluntary = (averaged(case.multiple_voluntary, alloc)
                                 * voluntary[t] * discount * case.alloc_service)
            row.dbo_death = (averaged(case.multiple_death, alloc)
                             * death[t] * discount * case.alloc_service)
            if t == 0:
                ahead_v = _ratio(wages[1] * _at(case.multiple_voluntary, 1),
                                 alloc[1]) / 2.0
                ahead_d = _ratio(wages[1] * _at(case.multiple_death, 1),
                                 alloc[1]) / 2.0
                row.cost_voluntary = ahead_v * voluntary[t] * discount
                row.cost_death = ahead_d * death[t] * discount
            else:
                row.cost_voluntary = (averaged(case.multiple_voluntary, alloc)
                                      * voluntary[t] * discount)
                row.cost_death = (averaged(case.multiple_death, alloc)
                                  * death[t] * discount)

        rows.append(row)

    return rows


# ── 장기종업원급여 ──────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class LongTermCase:
    """장기급여 한 사람분 입력.

    급부가 네 갈래다 — 평균임금분·금등 현물·현금·휴가일수. 재직(정년 포함)
    지급률과 탈퇴자 지급률이 따로 있고, 급부액은 **차기 연도** 지급률·임금으로
    잰다(그 해를 채워야 받는 급부라는 뜻이다).
    """

    discount_rate: float
    base_up: float
    nra: int
    wage: float
    """장기급여용 30일 평균임금."""
    daily_wage: float
    """휴가일수에 곱하는 日기본급."""
    total_service: float
    gold_price: float = 0.0
    gold_growth: float = 0.0
    age: Sequence[float] = ()
    promotion: Sequence[float] = ()
    withdrawal: Sequence[float] = ()
    mortality: Sequence[float] = ()
    staying_wage: Sequence[float] = ()      # BH — 재직·정년 평균임금 지급률
    staying_gold: Sequence[float] = ()      # BI
    staying_cash: Sequence[float] = ()      # BJ
    staying_leave: Sequence[float] = ()     # BK
    leaving_wage: Sequence[float] = ()      # BL — 탈퇴자 지급률
    leaving_gold: Sequence[float] = ()      # BM
    leaving_cash: Sequence[float] = ()      # BN
    leaving_leave: Sequence[float] = ()     # BO

    @property
    def years(self) -> int:
        return len(self.age)


@dataclass(slots=True)
class LongTermYear:
    """한 연차의 장기급여 결과."""

    t: int
    age: float
    total_service: float
    wage: float
    daily_wage: float
    discount: float
    at_work: float
    voluntary: float
    death: float
    surviving: float
    retirement: bool = False
    dbo_wage: float = 0.0        # BP
    dbo_gold: float = 0.0        # BQ
    dbo_cash: float = 0.0        # BR
    dbo_leave: float = 0.0       # BS
    service_cost: float = 0.0    # BU

    @property
    def dbo(self) -> float:
        """BT."""
        return self.dbo_wage + self.dbo_gold + self.dbo_cash + self.dbo_leave

    @property
    def timing(self) -> float:
        return float(self.t) if self.retirement else self.t + 0.5

    @property
    def cash_flow(self) -> float:
        """CB — 할인 전 금액."""
        return _ratio(self.dbo, self.discount)


def long_term_table(case: LongTermCase) -> list[LongTermYear]:
    """장기급여를 연차별로 편다."""
    years = case.years
    wages = [case.wage]
    dailies = [case.daily_wage]
    for t in range(1, years + 1):
        step = (1 + case.base_up) * (1 + _at(case.promotion, t - 1))
        wages.append(wages[-1] * step)
        dailies.append(dailies[-1] * step)
    service = [case.total_service + t for t in range(years + 1)]

    at_work = [0.0] * (years + 1)
    at_work[0] = 1.0
    voluntary = [0.0] * (years + 1)
    death = [0.0] * (years + 1)
    surviving = [0.0] * (years + 1)
    for t in range(years):
        voluntary[t] = at_work[t] * _at(case.withdrawal, t)
        death[t] = at_work[t] * _at(case.mortality, t)
        surviving[t] = at_work[t] - voluntary[t] - death[t]
        if t + 1 < years:
            at_work[t + 1] = surviving[t]

    rows: list[LongTermYear] = []
    for t in range(years):
        retiring = case.age[t] == case.nra
        discount = 1.0 / (1.0 + case.discount_rate) ** (t if retiring else t + 0.5)
        row = LongTermYear(
            t=t, age=case.age[t], total_service=service[t], wage=wages[t],
            daily_wage=dailies[t], discount=discount, at_work=at_work[t],
            voluntary=voluntary[t], death=death[t], surviving=surviving[t],
            retirement=retiring,
        )

        def gold_at(power: float) -> float:
            return case.gold_price * (1 + case.gold_growth) ** power

        if retiring:
            share = case.total_service / service[t]
            weight = share * at_work[t] * discount
            row.dbo_wage = _at(case.staying_wage, t + 1) * wages[t] * weight
            row.dbo_gold = _at(case.staying_gold, t + 1) * gold_at(t) * weight
            row.dbo_cash = _at(case.staying_cash, t + 1) * weight
            row.dbo_leave = _at(case.staying_leave, t + 1) * dailies[t] * weight
        else:
            # 그 해 안에 나가는 사람은 남는 사람과 급부가 다르다. 남는 쪽은
            # 연초·연말 재직확률의 평균으로, 나가는 쪽은 탈퇴확률 그대로.
            share = case.total_service / (service[t] + 0.5)
            staying = (at_work[t] + surviving[t]) / 2.0
            leaving = voluntary[t] + death[t]
            gold = gold_at(t + 1)
            row.dbo_wage = (
                _at(case.staying_wage, t + 1) * wages[t + 1] * share * staying * discount
                + _at(case.leaving_wage, t + 1) * wages[t + 1] * share * leaving * discount)
            row.dbo_gold = (
                _at(case.staying_gold, t + 1) * gold * share * staying * discount
                + _at(case.leaving_gold, t + 1) * gold * share * leaving * discount)
            row.dbo_cash = (
                _at(case.staying_cash, t + 1) * share * staying * discount
                + _at(case.leaving_cash, t + 1) * share * leaving * discount)
            row.dbo_leave = (
                _at(case.staying_leave, t + 1) * dailies[t + 1] * share * staying * discount
                + _at(case.leaving_leave, t + 1) * dailies[t + 1] * share * leaving * discount)

        # 근무원가는 채무를 기왕근속으로 나눈 1년치다. 첫 해만 절반 — 기준일
        # 이후 남은 기간의 몫이라는 뜻이다.
        row.service_cost = row.dbo / case.total_service * (0.5 if t == 0 else 1.0)
        rows.append(row)

    return rows


# ── 추가지급(전별금) ────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class ExtraPayCase:
    """근속 기념 전별금처럼 퇴직급여와 별도로 얹는 급부.

    퇴직급여 본체와 달리 **중도·사망을 가르지 않고 합쳐** 센다. 사유별 지급률
    차등이 없는 급부라 나눌 이유가 없기 때문이다.
    """

    discount_rate: float
    base_up: float
    nra: int
    base_pay: float
    """추가지급의 기준 기본급."""
    service: float
    """추가지급 기산일부터의 근속."""
    age: Sequence[float] = ()
    promotion: Sequence[float] = ()
    withdrawal: Sequence[float] = ()
    mortality: Sequence[float] = ()
    multiple: Sequence[float] = ()
    allocation: str = ALLOCATION_SERVICE

    @property
    def years(self) -> int:
        return len(self.age)


@dataclass(slots=True)
class ExtraPayYear:
    """한 연차의 추가지급 결과."""

    t: int
    age: float
    service: float
    base_pay: float
    discount: float
    at_work: float
    leaving: float
    """중도 + 사망. 이 급부는 둘을 가르지 않는다."""
    dbo: float = 0.0             # CO / CP
    service_cost: float = 0.0    # CQ


def extra_pay_table(case: ExtraPayCase) -> list[ExtraPayYear]:
    """추가지급을 연차별로 편다."""
    years = case.years
    pays = [case.base_pay]
    for t in range(1, years + 1):
        pays.append(pays[-1] * (1 + case.base_up) * (1 + _at(case.promotion, t - 1)))
    service = [case.service + t for t in range(years + 1)]

    at_work = [0.0] * (years + 1)
    at_work[0] = 1.0
    leaving = [0.0] * (years + 1)
    for t in range(years):
        out = at_work[t] * (_at(case.withdrawal, t) + _at(case.mortality, t))
        leaving[t] = out
        if t + 1 < years:
            at_work[t + 1] = at_work[t] - out

    by_multiple = case.allocation == ALLOCATION_MULTIPLE
    first = _at(case.multiple, 0)
    rows: list[ExtraPayYear] = []
    for t in range(years):
        retiring = case.age[t] == case.nra
        discount = 1.0 / (1.0 + case.discount_rate) ** (t if retiring else t + 0.5)
        row = ExtraPayYear(
            t=t, age=case.age[t], service=service[t], base_pay=pays[t],
            discount=discount, at_work=at_work[t], leaving=leaving[t],
        )
        denominators = case.multiple if by_multiple else service
        scale = first if by_multiple else case.service
        if by_multiple and not first:
            rows.append(row)
            continue

        def amount(index: int) -> float:
            return _ratio(pays[index] * _at(case.multiple, index),
                          _at(denominators, index))

        if retiring:
            row.dbo = amount(t) * at_work[t] * discount * scale
            row.service_cost = amount(t) * at_work[t] * discount
        else:
            averaged = (amount(t) + amount(t + 1)) / 2.0
            row.dbo = averaged * leaving[t] * discount * scale
            # 첫 해는 차기 연도 한 항만 절반으로 센다.
            unit = amount(t + 1) / 2.0 if t == 0 else averaged
            row.service_cost = unit * leaving[t] * discount
        rows.append(row)

    return rows


# ── 합계 ────────────────────────────────────────────────────────────


def totals(rows: Sequence[SeveranceYear | LongTermYear]) -> dict[str, float]:
    """연차별 표를 한 사람분 요약으로 접는다.

    :return: ``확정급여채무`` · ``당기근무원가`` · ``가중평균만기`` ·
        ``차년도 이자비용`` 을 담은 뭉치.
    """
    dbo = sum(row.dbo for row in rows)
    return {
        "확정급여채무": dbo,
        "당기근무원가": sum(row.service_cost for row in rows),
        "가중평균만기": duration_of(rows),
        "할인전 현금흐름": sum(row.cash_flow for row in rows),
    }


def duration_of(rows: Sequence[SeveranceYear | LongTermYear]) -> float:
    """가중평균만기(맥컬리).

    할인된 채무를 무게로 삼아 시점을 평균한다. 정년 몫만 정확히 ``t`` 에,
    나머지는 연 중앙(``t + 0.5``)에 놓인다 — 할인할 때 쓴 시점과 같아야 한다.
    """
    dbo = sum(row.dbo for row in rows)
    if not dbo:
        return 0.0
    return sum(row.dbo * row.timing for row in rows) / dbo
