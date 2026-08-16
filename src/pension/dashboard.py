"""산출 한 회차를 그림으로 보는 데 필요한 값 한 벌.

산출 결과 엑셀은 감사 조서로는 좋지만, 결산 회의에서 "왜 이 금액인가"를
설명하기에는 열어 볼 시트가 너무 많다. 이 모듈은 :class:`~pension.pipeline.PensionRun`
하나에서 화면이 그릴 값(지표·직군·증감·자산·민감도·만기·장기급여, 그리고
대표 1인의 연차별 계산 근거)을 뽑아 준다.

**계산은 하지 않는다.** 대표 1인의 시나리오만 :func:`~pension.valuation.value_member`
를 다시 부르는데, 그것도 엔진의 같은 함수다. 그래서 화면 숫자와 결과 엑셀·
보고서가 어긋날 수 없다.
"""

from __future__ import annotations

from typing import Any

from .normalize import text

__all__ = ["build", "pick_member"]

#: 대표로 세울 사람을 고를 때 보는 구간. 정년 임박자는 투영이 두어 해뿐이라
#: 곡선이 보이지 않고, 신입은 채무가 작아 표가 심심하다.
_PREFER_AGE = (38, 50)
_PREFER_SERVICE = 8.0


def pick_member(valuation: Any, employee_id: str = "") -> Any:
    """화면에 세울 재직자 한 명.

    사번을 주면 그 사람. 없으면 근속이 어느 정도 쌓인 중견 직원 중 채무가 가장
    큰 사람을 고른다 — 연차별 곡선과 민감도가 함께 보이는 자리다.
    """
    alive = [m for m in valuation.members if not m.excluded_reason]
    if not alive:
        raise ValueError("산출 대상 인원이 없습니다")

    wanted = text(employee_id)
    if wanted:
        for m in alive:
            if text(m.employee_id) == wanted or text(m.name) == wanted:
                return m
        raise ValueError(f"사번 '{wanted}' 을(를) 산출 대상에서 찾지 못했습니다")

    low, high = _PREFER_AGE
    middling = [m for m in alive
                if low <= m.age <= high and m.past_service >= _PREFER_SERVICE]
    return max(middling or alive, key=lambda m: m.dbo)


def _scenarios(member: Any, config: Any, assumptions: Any) -> list[dict[str, Any]]:
    """기준 가정과 충격 가정별로 그 한 사람을 다시 산출한다."""
    from .sensitivity import DEFAULT_SHOCKS, apply_shock
    from .valuation import value_member

    def once(assum: Any, label: str) -> dict[str, Any]:
        trace: list[dict] = []
        result = value_member(member, config, assum, trace=trace)
        return {
            "label": label, "dbo": result.dbo, "service_cost": result.service_cost,
            "duration": result.duration, "accrued": result.accrued_benefit,
            "trace": trace,
        }

    made = [once(assumptions, "기준")]
    made += [once(apply_shock(assumptions, s), s.name) for s in DEFAULT_SHOCKS]
    return made


def _member_block(run: Any, employee_id: str) -> dict[str, Any]:
    picked = pick_member(run.valuation, employee_id)
    # 같은 사번이 지급구간으로 여러 줄 오는 명부가 있다. 근속으로 한 번 더
    # 좁혀 그 줄을 집는다 — 다른 구간을 집으면 옆의 채무와 그림이 어긋난다.
    same = [m for m in run.roster.active if m.employee_id == picked.employee_id]
    member = next(
        (m for m in same
         if abs(m.service_years(run.config.base_date) - picked.past_service) < 0.01),
        same[0],
    )
    # 정년·적용 여부의 출처 — "임원인데 왜 이렇게 길게 투영되나" 는 늘 여기서
    # 갈리므로, 어느 칸이 그렇게 시켰는지 한 줄로 같이 내보낸다.
    notes = []
    index = getattr(member, "job_group_index", None)
    rule = (run.config.job_group_rules[index]
            if index is not None and index < len(run.config.job_group_rules)
            else None)
    if member.declared_nra:
        notes.append(f"정년 {picked.retirement_age}세 = 명부의 개인별 정년 칸")
    elif (rule is not None and rule.executive_nra
          and picked.retirement_age == rule.executive_nra):
        notes.append(f"정년 {picked.retirement_age}세 = [임원 정년연령] 칸")
    for label, on in (
        ("중도퇴직률", member.apply_withdrawal),
        ("사망률", member.apply_mortality),
    ):
        if not on:
            notes.append(f"{label} 미반영 (기본가정 설정)")

    return {
        "profile": {
            "사번": picked.employee_id, "성명": picked.name,
            "직군": picked.job_group, "성별": picked.gender,
            "연령": picked.age, "근속": round(picked.past_service, 2),
            "정년": picked.retirement_age, "투영연수": picked.projection_years,
            "월평균임금": picked.monthly_wage, "추계액": picked.accrued_benefit,
            "지급률규정": picked.benefit_rule or picked.job_group,
            "가정메모": " · ".join(notes),
        },
        "scenarios": _scenarios(member, run.config, run.assumptions),
    }


