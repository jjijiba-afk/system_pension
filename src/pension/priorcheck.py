"""전기 명부와 당기 명부를 사람 단위로 맞대어 본다.

당기 명부만 놓고 보면 멀쩡한데 **전기와 나란히 놓아야 드러나는** 잘못이 있다.
검증(:mod:`pension.validation`)은 한 해치 자료의 앞뒤만 보므로 이런 것을 잡지
못한다.

    같은 사번인데 생년월일이 다르다      → 연령이 통째로 달라져 채무가 어긋난다
    같은 사번인데 입사일이 다르다        → 근속이 달라져 지급배수가 어긋난다
    임금이 열 배로 뛰었다                → 단위 혼재(천원↔원)이거나 자릿수 오타
    전기 재직자가 어디에도 없다          → 명부에서 누락됐거나 퇴직자에 안 옮겼다
    전기 퇴직자가 당기 재직자에 있다     → 퇴직 처리가 잘못됐거나 재입사인데 표기가 없다
    DB 였는데 DC 가 됐다                 → 정산손익이 나야 하는데 빠졌을 수 있다

어느 것이든 **결산이 끝난 뒤에 발견하면 다시 산출** 해야 한다. 그래서 산출
전에 본다.

여기서 하는 일은 **찾아서 알려 주는 것뿐** 이다. 고치지 않고, 산출을 막지도
않는다. 실제로 사람이 바뀐 경우도 있기 때문이다 — 개명, 주민번호 정정, 승진에
따른 임금 급등은 모두 정상이다. 판단은 담당자가 한다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "HEADCOUNT_FLOOR",
    "WAGE_DROP",
    "WAGE_JUMP",
    "Finding",
    "PriorComparison",
    "compare_rosters",
]

#: 임금이 이 배를 넘게 뛰면 들여다볼 만하다. 승진·호봉 인상으로는 잘 나오지
#: 않는 폭이고, 천원 단위를 원 단위로 잘못 옮기면 정확히 1000배가 된다.
WAGE_JUMP = 1.5

#: 임금이 이 배 아래로 떨어졌을 때. 임금피크·감봉이 실제로 있으므로 경고로만 본다.
WAGE_DROP = 0.7

#: 재직 인원이 이 비율을 넘게 변하면 명부를 잘못 받았을 수 있다.
HEADCOUNT_SHIFT = 0.20

#: 비율로 인원 변동을 보려면 최소 이만큼은 있어야 한다.
#:
#: 8명짜리 단체에서 두 명이 나가면 -25% 다. 정상인데도 매번 걸린다. 작은
#: 단체는 사람이 하나 오가는 것만으로 비율이 크게 흔들리므로, 비율이 뜻을
#: 갖는 규모에서만 본다. 개인별 검사(생년월일·입사일·임금)는 인원과 무관하게
#: 늘 돌므로 작은 단체가 검사에서 빠지는 것은 아니다.
HEADCOUNT_FLOOR = 30


@dataclass(slots=True)
class Finding:
    """맞대어 보고 찾은 것 하나."""

    code: str
    """무엇인지 가리키는 짧은 이름. 화면이 묶어 세는 데 쓴다."""
    employee_id: str
    """해당하는 사번. 명부 전체에 걸친 것이면 빈 문자열."""
    message: str
    """사람이 읽을 설명. 무엇이 전기와 다른지 값까지 적는다."""
    serious: bool = False
    """산출 결과를 틀리게 만드는 것인지. 아니면 살펴볼 거리다."""

    def as_row(self) -> list[str]:
        return [self.code, self.employee_id, self.message]


@dataclass(slots=True)
class PriorComparison:
    """맞대어 본 결과 한 벌."""

    findings: list[Finding] = field(default_factory=list)
    current_active: int = 0
    prior_active: int = 0
    matched: int = 0
    """두 명부에 모두 있는 사람 수. 이 수가 너무 적으면 사번 체계가 바뀐 것이다."""

    @property
    def serious(self) -> list[Finding]:
        return [f for f in self.findings if f.serious]

    @property
    def notes(self) -> list[Finding]:
        return [f for f in self.findings if not f.serious]

    def summary(self) -> str:
        if not self.findings:
            return "전기 명부와 맞대어 본 결과 이상 없습니다."
        return (f"확인이 필요한 것 {len(self.serious)}건, "
                f"살펴볼 것 {len(self.notes)}건을 찾았습니다.")

    def counts(self) -> dict[str, int]:
        """코드별 건수. 같은 성격이 수십 건씩 나오므로 화면은 묶어서 보여 준다."""
        tally: dict[str, int] = {}
        for finding in self.findings:
            tally[finding.code] = tally.get(finding.code, 0) + 1
        return tally


def _wage(member: Any) -> float:
    return float(getattr(member, "monthly_wage", 0.0) or 0.0)


def _date_text(value: _dt.date | None) -> str:
    return value.isoformat() if value else "(없음)"


def _by_id(members: list[Any]) -> dict[str, Any]:
    """사번으로 색인한다.

    같은 사번이 여러 줄인 명부가 있다(임원 세법한도 구간 분할). 맞대어 보는
    목적에서는 첫 줄이면 충분하다 — 생년월일·입사일은 줄마다 같기 때문이다.
    """
    found: dict[str, Any] = {}
    for member in members:
        key = str(getattr(member, "employee_id", "") or "").strip()
        if key:
            found.setdefault(key, member)
    return found


def compare_rosters(current: Any, prior: Any) -> PriorComparison:
    """당기 명부를 전기 명부와 맞대어 본다.

    :param current: 이번에 산출할 :class:`~pension.models.Roster`.
    :param prior: 저장해 둔 전기 산출의 명부.
    """
    now_active = _by_id(current.active)
    was_active = _by_id(prior.active)
    now_retired = _by_id(current.retired)
    was_retired = _by_id(prior.retired)

    result = PriorComparison(
        current_active=len(current.active), prior_active=len(prior.active))
    found = result.findings

    # ── 명부 전체 ────────────────────────────────────────────────
    if len(prior.active) >= HEADCOUNT_FLOOR and current.active:
        shift = (len(current.active) - len(prior.active)) / len(prior.active)
        if abs(shift) > HEADCOUNT_SHIFT:
            found.append(Finding(
                "인원 급변", "",
                f"재직 인원이 전기 {len(prior.active):,}명 → 당기 "
                f"{len(current.active):,}명 ({shift:+.0%}) 입니다. "
                "명부를 제대로 받았는지 확인하세요.",
                serious=True))

    shared = set(now_active) & set(was_active)
    result.matched = len(shared)
    if was_active and not shared:
        found.append(Finding(
            "사번 불일치", "",
            "전기 명부와 겹치는 사번이 하나도 없습니다. 사번 체계가 바뀌었다면 "
            "아래 비교는 뜻이 없습니다.",
            serious=True))
        return result

    # ── 사람마다 ─────────────────────────────────────────────────
    for key in sorted(shared):
        now, was = now_active[key], was_active[key]

        if now.birth_date and was.birth_date and now.birth_date != was.birth_date:
            found.append(Finding(
                "생년월일 변경", key,
                f"생년월일이 {_date_text(was.birth_date)} → "
                f"{_date_text(now.birth_date)} 로 바뀌었습니다. "
                "연령이 달라져 채무가 통째로 어긋납니다.",
                serious=True))

        if now.hire_date and was.hire_date and now.hire_date != was.hire_date:
            found.append(Finding(
                "입사일 변경", key,
                f"입사일이 {_date_text(was.hire_date)} → "
                f"{_date_text(now.hire_date)} 로 바뀌었습니다. "
                "근속이 달라져 지급배수가 어긋납니다.",
                serious=True))

        old_wage, new_wage = _wage(was), _wage(now)
        if old_wage > 0 and new_wage > 0:
            ratio = new_wage / old_wage
            if ratio >= WAGE_JUMP:
                found.append(Finding(
                    "임금 급등", key,
                    f"30일 평균임금이 {old_wage:,.0f} → {new_wage:,.0f} 원 "
                    f"({ratio:.1f}배) 입니다. 단위 혼재나 자릿수를 확인하세요.",
                    serious=ratio >= 5))
            elif ratio <= WAGE_DROP:
                found.append(Finding(
                    "임금 급감", key,
                    f"30일 평균임금이 {old_wage:,.0f} → {new_wage:,.0f} 원 "
                    f"({ratio:.0%}) 입니다. 임금피크라면 정상입니다."))

        if (now.plan is not None and was.plan is not None
                and now.plan is not was.plan):
            found.append(Finding(
                "제도 전환", key,
                f"퇴직급여 제도가 {was.plan.value} → {now.plan.value} 로 "
                "바뀌었습니다. 정산손익이 났는지 확인하세요."))

    # ── 사라진 사람 / 되살아난 사람 ──────────────────────────────
    for key in sorted(set(was_active) - set(now_active) - set(now_retired)):
        found.append(Finding(
            "행방불명", key,
            "전기 재직자인데 당기 명부(재직·퇴직) 어디에도 없습니다. "
            "누락이거나 퇴직자로 옮기지 않은 것입니다.",
            serious=True))

    for key in sorted(set(was_retired) & set(now_active)):
        found.append(Finding(
            "퇴직자 재등장", key,
            "전기에 퇴직 처리된 사번이 당기 재직자에 있습니다. "
            "재입사라면 근속 기산일을 확인하세요."))

    return result
