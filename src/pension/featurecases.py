"""특이사항 한 가지씩만 담은 시험 명부 한 벌.

:mod:`pension.rostergen` 의 '자료불량' 명부는 특이사항 열두 가지를 **한 사람씩**
심어 한 파일에 몰아 넣는다. 무엇이 있는지 훑어보기에는 좋지만, 산출 결과를 놓고
"이 숫자가 왜 이렇게 나왔나" 를 따지기에는 쓸 수 없다. 한 사람만 달라진 것이라
채무 전체에서 티가 안 나고, 열두 가지가 서로 얽혀 무엇이 무엇을 움직였는지
가릴 수 없기 때문이다.

여기서는 반대로 만든다.

* **사람은 한 벌뿐이다.** 같은 씨앗으로 한 번 뽑아 모든 명부가 나눠 쓴다.
* **명부 하나에 특이사항 하나.** 그것도 한 사람이 아니라 **전원 또는 한 직군**
  전체에 건다.
* 특이사항이 하나도 없는 **기준 명부** 를 같이 낸다.

그러면 명부 둘의 채무 차이가 곧 **그 특이사항이 만든 차이** 다. 사람도 가정도
같으므로 달리 설명할 것이 없다.

기초율은 한 벌을 모든 명부가 함께 쓴다. 명부마다 다른 기초율을 붙이면 차이가
어디서 왔는지 다시 알 수 없다.
"""

from __future__ import annotations

import copy
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

from .rostergen import (
    BASE_DATE,
    CaseSpec,
    make_population,
    write_case_assumptions,
    write_case_roster,
)

__all__ = [
    "FEATURES",
    "BASE_SPEC",
    "FeatureSpec",
    "Measured",
    "measure_feature_pack",
    "report_text",
    "write_feature_pack",
    "write_feature_rosters",
]

#: 방향 표시. 기준 명부보다 채무가 어느 쪽으로 움직여야 하는가.
UP: Final = "증가"
DOWN: Final = "감소"
FLAT: Final = "변화 없음"
EITHER: Final = "판단 필요"


@dataclass(slots=True, frozen=True)
class FeatureSpec:
    """특이사항 하나."""

    key: str
    """파일 이름에 쓸 짧은 이름."""
    title: str
    """특이사항 이름."""
    detail: str
    """무엇인지 한 줄."""
    scope: str
    """적용 범위. ``"전원"`` 또는 직군 이름."""
    expect: str
    """기준 명부 대비 기대하는 채무 방향."""
    why: str
    """왜 그 방향인지. 방향이 어긋났을 때 어디를 볼지 알려면 근거가 있어야 한다."""


#: 사람을 뽑는 기준. 특이사항이 하나도 없는 평범한 명부다 — 여기에 한 가지씩만
#: 얹어야 차이가 그 한 가지 때문이라고 말할 수 있다.
BASE_SPEC: Final = CaseSpec(
    key="특이사항기준",
    title="0_기준",
    summary="특이사항이 하나도 없는 기준 명부입니다. 아래 명부들은 모두 이 "
            "명부와 같은 사람이며, 한 가지씩만 다릅니다.",
    active=290, retired=28,
    groups=(("정규직", 0.82), ("계약직", 0.12), ("임원", 0.06)),
    notes=(
        "· 이 명부에는 특이사항이 없습니다. 다른 명부와 맞대어 보는 기준선입니다.",
        "· 같은 벌의 명부는 사람도 기초율도 같고 한 가지씩만 다릅니다.",
    ),
)


def _staff(row: dict[str, Any]) -> bool:
    return str(row.get("employee_type")) != "임원"


def _in_group(row: dict[str, Any], scope: str) -> bool:
    if scope == "전원":
        return True
    if scope == "임원":
        return not _staff(row)
    return str(row.get("job_group")) == scope


# ── 특이사항 하나하나 ────────────────────────────────────────────
# 손대는 것은 명부 칸뿐이다. 산출 쪽은 건드리지 않는다 — 회사가 그렇게 적어
# 보낸 명부를 그대로 재현하는 것이 목적이다.

