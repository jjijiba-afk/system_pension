"""사번 하나를 같은 기초율로 재산출해 계산 근거를 통째로 보여준다.

전체 산출은 명부 몇백 명의 합계라, "이 사람 채무가 왜 이 금액인가" 는 합계만
봐서는 답할 수 없다. 감사인·회사 담당자의 질문이 늘 개인 단위로 오기 때문에
(퇴직 임박자, 임원, 금액이 큰 사람), 그 한 명을 **전체 산출과 같은 코드로**
다시 계산하면서 연차·퇴직사유별 근거를 한 줄씩 남긴다.

같은 코드(:func:`pension.valuation.value_member`)를 쓰므로 여기 나온 채무의
합은 전체 산출의 그 사람 몫과 원 단위까지 같다 — 별도 재현 로직이 만드는
"조회 화면과 산출 결과가 다른" 사고가 원천적으로 없다.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .assumptions import CAUSE_DEATH, CAUSE_NORMAL, CAUSE_VOLUNTARY
from .normalize import text

__all__ = ["lookup"]


def _matches(member: Any, needle: str) -> bool:
    """사번이 정확히 같으면 참. 사번이 없으면 성명으로라도 찾아 준다."""
    if text(member.employee_id) == needle:
        return True
    return not text(member.employee_id) and text(member.name) == needle


def _date(value: _dt.date | None) -> str:
    return str(value) if value else ""


def _active_block(member: Any, config: Any, assumptions: Any) -> dict[str, Any]:
    """재직자 한 줄(지급 구간 하나)의 재산출 결과와 근거."""
    from .valuation import value_member

    trace: list[dict] = []
    result = value_member(member, config, assumptions, trace=trace)

    profile = {
        "사번": result.employee_id, "성명": result.name,
        "직군": result.job_group, "임직원구분": result.employee_type,
        "성별": result.gender, "생년월일": _date(result.birth_date),
        "입사일자": _date(result.hire_date),
        "중간정산일": _date(result.settlement_date),
        "제도": result.plan,
        "기준일 연령": result.age,
        "기준일 근속": round(result.past_service, 4),
        "정년연령": result.retirement_age,
        "투영연수": result.projection_years,
        "30일 평균임금": result.monthly_wage,
    }
    if member.has_period:
        profile["지급구간"] = (
            f"{_date(member.period_start) or '(입사)'} ~ "
            f"{_date(member.period_end) or '(퇴직)'}"
        )

    applied = {
        "지급률 규정": result.benefit_rule or result.job_group,
        "퇴직률 규정": result.withdrawal_rule or result.job_group,
        "가입자격(최소 근속)": result.min_service_years,
        "지급액 반올림 단위": result.rounding_unit,
    }
    # 정년·적용 여부의 **출처** — "임원인데 왜 65세까지 투영되나 / 퇴직률이 왜
    # 0 인가" 는 늘 여기서 갈린다. 어느 칸이 그렇게 시켰는지를 그대로 적는다.
    index = getattr(member, "job_group_index", None)
    rule = (config.job_group_rules[index]
            if index is not None and index < len(config.job_group_rules)
            else None)
    if member.declared_nra:
        applied["정년연령 근거"] = (
            f"{result.retirement_age}세 — 명부의 개인별 정년 칸 (직군 규정보다 우선)"
        )
    elif (rule is not None and rule.executive_nra
          and result.retirement_age == rule.executive_nra):
        applied["정년연령 근거"] = (
            f"{result.retirement_age}세 — [기본가정] 임원 정년연령 칸"
        )
    elif result.retirement_age:
        applied["정년연령 근거"] = (
            f"{result.retirement_age}세 — 직군 '{result.job_group}' 규칙"
        )
    off = [label for label, on in (
        ("중도퇴직률", member.apply_withdrawal),
        ("사망률", member.apply_mortality),
        ("승급률", member.apply_promotion),
        ("Base-up", member.apply_base_up),
    ) if not on]
    if off:
        applied["미반영 가정"] = (
            "·".join(off) + " — [기본가정] 적용 여부 칸이 '미반영' 입니다. "
            "중도퇴직률 미반영은 정년까지 전원 근무한다고 보는 설정입니다"
        )
    if result.extra_payment:
        applied["명부 추가지급 기본급"] = (
            f"{result.extra_payment:,.0f}원 — 산출에 더해지지 않았습니다. "
            "얹으려면 [퇴직사유] 표의 가산액에 적으세요"
        )
    if result.db_ratio != 1.0:
        applied["DB 비중 (혼합형)"] = (
            f"{result.db_ratio:.2f} — 급여의 이만큼만 채무로 잡습니다"
        )
    if result.progressive_service:
        applied["누진 보전 구간"] = (
            f"{result.progressive_service:g}년까지 연 {result.progressive_rate:g}배"
        )
    if member.service_add_years or member.service_deduct_years:
        applied["지급률 근속 가산·차감"] = (
            f"+{member.service_add_years:g}년 / −{member.service_deduct_years:g}년 "
            "— 급여 배수를 찾는 근속에만 반영, 귀속(할당)은 실제 근속"
        )

    return {
        "excluded": result.excluded_reason,
        "profile": profile,
        "applied": applied,
        "result": {
            "확정급여채무 (DBO)": result.dbo,
            # 사유별 몫 — 셋의 합이 위의 채무와 같다. 사유마다 지급률이 다른
            # 규정은 총액만으로는 검산이 되지 않는다.
            "DBO — 정년": result.by_cause.get(CAUSE_NORMAL, {}).get("dbo", 0.0),
            "DBO — 중도": result.by_cause.get(CAUSE_VOLUNTARY, {}).get("dbo", 0.0),
            "DBO — 사망": result.by_cause.get(CAUSE_DEATH, {}).get("dbo", 0.0),
            "당기근무원가": result.service_cost,
            "이자원가 (차기)": result.interest_cost,
            "지급률 (누적 배수)": result.accrued_multiple,
            "퇴직급여추계액": result.accrued_benefit,
            "듀레이션 (년)": result.duration,
        },
        # 사유별로 갈라 놓은 몫. 합은 위의 채무·근무원가와 같다.
        "by_cause": [
            {"cause": cause, "dbo": share["dbo"],
             "service_cost": share["service_cost"], "benefit_pv": share["benefit_pv"]}
            for cause, share in result.by_cause.items()
        ],
        "trace": trace,
    }


def _longterm_block(member: Any, config: Any, assumptions: Any) -> dict[str, Any] | None:
    """장기급여 규정이 있으면 그 채무도 같이 재산출한다."""
    if not assumptions.longterm_items(member.rules.longterm_benefit or member.job_group):
        return None
    from .longterm import value_longterm_member

    trace: list[dict] = []
    result = value_longterm_member(member, config, assumptions, trace=trace)
    return {
        "excluded": result.excluded_reason,
        "지급유형": result.benefit_kind,
        "1일 통상임금": result.daily_base_pay,
        "다음 지급 근속": result.next_milestone,
        "남은 지급 시점 수": result.milestone_count,
        "result": {
            "장기급여채무": result.dbo,
            "당기근무원가": result.service_cost,
            "이자원가 (차기)": result.interest_cost,
        },
        "trace": trace,
    }


def _retired_block(member: Any) -> dict[str, Any]:
    return {
        "사번": text(member.employee_id), "성명": text(member.name),
        "퇴직일자": _date(member.exit_date),
        "퇴직사유": member.reason_raw or (member.reason.value if member.reason else ""),
        "퇴직급여 지급총액": member.total_payment,
        "사외적립 지급액": member.fund_payment,
        "장기급여 지급액": member.longterm_payment,
    }


def lookup(
    roster_path: str,
    assumptions_path: str,
    employee_id: str,
    base_date: _dt.date | None = None,
) -> dict[str, Any]:
    """사번(없으면 성명)으로 찾아 그 사람만 다시 산출한다.

    :returns: ``rows`` 에 재직 줄별 상세(같은 사번이 지급 구간으로 나뉘어
        있으면 여러 줄), ``retired`` 에 퇴직자명부에서 찾은 내역.
    :raises ValueError: 어느 명부에도 없는 사번일 때.
    """
    from .pipeline import load_inputs

    needle = text(employee_id)
    if not needle:
        raise ValueError("사번을 입력하세요")

    config, roster, assumptions, _log, _general = load_inputs(
        roster_path, assumptions_path, base_date
    )

    rows = [m for m in roster.active if _matches(m, needle)]
    retired = [m for m in roster.retired if _matches(m, needle)]
    if not rows and not retired:
        raise ValueError(
            f"사번 '{needle}' 을(를) 재직자·퇴직자명부 어디에서도 찾지 못했습니다"
        )

    blocks = [_active_block(m, config, assumptions) for m in rows]
    longterm = [_longterm_block(m, config, assumptions) for m in rows]

    total = {
        "확정급여채무 (DBO)": sum(b["result"]["확정급여채무 (DBO)"] for b in blocks),
        "당기근무원가": sum(b["result"]["당기근무원가"] for b in blocks),
        "이자원가 (차기)": sum(b["result"]["이자원가 (차기)"] for b in blocks),
    }
    # 지급구간이 여럿이면 구간별 사유 몫을 하나로 합쳐 준다.
    causes: dict[str, dict[str, float]] = {}
    for block in blocks:
        for share in block["by_cause"]:
            into = causes.setdefault(
                share["cause"], {"dbo": 0.0, "service_cost": 0.0, "benefit_pv": 0.0})
            into["dbo"] += share["dbo"]
            into["service_cost"] += share["service_cost"]
            into["benefit_pv"] += share["benefit_pv"]
    for block in longterm:
        if block and not block["excluded"]:
            total["장기급여채무"] = (
                total.get("장기급여채무", 0.0) + block["result"]["장기급여채무"]
            )

    return {
        "base_date": str(config.base_date),
        "discount_rate": assumptions.discount.level_rate,
        "rows": blocks,
        "longterm": [b for b in longterm if b],
        "retired": [_retired_block(m) for m in retired],
        "total": total,
        "by_cause": [{"cause": cause, **share} for cause, share in causes.items()],
    }
