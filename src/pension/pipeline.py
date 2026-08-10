"""명부 → 검증 → 계리산출 전 과정을 묶는 실행 계층.

GUI 와 CLI 는 모두 이 모듈만 호출한다.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from .assumptions import Assumptions, load_assumptions
from .config import CalculationConfig, read_config
from .errors import IssueLog, PensionDataError
from .longterm import LongTermResult, value_longterm
from .models import Roster
from .normalize import BenefitPlan, RetirementReason
from .planassets import PlanAssets, build_plan_assets
from .projection import Projection, project_next_year
from .readers import read_roster
from .rollforward import (
    LongTermRollForward,
    RollForward,
    build_longterm_rollforward,
    build_rollforward,
    initial_period,
)
from .sensitivity import DEFAULT_SHOCKS, SensitivityResult, Shock, run_sensitivity
from .upload import build_upload
from .valuation import ValuationResult, value_roster

__all__ = [
    "PensionRun",
    "PlanAssetInput",
    "PriorPeriod",
    "RunOptions",
    "load_inputs",
    "run_valuation",
]

Progress = Callable[[str, float], None]
"""진행상황 콜백. ``(메시지, 0.0~1.0)``."""


def _noop(_message: str, _fraction: float) -> None:
    return None


@dataclass(slots=True)
class PriorPeriod:
    """증감분석에 쓸 전기 산출 결과."""

    dbo: float = 0.0
    """전기말 확정급여채무."""
    service_cost: float = 0.0
    """당기에 인식할 근무원가(전기 가정으로 산출한 값)."""
    discount_rate: float = 0.0
    """전기말 할인율. 이자원가 산정에 쓴다."""
    assumptions_path: str = ""
    """전기 기초율 파일. 주면 경험조정과 가정변경효과를 나눠 계산한다."""
    past_service_cost: float = 0.0
    """당기 제도개정으로 생긴 과거근무원가.

    비워 두면 전기 기초율을 준 경우에 한해 **지급률 규정 변경분을 자동으로**
    계산한다. 정년 연장처럼 지급률 밖에서 일어난 개정은 프로그램이 알 수 없으니
    직접 넣어야 하고, 넣으면 자동 계산 대신 그 값을 쓴다.
    """
    longterm_dbo: float = 0.0
    """전기말 장기종업원급여채무. 주면 장기급여 증감표를 만든다."""
    settlement_obligation: float = 0.0
    """정산(중간정산·전출)으로 **소멸한** 확정급여채무.

    전기 개인별 결과에서 해당자의 채무를 합쳐 넣는다. 지급액과의 차이가
    정산손익이 된다. 0 이면 정산손익을 인식하지 않는다(종전 동작)."""

    def is_empty(self) -> bool:
        return self.dbo == 0.0 and self.service_cost == 0.0


@dataclass(slots=True)
class PlanAssetInput:
    """사외적립자산 입력. 신탁회사 명세서에서 그대로 옮긴다."""

    opening_fair_value: float = 0.0
    """기초 공정가치(전기말 잔액)."""
    closing_fair_value: float = 0.0
    """기말 공정가치(결산일 잔액)."""
    contributions: float = 0.0
    """당기 부담금 납입액."""
    benefits_paid: float = 0.0
    """자산에서 직접 지급된 퇴직급여. 0 이면 명부의 사외자산 지급액을 쓴다."""
    unpaid_benefits: float = 0.0
    """미지급 퇴직급여. 퇴직했으나 결산일까지 지급하지 않은 금액."""
    asset_ceiling: float | None = None
    """자산인식상한(문단 64). 초과적립일 때만 뜻이 있다. ``None`` 이면 미적용."""
    expected_contributions: float = 0.0
    """차년도 예상 부담금. 비우면 당기 납입액을 그대로 쓴다(문단 147(b))."""

    def is_empty(self) -> bool:
        return not (self.opening_fair_value or self.closing_fair_value
                    or self.contributions or self.unpaid_benefits)


@dataclass(slots=True)
class RunOptions:
    """산출 옵션."""

    roster_path: Path
    assumptions_path: Path
    output_path: Path
    base_date: _dt.date | None = None
    """산출기준일을 덮어쓴다. 비우면 명부 ``Input`` 시트의 값을 쓴다.

    명부를 고치지 않고 기준일만 바꿔 보고 싶은 일이 잦다(가결산·기준일 확정 전
    시산). 엑셀을 열어 고치게 하면 원본 명부가 회차마다 달라진다.
    """
    period_start: _dt.date | None = None
    """산출 시작일(기초일, 보통 직전 결산일). 이자원가를 이 기간으로 환산한다.

    비우면 1년으로 본다. 결산기가 바뀌어 기간이 1년이 아닌 회차에서 이자원가가
    과대·과소 계상되는 것을 막는다.
    """
    include_sensitivity: bool = True
    include_longterm: bool = True
    split_remeasurement: bool = False
    """가정변경효과를 사망률·퇴직률·임금상승률·할인율로 쪼갤지.

    가정을 하나씩 갈아 끼우며 재는 것이라 **명부 전체 산출이 네 번 더** 돈다.
    전기 기초율을 준 회차에서만 의미가 있고, 없으면 조용히 건너뛴다.
    """
    shocks: tuple[Shock, ...] = DEFAULT_SHOCKS
    prior: PriorPeriod = field(default_factory=PriorPeriod)
    plan_assets: PlanAssetInput = field(default_factory=PlanAssetInput)
    """사외적립자산. 비우면 명부의 ``1)일반사항`` 5번 표에서 읽어 온다."""
    read_general_info: bool = True
    """``1)일반사항`` 의 회계기간·사외적립자산·추계액 변동내역을 자동으로 쓸지.

    담당자가 이미 채워 보낸 표를 화면에 다시 옮겨 적게 할 이유가 없다. 명시적으로
    넣은 값이 있으면 그쪽이 이긴다."""
    fill_missing_ids: bool = True
    allow_errors: bool = False
    """검증 오류가 있어도 산출을 강행할지. 기본은 중단."""


@dataclass(slots=True)
class PensionRun:
    """산출 한 회차의 모든 결과물."""

    config: CalculationConfig
    roster: Roster
    assumptions: Assumptions
    issues: IssueLog
    valuation: ValuationResult
    longterm: LongTermResult | None = None
    sensitivity: SensitivityResult | None = None
    rollforward: RollForward | None = None
    plan_assets: PlanAssets | None = None
    """사외적립자산 증감과 순확정급여부채. 입력이 없으면 ``None``."""
    longterm_rollforward: LongTermRollForward | None = None
    """장기급여 증감표. 전기 장기급여채무를 주지 않으면 ``None``."""
    projection: Projection | None = None
    """차년도 예측. 산출을 마치면 늘 만든다."""
    general_info: Any = None
    """``1)일반사항`` 에서 읽은 것. 시트가 없으면 ``None``."""
    active_upload: list[list[Any]] = field(default_factory=list)
    retired_upload: list[list[Any]] = field(default_factory=list)

    @property
    def benefits_paid(self) -> float:
        """당기 퇴직급여 지급액.

        정산 성격(중간정산·전출)은 :attr:`settlements_paid` 로 따로 뺀다.
        """
        return sum(
            m.total_payment
            for m in self.roster.retired
            if m.reason
            not in (RetirementReason.DC_CONVERSION, RetirementReason.TRANSFER_OUT)
        )

    @property
    def settlements_paid(self) -> float:
        """중간정산·DC전환·계열사 전출로 빠져나간 금액."""
        total = sum(
            m.total_payment
            for m in self.roster.retired
            if m.reason in (RetirementReason.DC_CONVERSION, RetirementReason.TRANSFER_OUT)
        )
        return total + sum(m.transfer_out_payment for m in self.roster.retired)

    @property
    def other_payments(self) -> float:
        """퇴직위로금 등 퇴직급여 이외 지급액.

        총지급금액과 별도 칸이라 급여지급액에 잡히지 않는다. 증감표에서 빠지면
        그만큼이 설명 없는 경험조정으로 나타나므로 별도 줄로 보여 준다.
        """
        return sum(m.other_payment for m in self.roster.retired)

    @property
    def transfers_in(self) -> float:
        """전입으로 인수한 금액. 전출(``settlements_paid``)과 짝을 이룬다."""
        return sum(m.transfer_in_amount for m in self.roster.active)

    @property
    def fund_assets_paid(self) -> float:
        """사외적립자산에서 지급된 금액."""
        return sum(m.fund_payment for m in self.roster.retired)

    @property
    def longterm_paid(self) -> float:
        return sum(m.longterm_payment for m in self.roster.retired)

    def headcount_summary(self) -> dict[str, int]:
        """인원 현황 요약."""
        active = self.roster.active
        retired = self.roster.retired
        return {
            "재직자 총원": len(active),
            "재직자 DB": sum(1 for m in active if m.plan is BenefitPlan.DB),
            "재직자 DC": sum(1 for m in active if m.plan is BenefitPlan.DC),
            "재직자 퇴직금제도": sum(1 for m in active if m.plan is BenefitPlan.LEGACY),
            "퇴직자 총원": len(retired),
            "산출대상 재직자": self.valuation.headcount,
        }


def load_inputs(
    roster_path: str | Path,
    assumptions_path: str | Path,
    base_date: _dt.date | None = None,
    *,
    label: str = "당기 가정",
) -> tuple[CalculationConfig, Roster, Assumptions, IssueLog, Any]:
    """명부 워크북과 기초율 워크북을 읽어 검증까지 마친다.

    직군 규칙은 두 곳에 있을 수 있다. 명부의 ``Input`` 시트와, 가정 입력 화면이
    기초율 워크북에 저장하는 ``지급규정`` 시트다. **후자가 있으면 그쪽을 쓴다.**
    ``Input`` 시트는 회사가 채워 보내는 칸이라 비어 있거나 직군만 적혀 오는 일이
    잦고, ``지급규정`` 은 계리 담당자가 규정을 보고 확정한 것이기 때문이다.
    """

    from .validation import validate_roster
    from .workbook import open_workbook

    roster_path = Path(roster_path)
    if not roster_path.exists():
        raise FileNotFoundError(f"명부 파일을 찾을 수 없습니다: {roster_path}")

    payout_rules = _read_payout_rules(assumptions_path)

    wb = open_workbook(roster_path)
    try:
        config = read_config(wb)
        general = _read_general_sheet(wb)
        if base_date is None and general is not None and general.period_end:
            # 자료요청서 2번 '대상 회계기간' 기말이 곧 산출기준일이다.
            config = replace(config, base_date=general.period_end)
        if base_date is not None:
            config = replace(config, base_date=base_date)
        if payout_rules:
            # 직군 배정이 명부를 읽는 도중에 일어나므로 읽기 전에 바꿔 끼워야 한다.
            config = replace(config, job_group_rules=payout_rules, inferred=False)
        log = IssueLog()
        _check_general_sheet(general, log)
        roster = read_roster(wb, config, log)
    finally:
        wb.close()

    assumptions = load_assumptions(assumptions_path, label=label)
    validate_roster(roster, config, log)
    return config, roster, assumptions, log, general


def _read_general_sheet(wb):
    """``1)일반사항`` 을 읽는다. 없거나 깨졌으면 ``None``.

    일반사항이 없는 명부(업로드용으로 변환한 것 등)도 많으므로, 못 읽는다고
    산출을 막지는 않는다.
    """
    from .general_info import read_general_info

    try:
        info = read_general_info(wb)
    except Exception:
        return None
    return info


def _check_general_sheet(general, log: IssueLog) -> None:
    """5-2) 표가 스스로 맞는지 본다.

    서식에 '검증' 줄이 있는데도 맞지 않은 채로 오는 파일이 있다. 그 표를
    말없이 쓰면 재측정손익이 차이만큼 틀어지므로, 여기서 짚어 둔다.
    """
    if general is None or general.assets.is_empty():
        return
    difference = general.assets.difference
    if round(difference) != 0:
        log.warning(
            "GEN_ASSET_NOT_BALANCED",
            "사외적립자산 변동내역이 맞지 않습니다 "
            f"(기초+유입−유출−기말 = {difference:,.0f}원). "
            "회사가 보내온 표를 확인하세요",
            sheet="1)일반사항",
            value=round(difference),
        )


def _read_payout_rules(assumptions_path: str | Path) -> list:
    """기초율 워크북의 ``지급규정`` 시트를 읽는다. 없으면 빈 목록."""
    from .config import read_payout_rules
    from .workbook import open_workbook

    path = Path(assumptions_path)
    if not path.exists():
        return []
    wb = open_workbook(path)
    try:
        return read_payout_rules(wb)
    finally:
        wb.close()


def run_valuation(options: RunOptions, progress: Progress = _noop) -> PensionRun:
    """산출 전 과정을 실행한다.

    :raises PensionDataError: 검증 오류가 있고 ``allow_errors`` 가 거짓일 때.
    """
    progress("명부와 기초율을 읽는 중", 0.05)
    config, roster, assumptions, log, general = load_inputs(
        options.roster_path, options.assumptions_path, options.base_date
    )

    if log.has_errors() and not options.allow_errors:
        raise PensionDataError(
            f"명부 검증에서 오류 {len(log.errors)}건이 발견되어 산출을 중단했습니다. "
            "검증 리포트를 확인하고 명부를 수정하세요",
            log.errors,
        )

    progress("업로드 명부를 만드는 중", 0.20)
    active_upload, retired_upload = build_upload(
        roster, config, fill_missing_ids=options.fill_missing_ids
    )

    progress("확정급여채무를 산출하는 중", 0.35)
    valuation = value_roster(roster, config, assumptions)

    run = PensionRun(
        config=config,
        roster=roster,
        assumptions=assumptions,
        issues=log,
        valuation=valuation,
        general_info=general if options.read_general_info else None,
        active_upload=active_upload,
        retired_upload=retired_upload,
    )

    if options.include_longterm:
        progress("장기종업원급여를 산출하는 중", 0.55)
        run.longterm = value_longterm(roster, config, assumptions)

    if options.include_sensitivity:
        progress("민감도분석을 실행하는 중", 0.70)
        run.sensitivity = run_sensitivity(
            roster, config, assumptions, shocks=options.shocks, base_dbo=valuation.dbo
        )

    progress("증감분석을 만드는 중", 0.90)
    run.rollforward = _build_rollforward(run, options, progress)
    run.plan_assets = _build_plan_assets(run, options)
    run.longterm_rollforward = _build_longterm_rollforward(run, options)
    run.projection = project_next_year(
        run, contributions=options.plan_assets.expected_contributions or None
    )

    progress("산출을 마쳤습니다", 1.0)
    return run


def _build_longterm_rollforward(
    run: PensionRun, options: RunOptions
) -> LongTermRollForward | None:
    """장기급여 증감표. 전기 채무를 주지 않으면 만들지 않는다."""
    if run.longterm is None or not options.prior.longterm_dbo:
        return None
    # 지급액은 회사 장부(자료요청서 8번)가 있으면 그것을, 없으면 퇴직자명부에서.
    info = run.general_info
    paid = (info.longterm_paid if info is not None and info.longterm_paid
            else run.longterm_paid)
    return build_longterm_rollforward(
        opening_dbo=options.prior.longterm_dbo,
        service_cost=run.longterm.service_cost,
        discount_rate=options.prior.discount_rate or _fallback_rate(run),
        benefits_paid=paid,
        closing_dbo=run.longterm.dbo,
        period_years=_period_years(run.config.base_date, _period_start(run, options)),
    )


def _period_years(base_date: _dt.date, start: _dt.date | None) -> float:
    """산출 기간(년). 시작일을 주지 않았거나 순서가 뒤집혔으면 1년으로 본다."""
    if start is None or start >= base_date:
        return 1.0
    return (base_date - start).days / 365.25


def _build_rollforward(
    run: PensionRun, options: RunOptions, progress: Progress
) -> RollForward:
    prior = options.prior
    if prior.is_empty():
        return initial_period(run.valuation.dbo, run.valuation.service_cost)

    # 지급액은 회사 장부(자료요청서 5-1 표)가 있으면 그것을 쓴다. 명부에서
    # 더한 값은 파생치라, 공시에 나갈 증감표는 장부 숫자와 맞아야 한다.
    book = run.general_info.obligation if run.general_info is not None else None
    if book is not None and not book.is_empty():
        benefits_paid = book.benefits_paid
        settlements = book.settlement_paid + book.dc_converted + book.transfer_out
        other_paid = book.other_paid
        transfers_in = book.transfer_in + book.merger_in
    else:
        benefits_paid = run.benefits_paid
        settlements = run.settlements_paid
        other_paid = run.other_payments
        transfers_in = run.transfers_in

    # ── 전기 가정으로 다시 산출 ──────────────────────────────────
    # 두 벌이 필요하다. 하나는 전기 가정 그대로(A), 하나는 전기 계리가정에
    # **당기 지급률 규정만** 얹은 것(B). 그 차이가 제도개정 효과다.
    prior_assumptions = None
    dbo_prior_all: float | None = None
    dbo_after_amendment: float | None = None
    service_cost_prior_basis = 0.0
    assumption_steps: list[tuple[str, float]] = []
    if prior.assumptions_path:
        progress("전기 가정으로 다시 산출하는 중", 0.93)
        prior_assumptions = load_assumptions(prior.assumptions_path, label="전기 가정")
        prior_run = value_roster(run.roster, run.config, prior_assumptions)
        dbo_prior_all = prior_run.dbo
        service_cost_prior_basis = prior_run.service_cost

        amended = prior_assumptions.replace(
            severance_benefit=run.assumptions.severance_benefit,
            label="전기 계리가정 + 당기 지급률",
        )
        dbo_after_amendment = value_roster(run.roster, run.config, amended).dbo

        if options.split_remeasurement:
            progress("가정변경효과를 가정별로 나누는 중", 0.95)
            assumption_steps = _split_assumption_change(
                run, amended, dbo_after_amendment
            )

    # 당기근무원가는 **기초 가정** 으로 재는 것이 원칙이다(문단 57). 전기 가정을
    # 주지 않았으면 당기 것으로 갈음할 수밖에 없다.
    service_cost = (
        prior.service_cost or service_cost_prior_basis or run.valuation.service_cost
    )

    # 이자원가에 쓸 할인율. 곡선을 썼을 때 ``level_rate`` 는 1년 만기 이자율이라
    # 채무 전체의 단일할인율보다 한참 낮다 — 그대로 쓰면 이자원가가 크게 준다.
    rate = prior.discount_rate or _fallback_rate(run)

    # 이자원가는 기초채무에 대한 기간분에, 기중 발생한 근무원가·급여지급의
    # 절반년치를 더해 근사한다(기중 균등발생 가정).
    #
    # 기간은 보통 1년이지만 결산기가 바뀌면 아니다. 산출 시작일을 주면 실제
    # 기간으로 환산한다 — 1년으로 두면 9개월 결산에서 이자원가가 3할 부풀려진다.
    years = _period_years(run.config.base_date, _period_start(run, options))
    interest_cost = (
        prior.dbo * rate * years
        + (service_cost - benefits_paid - settlements) * rate * years * 0.5
    )

    # 제도개정 효과. 손으로 넣은 값이 있으면 그것을 존중한다 — 지급률 규정
    # 밖에서 일어난 개정(정년 연장 등)은 프로그램이 알 수 없기 때문이다.
    past_service_cost = prior.past_service_cost
    if not past_service_cost and dbo_after_amendment is not None:
        past_service_cost = dbo_after_amendment - (dbo_prior_all or 0.0)

    roll = build_rollforward(
        opening_dbo=prior.dbo,
        service_cost=service_cost,
        interest_cost=interest_cost,
        benefits_paid=benefits_paid,
        settlement_paid=settlements,
        settlement_obligation=prior.settlement_obligation,
        other_paid=other_paid,
        transfers_in=transfers_in,
        closing_dbo=run.valuation.dbo,
        dbo_with_prior_assumptions=dbo_prior_all,
        dbo_after_amendment=dbo_after_amendment,
        past_service_cost=past_service_cost,
    )
    roll.assumption_steps = assumption_steps
    return roll


#: 가정을 갈아 끼우는 차례. 인구통계적 가정을 먼저, 재무적 가정을 나중에 재는
#: 것이 공시 관행이다(문단 141(c) 의 세부 분해). 순서가 바뀌면 교차효과가 어느
#: 항목에 붙는지가 달라지므로 표에 차례를 함께 적는다.
_ASSUMPTION_ORDER: Final = (
    ("사망률", "mortality"),
    ("퇴직률", "withdrawal"),
    ("임금상승률", "salary"),
    ("할인율", "discount"),
)


def _split_assumption_change(
    run: PensionRun, amended: Assumptions, start_dbo: float
) -> list[tuple[str, float]]:
    """가정변경효과를 가정별로 쪼갠다.

    제도개정까지 반영한 채무(``start_dbo``)에서 출발해 전기 가정을 당기 가정으로
    하나씩 갈아 끼우며 그때마다 채무를 다시 잰다. 각 단계의 증가분이 그 가정의
    몫이고, 마지막 단계는 당기 가정 전부를 쓴 것이므로 기말채무와 같아진다 —
    그래서 몫의 합은 가정변경효과와 정확히 맞아떨어진다.
    """
    steps: list[tuple[str, float]] = []
    current = run.assumptions
    working = amended
    previous = start_dbo

    for label, attribute in _ASSUMPTION_ORDER:
        working = working.replace(
            label=f"{label}까지 당기 가정", **{attribute: getattr(current, attribute)}
        )
        moved = value_roster(run.roster, run.config, working).dbo
        steps.append((label, moved - previous))
        previous = moved
    return steps


def _build_plan_assets(run: PensionRun, options: RunOptions) -> PlanAssets | None:
    """사외적립자산 증감표.

    담당자가 화면에 넣은 값이 없으면 명부의 ``1)일반사항`` 5-2) 표를 그대로
    쓴다. 신탁 명세서를 보고 이미 채워 보낸 표라 다시 옮겨 적을 이유가 없다.
    """
    given = options.plan_assets
    info = run.general_info
    from_sheet = (
        info.assets if (info is not None and not info.assets.is_empty()) else None
    )

    if given.is_empty() and from_sheet is None:
        return None

    if given.is_empty() and from_sheet is not None:
        opening = from_sheet.opening
        closing = from_sheet.closing
        contributions = from_sheet.contributions
        # 자산에서 나간 돈은 전부 뺀다 — 수수료도 자산을 줄인다.
        paid = from_sheet.total_paid - from_sheet.total_received
        unpaid = 0.0
    else:
        opening = given.opening_fair_value
        closing = given.closing_fair_value
        contributions = given.contributions
        paid = given.benefits_paid or run.fund_assets_paid
        unpaid = given.unpaid_benefits

    return build_plan_assets(
        opening_fair_value=opening,
        closing_fair_value=closing,
        contributions=contributions,
        benefits_paid=paid,
        discount_rate=options.prior.discount_rate or _fallback_rate(run),
        closing_dbo=run.valuation.dbo,
        unpaid_benefits=unpaid,
        period_years=_period_years(run.config.base_date, _period_start(run, options)),
        asset_ceiling=given.asset_ceiling,
    )


def _period_start(run: PensionRun, options: RunOptions) -> _dt.date | None:
    """산출 시작일. 화면 입력이 없으면 자료요청서 2번 '기시' 를 쓴다."""
    if options.period_start is not None:
        return options.period_start
    info = run.general_info
    return info.period_start if info is not None else None


def _fallback_rate(run: PensionRun) -> float:
    """전기말 할인율을 안 줬을 때 쓸 이자율.

    곡선을 썼으면 채무 전체에서 역산한 단일할인율을 쓴다. ``level_rate`` 는
    1년 만기 이자율이라, 우상향 곡선에서는 실제보다 1%p 넘게 낮게 잡힌다.
    """
    if run.assumptions.discount.flat is None:
        single = run.valuation.single_discount_rate()
        if single:
            return single
    return run.assumptions.discount.level_rate