def _leave(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["leave_days"] = 365
        row["note"] = "휴직 차감 365일"


def _settlement(rows: list[dict[str, Any]], base: _dt.date) -> None:
    settled = base - _dt.timedelta(days=int(5 * 365.25))
    for row in rows:
        hire = _dt.date.fromisoformat(str(row["hire_date"])[:10])
        if hire >= settled:
            continue
        served = (settled - hire).days / 365.25
        row["settlement_date"] = settled.isoformat()
        row["settlement_amount"] = int(
            float(row["monthly_wage"]) * served / 1_000) * 1_000
        row["note"] = "전원 중간정산 — 근속을 정산일부터 다시 셈"


def _wage_peak(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["wage_peak_age"] = 57
        row["note"] = "임금피크 만 57세 진입"


def _multiple(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["payout_multiple"] = 2.0
        row["note"] = "임원 퇴직금 지급규정 — 2배수"


def _late_nra(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        # 직군 규칙의 임원 정년이 65세다. 65 를 적으면 규칙과 같아 아무것도
        # 달라지지 않는다 — 명부 칸이 규칙을 실제로 덮는지 보려면 달라야 한다.
        row["declared_nra"] = 68
        row["note"] = "임원 정년 만 68세 (직군 규칙 65세를 명부에서 덮음)"


def _progressive(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        served = (base - _dt.date.fromisoformat(str(row["hire_date"])[:10])).days / 365.25
        row["progressive_service"] = round(served * 0.5, 1)
        row["progressive_rate"] = 1.5
        row["note"] = "연봉제 전환 이전 근속분 누진 배수 1.5 보전"


def _honorary(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["honorary_wage"] = int(float(row["monthly_wage"]) * 1.15 / 1_000) * 1_000
        row["note"] = "명예퇴직 산정용 임금 별도 (평균임금의 115%)"


def _extra_pay(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["extra_pay_base_wage"] = int(
            float(row["monthly_wage"]) * 0.8 / 1_000) * 1_000
        row["note"] = "정년퇴직 시 기본급 1개월분 추가지급"


def _mixed_plan(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["db_ratio"] = 0.5
        row["note"] = "혼합형 — DC 50% / DB 50%"


def _contract_end(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["remaining_contract_years"] = 2
        row["note"] = "계약 만료까지 2년 — 정년이 아니라 그때 나간다"


def _to_dc(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["plan"] = "DC"
        row["note"] = "DC 전환 — 확정급여채무 대상 아님"


def _thousand_won(rows: list[dict[str, Any]], base: _dt.date) -> None:
    for row in rows:
        row["monthly_wage"] = int(float(row["monthly_wage"]) / 1_000)
        row["note"] = "월평균임금을 천원 단위로 기재"


_APPLY: Final[dict[str, Callable[[list[dict[str, Any]], _dt.date], None]]] = {
    "휴직차감": _leave,
    "중간정산": _settlement,
    "임금피크": _wage_peak,
    "지급배수": _multiple,
    "정년연장": _late_nra,
    "누진보전": _progressive,
    "명예퇴직임금": _honorary,
    "추가지급": _extra_pay,
    "DC전환": _to_dc,
    "혼합형": _mixed_plan,
    "계약만료": _contract_end,
    "임금단위": _thousand_won,
}


FEATURES: Final[tuple[FeatureSpec, ...]] = (
    FeatureSpec(
        key="휴직차감", title="휴직 차감 (365일)",
        detail="전원의 근속 기산일을 365일 뒤로 민다.",
        scope="전원", expect=DOWN,
        why="근속이 한 해 줄면 지급률도 귀속액도 준다. 예외 없이 감소한다.",
    ),
    FeatureSpec(
        key="중간정산", title="전원 중간정산",
        detail="5년 전 전원 중간정산. 근속을 그 날부터 다시 센다.",
        scope="전원", expect=DOWN,
        why="과거근속이 5년으로 잘리므로 귀속된 몫이 그만큼 줄어든다.",
    ),
    FeatureSpec(
        key="임금피크", title="임금피크 (만 57세)",
        detail="정규직 전원이 만 57세에 임금피크에 든다. 정년이 그 연령으로 당겨진다.",
        scope="정규직", expect=UP,
        why="귀속된 몫은 과거근속 × 임금 × ((1+임금상승률)÷(1+할인율))^잔여연수 로 "
            "정리된다. 임금상승률이 할인율보다 낮으면 잔여연수가 짧아질수록 현가가 "
            "커진다. 표준 가정이 그 경우라 증가한다 — 두 율의 대소가 뒤집히면 "
            "이 명부의 방향도 뒤집힌다.",
    ),
    FeatureSpec(
        key="지급배수", title="임원 개인 지급배수 2.0",
        detail="임원 전원에게 개인 지급배수 2.0 을 건다.",
        scope="임원", expect=UP,
        why="지급률 규정이 내는 배수에 명부의 배수가 곱해진다. 임원 몫이 정확히 "
            "두 배가 되므로, 늘어난 금액은 기준 명부의 임원 채무와 같아야 한다 — "
            "그것이 이 명부의 검산이다. 규정이 수식 방식이고 식이 이미 `배수` 를 "
            "쓰고 있으면 밖에서 다시 곱하지 않는다(두 번 먹지 않게).",
    ),
    FeatureSpec(
        key="정년연장", title="임원 정년 68세 (명부가 직군 규칙을 덮음)",
        detail="임원 정년이 직군 규칙으로는 65세인데 명부에 68세로 적혀 왔다.",
        scope="임원", expect=DOWN,
        why="명부의 정년연령이 직군 규칙을 덮는지 보는 명부다. 값이 닿으면 지급 "
            "시점이 3년 멀어지는데, 임금상승률이 할인율보다 낮으면 현가는 오히려 "
            "줄어든다(임금피크와 정확히 반대). 변화가 없다면 명부 칸이 규칙에 "
            "묻힌 것이다.",
    ),
    FeatureSpec(
        key="누진보전", title="연봉제 전환 누진 보전",
        detail="정규직 전원의 근속 절반에 누진 배수 1.5 를 보전한다.",
        scope="정규직", expect=UP,
        why="같은 근속에 배수가 1.0 에서 1.5 로 오른 구간이 생긴다.",
    ),
    FeatureSpec(
        key="명예퇴직임금", title="명예퇴직 산정용 임금",
        detail="전원에게 명예퇴직 산정용 임금(평균임금의 115%)이 적혀 있다.",
        scope="전원", expect=FLAT,
        why="명예퇴직으로 나가는 사람이 없으면 쓰이지 않는 칸이다. 채무가 움직이면 "
            "쓰이지 않아야 할 값이 새어 들어간 것이다.",
    ),
    FeatureSpec(
        key="추가지급", title="정년퇴직 시 기본급 추가지급",
        detail="전원에게 정년 시 기본급 1개월분(평균임금의 80%)을 얹어 준다.",
        scope="전원", expect=UP,
        why="정년으로 나가는 몫에 정액이 더해진다. 정년 비중만큼 늘어난다.",
    ),
    FeatureSpec(
        key="DC전환", title="계약직 DC 전환",
        detail="계약직 전원이 DC 로 전환했다.",
        scope="계약직", expect=DOWN,
        why="DC 가입자는 확정급여채무에서 통째로 빠진다.",
    ),
    FeatureSpec(
        key="혼합형", title="혼합형 DB 50%",
        detail="전원이 혼합형이고 DB 비중이 절반이다.",
        scope="전원", expect=DOWN,
        why="DC 로 낸 몫은 낸 순간 회사 손을 떠나 확정급여채무가 아니다. "
            "DB 비중만 남으므로 채무는 대략 절반이 된다 — 정액 위로금이 없는 "
            "명부라 여기서는 거의 정확히 절반이어야 한다.",
    ),
    FeatureSpec(
        key="계약만료", title="계약직 잔여계약기간 2년",
        detail="계약직 전원이 정년이 아니라 2년 뒤 계약 만료로 나간다.",
        scope="계약직", expect=UP,
        why="지급 시점이 정년에서 2년 뒤로 크게 당겨진다. 임금상승률이 할인율보다 "
            "낮으면 잔여연수가 짧아질수록 현가가 커지고(임금피크와 같은 이치), "
            "그 사이에 중도퇴직·사망으로 빠질 틈도 줄어 도달확률이 오른다. "
            "둘 다 같은 쪽을 가리키므로 증가한다.",
    ),
    FeatureSpec(
        key="임금단위", title="임금 단위 혼재 (천원)",
        detail="전원의 월평균임금이 천원 단위로 적혀 왔다. **자료 오류** 다.",
        scope="전원", expect=DOWN,
        why="임금이 1/1000 이 되므로 채무도 그만큼 줄어든다. 검증이 [평균임금 하한 "
            "점검액] 으로 전원을 잡아 주어야 하고, 잡지 못하면 그것이 문제다.",
    ),
)


def _roster_name(index: int, feature: FeatureSpec) -> str:
    return f"{index}_{feature.key}"


def write_feature_rosters(
    directory: str | Path, *, seed: int = 20251231,
    base_date: _dt.date | None = None,
) -> list[Path]:
    """기준 명부 + 특이사항 명부들 + 공통 기초율.

    사람은 한 번만 뽑아 모든 명부가 나눠 쓴다. 명부마다 다시 뽑으면 채무 차이가
    특이사항 때문인지 사람이 달라서인지 가릴 수 없다.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    base = base_date or BASE_DATE

    actives, retirees = make_population(BASE_SPEC, seed=seed, base_date=base)
    made = [write_case_roster(
        BASE_SPEC, directory / f"{BASE_SPEC.title}.xlsx", seed=seed, base_date=base,
        population=(copy.deepcopy(actives), copy.deepcopy(retirees)),
    )]

    for index, feature in enumerate(FEATURES, start=1):
        rows = copy.deepcopy(actives)
        others = copy.deepcopy(retirees)
        targets = [row for row in rows if _in_group(row, feature.scope)]
        _APPLY[feature.key](targets, base)

        spec = CaseSpec(
            key=feature.key,
            title=_roster_name(index, feature),
            summary=f"특이사항 하나만 담은 명부입니다 — {feature.title}. "
                    f"적용 범위: {feature.scope} ({len(targets)}명). {feature.detail}",
            active=BASE_SPEC.active, retired=BASE_SPEC.retired,
            groups=BASE_SPEC.groups,
            # 임금 단위 오류는 하한 점검액이 있어야 검증이 잡는다.
            wage_check=1_000_000 if feature.key == "임금단위" else 0,
            notes=(
                f"· 특이사항: {feature.title} — {feature.detail}",
                f"· 적용 범위: {feature.scope} {len(targets)}명 / 재직 {len(rows)}명",
                f"· 기준 명부(0_기준) 대비 확정급여채무는 {feature.expect} 해야 합니다.",
                f"  {feature.why}",
                "· 사람과 기초율은 기준 명부와 같습니다. 다른 것은 이 한 가지뿐입니다.",
            ),
        )
        made.append(write_case_roster(
            spec, directory / f"{spec.title}.xlsx", seed=seed, base_date=base,
            population=(rows, others),
        ))

    # 기초율은 한 벌. 명부마다 다른 것을 붙이면 차이가 어디서 왔는지 알 수 없다.
    made.append(write_case_assumptions(BASE_SPEC, directory / "기초율.xlsx"))
    return made


# ── 산출해 보기 ──────────────────────────────────────────────────

@dataclass(slots=True)
class Measured:
    """명부 하나를 산출한 결과."""

    name: str
    title: str
    scope: str
    dbo: float
    service_cost: float
    headcount: int
    errors: int
    expect: str = ""
    change: float = 0.0
    """기준 명부 대비 채무 차이."""

    @property
    def ratio(self) -> float:
        return self.change / (self.dbo - self.change) if self.dbo != self.change else 0.0

    @property
    def moved(self) -> str:
        """실제로 움직인 방향. 1원 미만은 '변화 없음' 으로 본다."""
        if abs(self.change) < 1:
            return FLAT
        return UP if self.change > 0 else DOWN

    @property
    def verdict(self) -> str:
        if not self.expect or self.expect == EITHER:
            return "—"
        return "맞음" if self.moved == self.expect else "어긋남"


def measure_feature_pack(
    directory: str | Path, *, base_date: _dt.date | None = None,
) -> list[Measured]:
    """만들어 둔 명부를 전부 산출해 채무를 잰다.

    첫 줄이 기준 명부이고 나머지는 그 대비 차이를 함께 담는다.
    """
    from .pipeline import RunOptions, run_valuation

    directory = Path(directory)
    assumptions = directory / "기초율.xlsx"
    work = directory / "_산출"
    work.mkdir(parents=True, exist_ok=True)

    def once(name: str, title: str, scope: str) -> Measured:
        run = run_valuation(RunOptions(
            roster_path=directory / f"{name}.xlsx",
            assumptions_path=assumptions,
            output_path=work / f"{name}.xlsx",
            include_sensitivity=False, include_longterm=False,
            # 임금 단위 오류 명부는 검증에 걸린다 — 그래도 끝까지 돌려야
            # '얼마나 줄어드는지' 를 보여 줄 수 있다.
            allow_errors=True,
            base_date=base_date,
        ))
        return Measured(
            name=name, title=title, scope=scope,
            dbo=run.valuation.dbo,
            service_cost=run.valuation.service_cost,
            headcount=run.valuation.headcount,
            errors=len(run.issues.errors),
        )

    rows = [once(BASE_SPEC.title, "기준 (특이사항 없음)", "—")]
    baseline = rows[0].dbo
    for index, feature in enumerate(FEATURES, start=1):
        measured = once(_roster_name(index, feature), feature.title, feature.scope)
        measured.expect = feature.expect
        measured.change = measured.dbo - baseline
        rows.append(measured)
    return rows


def report_text(rows: list[Measured], base: _dt.date, seed: int) -> str:
    lines = [
        "특이사항 한 가지씩 — 시험 명부 한 벌",
        "=" * 66,
        "",
        f"산출기준일  {base}",
        f"난수 씨앗   {seed}",
        "",
        "명부 하나에 특이사항 하나만 담았습니다. 한 사람이 아니라 **전원 또는 한",
        "직군 전체** 에 걸었습니다. 사람도 기초율도 모든 명부가 같으므로, 기준",
        "명부와의 채무 차이가 곧 그 특이사항이 만든 차이입니다.",
        "",
        "쓰는 법: 명부와 기초율.xlsx 를 함께 올려 산출하고, 나온 확정급여채무를",
        "아래 값과 맞대어 보십시오.",
        "",
        "[확정급여채무]",
        "",
        f"  {'명부':<16}{'적용 범위':<8}{'확정급여채무':>18}"
        f"{'기준 대비':>16}{'':>9}{'기대':>8}{'판정':>7}",
        "  " + "-" * 78,
    ]
    for row in rows:
        share = f"{row.ratio:+.2%}" if row.change else ""
        change = f"{row.change:+,.0f}" if row.name != rows[0].name else ""
        lines.append(
            f"  {row.name:<16}{row.scope:<8}{row.dbo:>18,.0f}"
            f"{change:>16}{share:>9}{row.expect:>8}{row.verdict:>7}"
        )
    lines += [
        "",
        "[각 명부가 무엇을 담고 있는지]",
        "",
        f"  0_기준 — {BASE_SPEC.summary}",
    ]
    for index, feature in enumerate(FEATURES, start=1):
        lines += [
            "",
            f"  {_roster_name(index, feature)} — {feature.title}",
            f"    {feature.detail}",
            f"    적용 범위: {feature.scope}",
            f"    기대: 기준 대비 {feature.expect}. {feature.why}",
        ]

    lines += [
        "",
        "[이 숫자를 어디까지 믿을 수 있는지]",
        "",
        "  위 확정급여채무는 **이 프로그램이 산출한 값** 입니다. 손으로 따로 검산한",
        "  값이 아니므로, 엔진이 틀렸다면 이 값도 같이 틀립니다. 그러니 '정답' 이",
        "  아니라 **기준값** 으로 쓰십시오 — 고친 뒤 이 값이 달라지면 무엇인가",
        "  바뀐 것이고, 그 변화가 의도한 것인지 확인하면 됩니다.",
        "",
        "  실제로 검산이 되는 것은 **기준 대비 방향** 입니다. 사람도 가정도 같고",
        "  한 칸만 달라졌으므로, 채무가 어느 쪽으로 움직여야 하는지는 계산 없이도",
        "  말할 수 있습니다. 위 표의 [기대]·[판정] 이 그것입니다. '어긋남' 이 하나라도",
        "  나오면 그 명부부터 보십시오.",
        "",
        "  값 자체를 손으로 검산하려면 탈퇴가 하나뿐인 작은 사례여야 합니다.",
        "  정년 하나만 있는 한 사람이면 확정급여채무는 이렇게 닫힌 식으로 떨어집니다.",
        "",
        "      DBO = (총근속 × 임금 × 지급률) × (과거근속 ÷ 총근속) ÷ (1+할인율)^잔여연수",
        "",
        "  이 검산은 tests/test_valuation.py 의 TestSingleDecrementCase 가 이미",
        "  하고 있습니다. 290명 명부를 손으로 검산할 방법은 없습니다 — 사람마다",
        "  연차별로 퇴직확률을 곱해 더하는 계산이라, 손계산이 곧 엔진을 한 번 더",
        "  만드는 일이 됩니다.",
        "",
        "이 명부는 난수로 만든 가상 자료입니다. 성명·사번은 실제 인물과 무관합니다.",
    ]
    return "\n".join(lines) + "\n"


def write_feature_pack(
    directory: str | Path, *, seed: int = 20251231,
    base_date: _dt.date | None = None, measure: bool = True,
) -> list[Path]:
    """명부 한 벌과 안내문을 만든다.

    :param measure: 참이면 명부를 전부 산출해 확정급여채무 표까지 안내문에
        적는다. 명부 수만큼 산출이 도는 만큼 시간이 걸린다.
    """
    directory = Path(directory)
    base = base_date or BASE_DATE
    made = write_feature_rosters(directory, seed=seed, base_date=base)

    report = directory / "특이사항_한가지씩_안내.txt"
    rows = measure_feature_pack(directory, base_date=base) if measure else []
    if rows:
        report.write_text(report_text(rows, base, seed), encoding="utf-8")
    else:
        report.write_text(
            report_text(
                [Measured(name=BASE_SPEC.title, title="기준", scope="—",
                          dbo=0.0, service_cost=0.0, headcount=0, errors=0)],
                base, seed,
            ),
            encoding="utf-8",
        )
    made.append(report)
    return made