def _curves(scale: Any) -> dict[str, dict[str, float]]:
    return {name: dict(sorted(curve.points.items()))
            for name, curve in scale.items() if curve and curve.points}


def build(run: Any, employee_id: str = "") -> dict[str, Any]:
    """산출 한 회차를 화면이 그릴 수 있는 모양으로.

    :param employee_id: 해부해 볼 사번. 비우면 대표 1인을 골라 준다.
    """
    from .webreport import maturity_buckets

    val = run.valuation
    roll, assets, lt = run.rollforward, run.plan_assets, run.longterm
    info = run.general_info
    single = (run.assumptions.discount.flat
              if run.assumptions.discount.flat is not None
              else val.single_discount_rate())

    # 전기를 연결하지 않으면 파이프라인이 '최초 인식' 증감표를 만든다. 기초가
    # 0인 표를 공시 서식으로 보여 주면 오해를 사므로 화면에서도 감춘다.
    if roll is not None and not roll.opening_dbo and not roll.interest_cost:
        roll = None

    paid = dict(maturity_buckets(val.benefit_cash_flows()))
    data: dict[str, Any] = {
        "base_date": str(run.config.base_date),
        "single_rate": single,
        "grade": info.credit_grade if info is not None else "",
        "period": [str(info.period_start) if info and info.period_start else "",
                   str(info.period_end) if info and info.period_end else ""],
        "totals": {
            "headcount": val.headcount, "dbo": val.dbo,
            "sc": val.service_cost, "ic": val.interest_cost,
            "duration": val.duration, "accrued": val.accrued_benefit,
            "lt_dbo": lt.dbo if lt else 0.0,
            "lt_sc": lt.service_cost if lt else 0.0,
            "lt_ic": lt.interest_cost if lt else 0.0,
            "lt_head": lt.headcount if lt else 0,
        },
        # 껐는지, 켰는데 대상자가 없는지는 화면에서 다르게 말해야 한다.
        "has_longterm": lt is not None,
        "groups": [{"name": n, "n": c, "dbo": d, "sc": s}
                   for n, (c, d, s) in val.by_job_group().items()],
        # 퇴직사유별 몫. 사유마다 지급률이 다른 규정에서는 합계만으로 검산이
        # 안 된다 — 한 칸을 잘못 넣어도 총액은 조금 움직일 뿐이다.
        "causes": [{"name": name, "dbo": share["dbo"], "sc": share["service_cost"],
                    "pv": share["benefit_pv"]}
                   for name, share in val.by_cause().items()],
        "excluded": val.exclusion_summary(),
        "rollforward": [[k, a] for k, a in roll.as_rows()] if roll else [],
        "assets": [[k, a] for k, a in assets.as_rows()] if assets else [],
        "net": [[k, a] for k, a in assets.net_rows()] if assets else [],
        "funded": assets.funded_ratio if assets else 0.0,
        "asset_breakdown": dict(info.assets.breakdown) if info else {},
        "asset_quoted": dict(info.assets.quoted) if info else {},
        "events": [[k, v] for k, v in run.events.as_rows()],
        "assumption_steps": [[k, v] for k, v in (roll.assumption_steps if roll else [])],
        "ceiling": ({"limit": assets.asset_ceiling, "surplus": assets.surplus,
                     "effect": assets.ceiling_effect}
                    if assets is not None and assets.asset_ceiling is not None else None),
        "longterm_roll": ([[k, v] for k, v in run.longterm_rollforward.as_rows()]
                          if run.longterm_rollforward else []),
        "projection": ({"expense": [[k, v] for k, v in run.projection.expense_rows()],
                        "dbo": [[k, v] for k, v in run.projection.dbo_rows()],
                        "assets": ([[k, v] for k, v in run.projection.asset_rows()]
                                   if run.projection.has_assets else [])}
                       if run.projection is not None else None),
        "sensitivity": ([[c.name, c.dbo, c.change, c.change_ratio]
                         for c in run.sensitivity.cases] if run.sensitivity else []),
        "maturity": [[k, a, paid.get(k, 0.0)]
                     for k, a in maturity_buckets(val.cash_flows())],
        "issues": {"오류": len(run.issues.errors), "경고": len(run.issues.warnings)},
        "curves": {
            "중도퇴직률": _curves(run.assumptions.withdrawal.curves),
            "승급률": _curves(run.assumptions.salary.promotion.curves),
            "사망률": {"남자": dict(sorted(run.assumptions.mortality.male.points.items())),
                     "여자": dict(sorted(run.assumptions.mortality.female.points.items()))},
        },
        "curve_axis": {
            "중도퇴직률": run.assumptions.withdrawal.basis,
            "승급률": run.assumptions.salary.promotion.basis,
            "사망률": "연령",
        },
    }
    data.update(_member_block(run, employee_id))
    return data
