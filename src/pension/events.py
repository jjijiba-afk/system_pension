"""기중 제도 변동 — 축소·정산·사업결합·분할.

결산일 명부에는 없는 사람들이 있다. 이미 정산하고 나갔거나, 사업을 사고팔며
통째로 넘어왔거나 넘어갔거나, 제도가 축소되어 종전 조건으로는 더 이상 세지
않는 사람들이다. 이들이 **결산일 명부에만 없고 어디에도 안 잡히면**, 기초에서
기말까지의 증감표가 그 금액만큼 통째로 어긋난다. 지금까지는 담당자가 소멸
채무를 손으로 계산해 한 칸에 적어 넣었다.

여기서는 [추가명부] 를 받아 **사건 시점 기준으로** 다시 평가한다. 결산일
가정으로 재면 사건일부터 결산일까지의 이자와 임금상승이 섞여 들어가, 정산손익이
그만큼 틀린다.

문단 109~110(정산), 문단 105~108(축소)이 요구하는 것은 같다 — 그 사건이
일어난 **시점의** 채무를 없애고, 지급액과의 차이를 그 즉시 당기손익으로
인식하라는 것이다.
"""

from __future__ import annotations

import dataclasses as _dc
import datetime as _dt
from dataclasses import dataclass, field
from typing import Final

from .assumptions import Assumptions
from .config import CalculationConfig
from .errors import IssueLog
from .models import ActiveMember
from .normalize import text

__all__ = [
    "CURTAILMENT",
    "DISPOSAL",
    "EVENT_KINDS",
    "EventEffect",
    "EventOutcome",
    "MERGER",
    "SETTLEMENT",
    "measure_events",
]

CURTAILMENT: Final = "축소"
"""제도 축소. 종전 조건으로 쌓이던 급여가 그 시점에 끊긴다."""
SETTLEMENT: Final = "정산"
"""정산. 채무를 돈으로 치르고 끝낸다 — 지급액과의 차이가 정산손익이다."""
MERGER: Final = "사업결합"
"""사업결합으로 **넘겨받은** 사람들. 채무가 그만큼 늘어난다."""
DISPOSAL: Final = "분할"
"""사업 분할·처분으로 **넘긴** 사람들. 채무가 그만큼 줄어든다."""

EVENT_KINDS: Final = (CURTAILMENT, SETTLEMENT, MERGER, DISPOSAL)

#: 사건이 채무를 늘리는가 줄이는가. 사업결합만 들어오는 쪽이다.
_INCOMING: Final = frozenset({MERGER})


@dataclass(slots=True)
class EventEffect:
    """사건 한 종류의 몫."""

    kind: str
    headcount: int = 0
    obligation: float = 0.0
    """사건 시점에 잰 확정급여채무. 소멸했든 인수했든 **양수** 로 담는다."""
    payment: float = 0.0
    """그 사건으로 실제 오간 금액."""

    @property
    def gain(self) -> float:
        """정산손익. 준 돈이 없앤 채무보다 적으면 이익(음수)이다.

        문단 109 의 부호를 그대로 쓴다 — 채무를 늘리는 쪽이 양수다. 인수한
        쪽(사업결합)은 대가를 따로 받으므로 여기서 손익을 내지 않는다.
        """
        if self.kind in _INCOMING:
            return 0.0
        return self.payment - self.obligation


@dataclass(slots=True)
class EventOutcome:
    """[추가명부] 를 재어 본 결과 전부."""

    effects: dict[str, EventEffect] = field(default_factory=dict)
    skipped: int = 0
    """사건 구분이나 사건일이 없어 세지 못한 줄 수."""

    def of(self, kind: str) -> EventEffect:
        return self.effects.get(kind) or EventEffect(kind=kind)

    @property
    def is_empty(self) -> bool:
        return not self.effects

    @property
    def settled_obligation(self) -> float:
        """정산·축소로 **소멸한** 채무. 증감표의 정산손익 계산에 쓴다."""
        return self.of(SETTLEMENT).obligation + self.of(CURTAILMENT).obligation

    @property
    def settled_paid(self) -> float:
        return self.of(SETTLEMENT).payment + self.of(CURTAILMENT).payment

    @property
    def transfers_in(self) -> float:
        """사업결합으로 인수한 채무. 증감표의 유입이다."""
        return self.of(MERGER).obligation

    @property
    def transfers_out(self) -> float:
        """분할·처분으로 넘긴 채무. 증감표의 유출이다."""
        return self.of(DISPOSAL).obligation

    def as_rows(self) -> list[tuple[str, float]]:
        """보고서에 그대로 실을 줄들."""
        rows: list[tuple[str, float]] = []
        for kind in EVENT_KINDS:
            effect = self.effects.get(kind)
            if effect is None:
                continue
            rows.append((f"{kind} — 인원", float(effect.headcount)))
            rows.append((f"{kind} — 사건시점 채무", effect.obligation))
            if kind not in _INCOMING:
                rows.append((f"{kind} — 지급액", effect.payment))
                rows.append((f"{kind} — 손익", effect.gain))
        return rows


