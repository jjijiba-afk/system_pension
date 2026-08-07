"""명부 → 검증 → 계리산출 전 과정을 묶는 실행 계층.

GUI 와 CLI 는 모두 이 모듈만 호출한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assumptions import Assumptions, load_assumptions
from .config import CalculationConfig, read_config
from .errors import IssueLog, PensionDataError
from .longterm import LongTermResult, value_longterm
from .models import Roster
from .normalize import BenefitPlan, RetirementReason
from .readers import read_roster
from .rollforward import RollForward, build_rollforward, initial_period
from .sensitivity import DEFAULT_SHOCKS, SensitivityResult, Shock, run_sensitivity
from .upload import build_upload
from .valuation import ValuationResult, value_roster

__all__ = [
    "PensionRun",
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
    """당기 제도개정으로 생긴 과거근무원가."""

    def is_empty(self) -> bool:
        return self.dbo == 0.0 and self.service_cost == 0.0


@dataclass(slots=True)
class RunOptions:
    """산출 옵션."""

    roster_path: Path
    assumptions_path: Path
    output_path: Path
    include_sensitivity: bool = True
    include_longterm: bool = True
    shocks: tuple[Shock, ...] = DEFAULT_SHOCKS
    prior: PriorPeriod = field(default_factory=PriorPeriod)
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
    *,
    label: str = "당기 가정",
) -> tuple[CalculationConfig, Roster, Assumptions, IssueLog]:
    """명부 워크북과 기초율 워크북을 읽어 검증까지 마친다."""

    from .validation import validate_roster
    from .workbook import open_workbook

    roster_path = Path(roster_path)
    if not roster_path.exists():
        raise FileNotFoundError(f"명부 파일을 찾을 수 없습니다: {roster_path}")

    wb = open_workbook(roster_path)
    try:
        config = read_config(wb)
        log = IssueLog()
        roster = read_roster(wb, config, log)
    finally:
        wb.close()

    assumptions = load_assumptions(assumptions_path, label=label)
    validate_roster(roster, config, log)
    return config, roster, assumptions, log


def run_valuation(options: RunOptions, progress: Progress = _noop) -> PensionRun:
    """산출 전 과정을 실행한다.

    :raises PensionDataError: 검증 오류가 있고 ``allow_errors`` 가 거짓일 때.
    """
    progress("명부와 기초율을 읽는 중", 0.05)
    config, roster, assumptions, log = load_inputs(
        options.roster_path, options.assumptions_path
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

    progress("산출을 마쳤습니다", 1.0)
    return run


def _build_rollforward(
    run: PensionRun, options: RunOptions, progress: Progress
) -> RollForward:
    prior = options.prior
    if prior.is_empty():
        return initial_period(run.valuation.dbo, run.valuation.service_cost)

    service_cost = prior.service_cost or run.valuation.service_cost
    rate = prior.discount_rate or run.assumptions.discount.level_rate
    benefits_paid = run.benefits_paid
    settlements = run.settlements_paid

    # 이자원가는 기초채무에 대한 1년치에, 기중 발생한 근무원가·급여지급의
    # 절반년치를 더해 근사한다(기중 균등발생 가정).
    interest_cost = (
        prior.dbo * rate
        + service_cost * rate * 0.5
        - (benefits_paid + settlements) * rate * 0.5
    )

    dbo_prior_assumptions: float | None = None
    if prior.assumptions_path:
        progress("전기 가정으로 다시 산출하는 중", 0.93)
        prior_assumptions = load_assumptions(prior.assumptions_path, label="전기 가정")
        dbo_prior_assumptions = value_roster(
            run.roster, run.config, prior_assumptions
        ).dbo

    return build_rollforward(
        opening_dbo=prior.dbo,
        service_cost=service_cost,
        interest_cost=interest_cost,
        benefits_paid=benefits_paid,
        settlement_paid=settlements,
        closing_dbo=run.valuation.dbo,
        dbo_with_prior_assumptions=dbo_prior_assumptions,
        past_service_cost=prior.past_service_cost,
    )
