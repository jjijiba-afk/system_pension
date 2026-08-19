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
from .events import EventOutcome, measure_events
from .readers import (
    ACTIVE_SHEET,
    PRIOR_SHEET,
    read_extra_roster,
    read_prior_roster,
    read_roster,
)
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
    """사외적립자산. 비우면 명부의 ``예치금`` 시트에서 읽어 온다."""
    read_general_info: bool = True
    """``일반사항`` 의 회계기간·사외적립자산·추계액 변동내역을 자동으로 쓸지.

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
    """``일반사항`` 에서 읽은 것. 시트가 없으면 ``None``."""
    events: EventOutcome = field(default_factory=EventOutcome)
    """[추가명부] 를 사건 시점 기준으로 잰 결과. 시트가 없으면 빈 값이다."""
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
        # 화면에서 받은 기준일을 **읽기 전에** 넘긴다. 명부 어디에도 기준일이
        # 없는 통합문서가 있는데, 예전에는 read_config 가 먼저 터져서 화면에
        # 넣어 둔 날짜가 쓰이지도 못했다.
        config = read_config(wb, base_date=base_date)
        general, general_error = _read_general_sheet(wb)
        if base_date is None and general is not None and general.period_end:
            # 자료요청서 2번 '대상 회계기간' 기말이 곧 산출기준일이다.
            config = replace(config, base_date=general.period_end)
        if base_date is not None:
            config = replace(config, base_date=base_date)
        if payout_rules:
            # 직군 배정이 명부를 읽는 도중에 일어나므로 읽기 전에 바꿔 끼워야 한다.
            config = replace(config, job_group_rules=payout_rules, inferred=False)
        log = IssueLog()
        if general_error is not None:
            log.warning(*general_error)
        _check_uncalculated(roster_path, log)
        _check_general_sheet(general, log)
        roster = read_roster(wb, config, log)
        roster.extra = read_extra_roster(wb, config, log)
        roster.prior = read_prior_roster(wb, config, log)
        _check_roster_against_movement(roster, general, log)
        _check_national_pension_against_roster(roster, general, log)
        _check_against_prior_sheet(roster, log)
    finally:
        wb.close()

    assumptions = load_assumptions(assumptions_path, label=label)
    validate_roster(roster, config, log)
    return config, roster, assumptions, log, general


def _check_uncalculated(path: Path, log: IssueLog) -> None:
    """계산되지 않은 수식 칸을 알린다.

    엑셀에서 한 번도 열어 저장하지 않은 파일은 수식 자리에 값이 없다. 우리는
    값을 읽으므로 그 칸이 **빈 칸과 똑같이** 보이고, 숫자 칸이면 0 이 된다.
    조용히 0 이 되는 것이 문제라 여기서 한 번에 짚는다.
    """
    from .workbook import uncalculated_formulas

    try:
        cells, total = uncalculated_formulas(path)
    except Exception:
        return
    if not total:
        return

    shown = ", ".join(cells)
    more = f" 외 {total - len(cells)}칸" if total > len(cells) else ""
    log.warning(
        "FILE_FORMULA_NOT_CALCULATED",
        f"수식이 있는데 계산된 값이 없는 칸이 {total}개입니다 ({shown}{more}). "
        "이 칸들은 **빈 칸으로 읽혀 0 이 됩니다.** "
        "엑셀에서 파일을 열어 한 번 저장한 뒤 다시 올리세요 — "
        "추계액이 이렇게 0 이 되면 `추계액대비` 검산이 소리 없이 꺼집니다",
        sheet=path.name,
    )


def _read_general_sheet(wb):
    """``[기초자료]``·``[예치금]`` 을 읽는다 — ``(읽은 것, 남길 경고)``.

    이 시트가 없는 명부(업로드용으로 변환한 것 등)도 많으므로, 못 읽는다고
    산출을 막지는 않는다.

    다만 **시트는 있는데 읽다 실패한 경우** 는 말해 준다. 조용히 넘기면
    자산 금액과 직군 규칙이 통째로 빠진 채 산출이 끝나고, 화면에는 아무
    표시도 없어 담당자가 알 길이 없다 — 증감표의 자산이 0 이 되는데도
    숫자는 그럴듯하게 나온다.
    """
    from .general_info import GENERAL_SHEET_ALIASES, read_general_info
    from .workbook import find_sheet

    try:
        return read_general_info(wb), None
    except Exception as error:
        if find_sheet(wb, *GENERAL_SHEET_ALIASES) is None:
            return None, None          # 시트가 없는 명부는 원래 흔하다
        return None, (
            "GEN_SHEET_UNREADABLE",
            f"[기초자료]·[예치금] 시트를 읽지 못했습니다 ({error}). "
            "그 시트의 값(사외적립자산·직군 규칙 등)은 이번 산출에 "
            "들어가지 않았습니다 — 시트 서식을 확인하거나, 그 값들을 "
            "화면에서 직접 넣으십시오",
        )


def _check_general_sheet(general, log: IssueLog) -> None:
    """5-2) 표가 스스로 맞는지 본다.

    서식에 '검증' 줄이 있는데도 맞지 않은 채로 오는 파일이 있다. 그 표를
    말없이 쓰면 재측정손익이 차이만큼 틀어지므로, 여기서 짚어 둔다.
    """
    if general is None:
        return
    if not general.assets.is_empty():
        difference = general.assets.difference
        if round(difference) != 0:
            log.warning(
                "GEN_ASSET_NOT_BALANCED",
                "예치금 증감이 맞지 않습니다 "
                f"(기초+유입−유출−기말 = {difference:,.0f}원). "
                "회사가 보내온 표를 확인하세요",
                sheet="예치금",
                value=round(difference),
            )
    assets = general.assets
    if assets.has_national_pension():
        # 이 돈은 더 들어오지 않고 줄기만 한다 — 전환금을 가진 사람이 나가면
        # 그만큼 빠진다. 기초에서 지급액을 빼면 기말이 나와야 한다.
        difference = assets.national_pension_difference
        if round(difference) != 0:
            log.warning(
                "GEN_PENSION_NOT_BALANCED",
                "국민연금전환금 잔액이 맞지 않습니다 "
                f"(기초−지급−기말 = {difference:,.0f}원). "
                "전환금은 들어오는 일 없이 지급으로만 줄어듭니다",
                sheet="예치금",
                value=round(difference),
            )
    obligation = general.obligation
    if obligation.has_ends():
        difference = obligation.difference
        if round(difference) != 0:
            log.warning(
                "GEN_OBLIGATION_NOT_BALANCED",
                "추계액 증감이 맞지 않습니다 "
                f"(기초+증가+전입−지급−기말 = {difference:,.0f}원). "
                "회사가 보내온 표를 확인하세요",
                sheet="예치금",
                value=round(difference),
            )


#: 명부 합계와 증감표 기말이 이만큼 넘게 벌어지면 사람이 빠진 것으로 본다.
#:
#: 단수 처리나 원 단위 반올림으로 몇 만 원이 남는 것은 흔하다. 사람 하나가
#: 통째로 빠지면 보통 백만 원 단위로 벌어지므로, 그 사이에 문턱을 둔다.
_ROSTER_GAP_LIMIT: Final = 1_000_000


def _check_against_prior_sheet(roster, log: IssueLog) -> None:
    """[전년명부] 가 있으면 당기 명부와 사람 단위로 맞대어 본다.

    전기 산출 결과가 있으면 화면에서 그것과 맞대지만, 첫 해에 맡은 회사는
    맞댈 상대가 없다 — 당기 명부가 스스로 맞다고 말하는 것 외에 확인할 길이
    없고, 사람이 통째로 빠져도 알 수 없다. 회사가 전년 명부를 함께 보내 주면
    그 자리를 메운다.

    여기서 나오는 것은 **경고이지 오류가 아니다.** 사람이 바뀌는 것은
    정상이고, 우리가 볼 것은 '바뀐 사실이 명부에 제대로 적혔는가' 다.
    """
    if not roster.prior:
        return
    from .models import Roster
    from .priorcheck import compare_rosters

    found = compare_rosters(roster, Roster(active=list(roster.prior)))
    for finding in found.serious:
        log.warning("JAE_PRIOR_SHEET", finding.message,
                    sheet=PRIOR_SHEET, employee_id=finding.employee_id)
    for finding in found.notes:
        log.info("JAE_PRIOR_SHEET_NOTE", finding.message,
                 sheet=PRIOR_SHEET, employee_id=finding.employee_id)


def _check_national_pension_against_roster(roster, general, log: IssueLog) -> None:
    """전환금 지급액과 퇴직자명부의 전환금 열 합계를 맞댄다.

    잔액이 스스로 맞아도 지급액 자체가 다른 자료에서 옮겨 온 것일 수 있다.
    퇴직자명부와 맞대야 같은 사건을 두 곳에서 같은 금액으로 적었는지 드러난다.
    """
    if general is None or not general.assets.national_pension_paid:
        return
    total = sum(m.national_pension_payment for m in roster.retired)
    gap = general.assets.national_pension_paid - total
    if abs(gap) <= _ROSTER_GAP_LIMIT:
        return
    log.warning(
        "GEN_PENSION_ROSTER_GAP",
        f"[예치금] 의 국민연금전환금 지급액"
        f"({general.assets.national_pension_paid:,.0f}원)과 퇴직자명부 "
        f"전환금 열 합계({total:,.0f}원)가 {gap:,.0f}원 다릅니다",
        sheet="예치금",
        value=round(gap),
    )


def _check_roster_against_movement(roster, general, log: IssueLog) -> None:
    """증감표의 **기말 추계액** 과 명부 추계액 합계를 맞댄다.

    이것이 명부 검산이다. 사람별 추계액만 맞대면 **아예 빠진 사람은 비교 대상이
    없어 걸리지 않는다** — 명부에 없으니 짝지을 상대가 없고, 합계도 그만큼
    작아진 채로 그럴듯하다. 회사가 기초에서 출발해 그 해에 드나든 것을 더하고
    뺀 기말은 그 사람을 포함하고 있으므로, 두 값을 맞대면 그때 드러난다.
    """
    if general is None or not general.obligation.closing:
        return
    total = sum(m.accrued_benefit for m in roster.active)
    if not total:
        return                       # 추계액을 안 적어 온 명부다 — 다른 검증이 짚는다
    gap = general.obligation.closing - total
    if abs(gap) <= _ROSTER_GAP_LIMIT:
        return
    log.warning(
        "GEN_ROSTER_TOTAL_GAP",
        f"[예치금] 증감표의 기말 추계액({general.obligation.closing:,.0f}원)과 "
        f"재직자명부 추계액 합계({total:,.0f}원)가 {gap:,.0f}원 다릅니다. "
        "명부에서 사람이 빠졌거나, 증감표에 다른 기간의 금액이 섞였을 수 있습니다",
        sheet="예치금",
        value=round(gap),
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


def _check_rule_names(roster, assumptions, log: IssueLog) -> None:
    """명부에 적어 온 지급률 규정명이 기초율에 있는지.

    없으면 산출은 직군으로 물러서서 계속 간다. 문제는 그 사실이 결과 어디에도
    드러나지 않는다는 것이다 — 그 규정에 걸어 둔 [퇴직사유] 별 차등이 이름이
    안 맞아 통째로 빠지는데, 배수는 직군 것으로 채워져 **오류 없이 그럴듯한
    숫자** 가 나온다. 몇 명이 어느 이름으로 걸렸는지 한 줄로 말해 준다.
    """
    from collections import Counter

    scale = assumptions.severance_benefit
    unknown: Counter = Counter()
    for member in roster.active:
        name = member.rules.severance_benefit
        if name and member.job_group and not scale.knows(name):
            unknown[(name, member.job_group)] += 1

    for (name, group), count in sorted(unknown.items(), key=lambda x: -x[1]):
        log.warning(
            "JAE_BENEFIT_RULE_UNKNOWN",
            f"명부의 지급률 규정 '{name}' 이(가) 기초율에 없어 직군 '{group}' 으로 "
            f"산출했습니다({count}명). 그 이름에 걸어 둔 퇴직사유별 차등이 있다면 "
            "함께 빠집니다 — [지급률] 열 이름을 명부와 맞추세요",
            sheet=ACTIVE_SHEET, value=name,
        )


#: 회사 추계액과 우리 값이 이만큼 넘게 벌어지면 짚는다. 근속 단수·반올림
#: 차이로 몇 원씩 어긋나는 것은 흔하므로, 비율과 금액을 함께 본다.
_ACCRUED_TOLERANCE: Final = 0.01
_ACCRUED_FLOOR: Final = 100_000.0
#: 개인별로 짚어 줄 최대 인원. 전원이 어긋나는 명부에서 이슈 목록이 통째로
#: 묻히지 않게 한다.
_ACCRUED_NAMED: Final = 15


def _check_accrued(run: "PensionRun", log: IssueLog) -> None:
    """회사가 낸 추계액과 우리 값을 맞대어 본다.

    양식에 "우리 값과 맞대어 봅니다" 라고 적어 놓고 실제로는 음수만 걸러
    내고 있었다. 추계액이 어긋난다는 것은 근속 기산일·임금·지급률 가운데
    무언가를 서로 다르게 보고 있다는 뜻이라, 채무가 맞을 리 없다.

    **차년도 추계액** 은 축이 하나 더 있다. 당기가 맞는데 차년도가 어긋나면
    근속·임금이 아니라 **임금상승 가정** 이 회사 생각과 다른 것이다.
    """
    told = {m.employee_id: m for m in run.roster.active if m.employee_id}

    for label, mine_of, theirs_of in (
        ("추계액", lambda r: r.accrued_benefit, lambda m: m.accrued_benefit),
        ("차년도 추계액",
         lambda r: r.next_accrued_benefit, lambda m: m.next_accrued_benefit),
    ):
        named = 0
        gaps = 0
        mine_total = theirs_total = 0.0
        for result in run.valuation.members:
            member = told.get(result.employee_id)
            if member is None or result.excluded_reason:
                continue
            theirs = theirs_of(member)
            if theirs <= 0:                     # 안 적어 보냈으면 검산 대상이 아니다
                continue
            mine = mine_of(result)
            mine_total += mine
            theirs_total += theirs
            gap = mine - theirs
            if abs(gap) <= _ACCRUED_FLOOR or abs(gap) <= abs(theirs) * _ACCRUED_TOLERANCE:
                continue
            gaps += 1
            if named < _ACCRUED_NAMED:
                named += 1
                log.warning(
                    "JAE_ACCRUED_MISMATCH",
                    f"사번 {result.employee_id}: {label}이 회사 값 {theirs:,.0f}원, "
                    f"우리 값 {mine:,.0f}원으로 {gap:+,.0f}원 어긋납니다",
                    sheet=ACTIVE_SHEET, row=member.row, seq=member.seq,
                    employee_id=result.employee_id, value=theirs,
                )
        if gaps > named:
            log.warning(
                "JAE_ACCRUED_MISMATCH",
                f"{label}이 어긋나는 사람이 {gaps:,}명 더 있습니다(위에 {named}명만 "
                "적었습니다)", sheet=ACTIVE_SHEET,
            )
        if theirs_total:
            gap = mine_total - theirs_total
            log.info(
                "JAE_ACCRUED_TOTAL",
                f"{label} 합계 — 회사 {theirs_total:,.0f}원, 우리 {mine_total:,.0f}원 "
                f"({gap:+,.0f}원, {gap / theirs_total:+.2%})",
                sheet=ACTIVE_SHEET,
            )


def run_valuation(options: RunOptions, progress: Progress = _noop) -> PensionRun:
    """산출 전 과정을 실행한다.

    :raises PensionDataError: 검증 오류가 있고 ``allow_errors`` 가 거짓일 때.
    """
    progress("명부와 기초율을 읽는 중", 0.05)
    config, roster, assumptions, log, general = load_inputs(
        options.roster_path, options.assumptions_path, options.base_date
    )

    _check_rule_names(roster, assumptions, log)

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

    _check_accrued(run, log)

    if roster.extra:
        progress("기중 제도변동을 재는 중", 0.50)
        run.events = measure_events(roster.extra, config, assumptions, log)

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

    # [추가명부] 를 받았으면 그쪽이 이긴다. 사건 시점에 실제로 잰 채무라,
    # 손으로 적어 넣은 한 칸보다 근거가 낫다. 분할·처분으로 넘긴 채무는
    # 정산과 같은 자리(소멸)로, 사업결합으로 인수한 것은 유입으로 들어간다.
    events = run.events
    settlement_obligation = prior.settlement_obligation
    if not events.is_empty:
        settlement_obligation = events.settled_obligation + events.transfers_out
        settlements = max(settlements, events.settled_paid)
        transfers_in += events.transfers_in

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
        settlement_obligation=settlement_obligation,
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

    담당자가 화면에 넣은 값이 없으면 명부의 ``예치금`` 시트를 그대로
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
        unpaid = from_sheet.unpaid_benefits
    else:
        opening = given.opening_fair_value
        closing = given.closing_fair_value
        contributions = given.contributions
        paid = given.benefits_paid or run.fund_assets_paid
        unpaid = given.unpaid_benefits
        if not unpaid and from_sheet is not None:
            unpaid = from_sheet.unpaid_benefits

    # 상한은 화면 값이 먼저다 — 명부보다 나중 자료다. 화면이 비었을 때만
    # 명부에 적힌 것을 쓴다. 0 과 '안 적음' 을 가려야 해서 None 으로 본다.
    ceiling = given.asset_ceiling
    if ceiling is None and from_sheet is not None:
        ceiling = from_sheet.asset_ceiling

    return build_plan_assets(
        opening_fair_value=opening,
        closing_fair_value=closing,
        contributions=contributions,
        benefits_paid=paid,
        discount_rate=options.prior.discount_rate or _fallback_rate(run),
        closing_dbo=run.valuation.dbo,
        unpaid_benefits=unpaid,
        period_years=_period_years(run.config.base_date, _period_start(run, options)),
        asset_ceiling=ceiling,
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