def _normalize_kind(raw: str) -> str:
    """'사업 결합', '제도축소', '매각·분할' 같은 표기를 낱말 하나로."""
    token = text(raw).replace(" ", "")
    if not token:
        return ""
    for kind in EVENT_KINDS:
        if kind in token:
            return kind
    # 실무에서 자주 오는 다른 말들.
    if any(word in token for word in ("매각", "처분", "양도", "전출")):
        return DISPOSAL
    if any(word in token for word in ("합병", "인수", "양수", "전입")):
        return MERGER
    if "중간정산" in token or "지급" in token:
        return SETTLEMENT
    return ""


def measure_events(
    members: list[ActiveMember],
    config: CalculationConfig,
    assumptions: Assumptions,
    log: IssueLog | None = None,
) -> EventOutcome:
    """[추가명부] 를 사건 시점 기준으로 재어 본다.

    사건일이 여럿이면 **날짜마다 따로** 잰다. 한 날짜로 뭉뚱그리면 7월에 판
    사업부와 11월에 정산한 사람이 같은 시점의 채무로 섞인다.

    :param config: 결산일 기준 설정. 사건일마다 ``base_date`` 만 바꿔 쓴다 —
        직군 규칙과 지급규정은 그대로여야 그 시점에 걸려 있던 규정으로 재진다.
    """
    from .valuation import value_member

    outcome = EventOutcome()
    if not members:
        return outcome

    by_date: dict[_dt.date, list[tuple[str, ActiveMember]]] = {}
    for member in members:
        kind = _normalize_kind(member.event_kind)
        if not kind or member.event_date is None:
            outcome.skipped += 1
            if log is not None:
                log.warning(
                    "JAE_EVENT_INCOMPLETE",
                    f"사번 {member.employee_id or '(없음)'}: 사건 구분 또는 사건일이 "
                    f"없어 이 줄을 세지 않았습니다 "
                    f"({' / '.join(EVENT_KINDS)} 중 하나와 날짜가 필요합니다)",
                    sheet="추가명부", row=member.row, seq=member.seq,
                )
            continue
        by_date.setdefault(member.event_date, []).append((kind, member))

    for event_date, entries in sorted(by_date.items()):
        # 사건일 기준으로 연령·근속을 다시 잡는다. 검증 이슈는 결산일 명부의
        # 것과 섞이면 안 되므로 따로 받는다 — 사건일이 결산일보다 앞서기만
        # 하면 '기준일 이후 입사' 같은 경고가 무더기로 뜬다.
        at_event = _dc.replace(config, base_date=event_date)
        _refill(([m for _kind, m in entries]), at_event)
        for kind, member in entries:
            result = value_member(member, at_event, assumptions)
            effect = outcome.effects.setdefault(kind, EventEffect(kind=kind))
            effect.headcount += 1
            effect.obligation += result.dbo
            effect.payment += member.event_payment
    return outcome


def _refill(members: list[ActiveMember], config: CalculationConfig) -> None:
    """사건일 기준으로 연령·정년을 다시 채운다.

    :func:`pension.validation.validate_active` 를 그대로 부르지 않는 것은,
    그것이 결산일 명부를 겨눈 검사들(중복 사번·이름 누락·직군 미매칭)까지 함께
    돌려 이슈를 두 번 쌓기 때문이다. 여기서 필요한 것은 파생값뿐이다 — 직군
    규칙에서 오는 나머지 항목은 명부를 읽을 때 이미 채워져 있다.
    """
    from .actuarial import (
        attained_age,
        longterm_retirement_age,
        normal_retirement_age,
    )
    from .normalize import EmployeeType

    for member in members:
        if member.birth_date is None:
            continue
        member.age = attained_age(member.birth_date, config.base_date)
        if member.hire_date is not None:
            member.hire_age = attained_age(member.birth_date, member.hire_date)
        if member.job_group_index is None:
            continue
        rule = config.job_group_rules[member.job_group_index]
        member.severance_nra = normal_retirement_age(
            member.age, rule,
            wage_peak_age=member.wage_peak_age,
            is_executive=member.employee_type is EmployeeType.EXECUTIVE,
            declared_nra=member.declared_nra,
            contract_years=member.remaining_contract_years,
        )
        member.longterm_nra = longterm_retirement_age(
            member.age, rule, contract_years=member.remaining_contract_years,
            declared_nra=member.declared_longterm_nra,
        )
