"""명부 검증.

두 갈래의 검증을 하나로 합쳤다.

* VBA ``Module1``/``Module2`` 가 ``MsgBox`` + ``check = 1`` 로 처리하던 치명적
  오류(사번 중복, 직군 미매칭, 연령 범위, 평균임금 하한, 입사일 > 기준일 등).
* ``검증요약`` 시트가 수식으로 집계하던 항목(필수값 누락, 날짜 선후관계,
  사외자산 지급액 > 총지급액 등).

VBA 는 첫 오류에서 멈추지만 여기서는 전부 모아 한 번에 돌려준다. 담당자가
명부를 한 번만 손보면 되도록 하는 것이 목적이다.
"""

from __future__ import annotations

from collections import defaultdict

from .actuarial import attained_age
from .config import CalculationConfig
from .errors import IssueLog
from .models import ActiveMember, RetiredMember, Roster
from .normalize import BenefitPlan, RetirementReason, is_ambiguous_reason
from .readers import ACTIVE_COLUMNS, ACTIVE_SHEET, RETIRED_COLUMNS, RETIRED_SHEET

__all__ = ["validate_active", "validate_retired", "validate_roster"]


def _col(sheet: str, key: str) -> str:
    table = ACTIVE_COLUMNS if sheet == ACTIVE_SHEET else RETIRED_COLUMNS
    return table[key].letter


def _check_duplicate_ids(members, sheet: str, log: IssueLog, code: str) -> None:
    """사번 중복 검사.

    VBA 는 이중 루프로 O(n²) 비교를 한 뒤 첫 중복에서 멈춘다. 여기서는 사번별로
    묶어 한 번에 훑고, 중복된 사번을 모두 보고한다. 사번이 비어 있는 행은
    VBA 와 같이 중복 검사에서 제외한다(뒤에서 자동 생성하기 때문).
    """
    buckets: dict[str, list] = defaultdict(list)
    for member in members:
        if member.employee_id:
            buckets[member.employee_id].append(member)

    for employee_id, group in buckets.items():
        if len(group) < 2:
            continue
        rows = ", ".join(str(m.row) for m in group)
        for member in group:
            log.error(
                code,
                f"사번이 중복됩니다 (해당 행: {rows})",
                sheet=sheet,
                row=member.row,
                seq=member.seq,
                employee_id=employee_id,
                column=_col(sheet, "employee_id"),
                value=employee_id,
            )


def validate_active(members: list[ActiveMember], config: CalculationConfig, log: IssueLog) -> None:
    """재직자명부 검증 및 파생값(연령·정년연령) 채우기."""
    sheet = ACTIVE_SHEET
    _check_duplicate_ids(members, sheet, log, "JAE_DUP_ID")

    for member in members:
        kw = dict(sheet=sheet, row=member.row, seq=member.seq, employee_id=member.employee_id)

        if not member.name:
            log.warning("JAE_NAME_MISSING", "성명이 비어 있습니다",
                        column=_col(sheet, "name"), **kw)

        # ── 직군 매칭 (VBA: 미매칭이면 산출 중단) ──────────────────
        rule = None
        if member.job_group_index is None:
            if not config.job_group_rules:
                log.error("JAE_NO_JOB_GROUP_RULES",
                          "Input 시트에 직군 규칙이 한 건도 없습니다",
                          column=_col(sheet, "job_group"), **kw)
            else:
                known = ", ".join(r.source_name for r in config.job_group_rules if r.source_name)
                log.error(
                    "JAE_JOB_GROUP_UNKNOWN",
                    f"직군 '{member.job_group_raw}' 이(가) Input 시트에 없습니다 (등록된 직군: {known})",
                    column=_col(sheet, "job_group"), value=member.job_group_raw, **kw,
                )
        else:
            rule = config.job_group_rules[member.job_group_index]

        # ── 날짜 정합성 ────────────────────────────────────────────
        if member.birth_date and member.hire_date and member.birth_date >= member.hire_date:
            log.error("JAE_BIRTH_AFTER_HIRE", "생년월일이 입사일자보다 늦거나 같습니다",
                      column=_col(sheet, "birth_date"), value=member.birth_date, **kw)

        if member.hire_date and member.hire_date > config.base_date:
            log.error(
                "JAE_HIRE_AFTER_BASE",
                f"입사일자({member.hire_date})가 산출기준일({config.base_date})보다 늦습니다",
                column=_col(sheet, "hire_date"), value=member.hire_date, **kw,
            )

        if member.settlement_date and member.settlement_date > config.base_date:
            log.error(
                "JAE_SETTLEMENT_AFTER_BASE",
                f"중간정산일({member.settlement_date})이 산출기준일보다 늦습니다",
                column=_col(sheet, "settlement_date"), value=member.settlement_date, **kw,
            )

        if member.transfer_in_date and member.transfer_in_date > config.base_date:
            log.warning("JAE_TRANSFER_IN_AFTER_BASE", "전입일이 산출기준일보다 늦습니다",
                        column=_col(sheet, "transfer_in_date"),
                        value=member.transfer_in_date, **kw)

        # ── 연령 (VBA: 15세 미만 / 100세 초과는 산출 중단) ─────────
        if member.birth_date:
            member.age = attained_age(member.birth_date, config.base_date)
            if not config.min_age <= member.age <= config.max_age:
                log.error(
                    "JAE_AGE_RANGE",
                    f"산출기준일 만 연령이 {member.age}세입니다 "
                    f"({config.min_age}~{config.max_age}세 범위를 벗어남)",
                    column=_col(sheet, "birth_date"), value=member.birth_date, **kw,
                )
            if member.hire_date:
                member.hire_age = attained_age(member.birth_date, member.hire_date)
                if not config.min_age <= member.hire_age <= config.max_age:
                    log.error(
                        "JAE_HIRE_AGE_RANGE",
                        f"입사 시점 만 연령이 {member.hire_age}세입니다 "
                        f"({config.min_age}~{config.max_age}세 범위를 벗어남)",
                        column=_col(sheet, "hire_date"), value=member.hire_date, **kw,
                    )

        # ── 임금 (VBA: 체크금액 미만이거나 0 이면 산출 중단) ───────
        if member.monthly_wage <= 0:
            if member.plan is BenefitPlan.DC:
                # DC 가입자는 확정급여채무가 생기지 않으므로 평균임금이 없어도
                # 산출에 지장이 없다. 실제 명부에서 DC 가입자 임금란을 비우는 일이
                # 흔한데, 이를 오류로 막으면 명부 전체가 산출되지 않는다.
                log.warning(
                    "JAE_WAGE_MISSING_DC",
                    "30일 평균임금이 없습니다 (DC 가입자라 퇴직급여채무 산출에는 "
                    "영향이 없으나, 장기급여 대상이면 일 기본급이 필요합니다)",
                    column=_col(sheet, "monthly_wage"), **kw,
                )
            else:
                log.error("JAE_WAGE_MISSING", "30일 평균임금이 비었거나 0 이하입니다",
                          column=_col(sheet, "monthly_wage"), value=member.monthly_wage, **kw)
        elif member.monthly_wage < config.wage_check_amount:
            log.error(
                "JAE_WAGE_BELOW_CHECK",
                f"30일 평균임금 {member.monthly_wage:,.0f}원이 "
                f"체크금액 {config.wage_check_amount:,.0f}원 미만입니다",
                column=_col(sheet, "monthly_wage"), value=member.monthly_wage, **kw,
            )

        # ── 제도구분 (VBA: 공란이면 산출 중단) ─────────────────────
        if member.plan is None:
            log.error("JAE_PLAN_MISSING", "퇴직급여 제도구분이 비었거나 알 수 없는 값입니다",
                      column=_col(sheet, "plan"), **kw)

        if member.accrued_benefit < 0:
            log.warning("JAE_ACCRUED_NEGATIVE", "퇴직급여추계액이 음수입니다",
                        column=_col(sheet, "accrued_benefit"),
                        value=member.accrued_benefit, **kw)

        if member.deducted_service_years < 0:
            log.warning(
                "JAE_DEDUCT_NEGATIVE",
                "차감근속연수는 양수로 입력해야 합니다 (음수는 가산으로 처리됨)",
                column=_col(sheet, "deducted_service_years"),
                value=member.deducted_service_years, **kw,
            )

        # ── 정년연령 확정 ──────────────────────────────────────────
        if rule is not None and member.birth_date:
            from .actuarial import longterm_retirement_age, normal_retirement_age

            member.severance_nra = normal_retirement_age(
                member.age, rule, wage_peak_age=member.wage_peak_age
            )
            member.longterm_nra = longterm_retirement_age(member.age, rule)

            if member.severance_nra <= member.age and member.plan is not BenefitPlan.DC:
                log.warning(
                    "JAE_NRA_NOT_FUTURE",
                    f"확정된 퇴직급여 정년연령({member.severance_nra}세)이 "
                    f"현재 연령({member.age}세) 이하입니다. "
                    "Input 시트의 '정년연령 초과자 plus 연령' 을 확인하세요",
                    column=_col(sheet, "job_group"), **kw,
                )


def validate_retired(members: list[RetiredMember], config: CalculationConfig, log: IssueLog) -> None:
    """퇴직자명부 검증 및 파생값 채우기."""
    sheet = RETIRED_SHEET
    _check_duplicate_ids(members, sheet, log, "TOI_DUP_ID")

    for member in members:
        kw = dict(sheet=sheet, row=member.row, seq=member.seq, employee_id=member.employee_id)

        if not member.name:
            log.warning("TOI_NAME_MISSING", "성명이 비어 있습니다",
                        column=_col(sheet, "name"), **kw)

        if member.job_group_index is None:
            if not config.job_group_rules:
                log.error("TOI_NO_JOB_GROUP_RULES",
                          "Input 시트에 직군 규칙이 한 건도 없습니다",
                          column=_col(sheet, "job_group"), **kw)
            else:
                known = ", ".join(r.source_name for r in config.job_group_rules if r.source_name)
                log.error(
                    "TOI_JOB_GROUP_UNKNOWN",
                    f"직군 '{member.job_group_raw}' 이(가) Input 시트에 없습니다 (등록된 직군: {known})",
                    column=_col(sheet, "job_group"), value=member.job_group_raw, **kw,
                )

        if member.reason is None:
            log.error(
                "TOI_REASON_MISSING",
                "지급(퇴직)사유 구분을 1~6 중 하나로 입력하세요 "
                "(1:중도 2:사망 3:DC전환 4:정년 5:계열사전출 6:사업처분/분할)",
                column=_col(sheet, "reason"), **kw,
            )

        # 근로자퇴직급여보장법 제4조 단서: 계속근로기간 1년 미만은 퇴직급여
        # 지급 대상이 아니다. 그런 퇴직자는 제도구분·지급액이 비어 있는 것이
        # 정상이므로 오류로 막지 않는다. 퇴직자명부의 본래 용도가 경험퇴직률
        # 산출(인원 기준)이라 금액이 없어도 쓸 수 있다.
        short_service = 0.0 < member.service_years() < 1.0

        if member.plan is None:
            if short_service:
                log.warning(
                    "TOI_PLAN_MISSING_SHORT",
                    f"제도구분이 없습니다 (근속 {member.service_years():.2f}년으로 "
                    "1년 미만이라 퇴직급여 지급 대상이 아닙니다)",
                    column=_col(sheet, "plan"), **kw,
                )
            else:
                log.error("TOI_PLAN_MISSING", "퇴직급여 제도구분이 비었거나 알 수 없는 값입니다",
                          column=_col(sheet, "plan"), **kw)

        if member.birth_date and member.hire_date and member.birth_date >= member.hire_date:
            log.error("TOI_BIRTH_AFTER_HIRE", "생년월일이 입사일보다 늦거나 같습니다",
                      column=_col(sheet, "birth_date"), value=member.birth_date, **kw)

        if member.hire_date and member.exit_date and member.exit_date <= member.hire_date:
            log.error("TOI_EXIT_BEFORE_HIRE", "퇴사일이 입사일보다 이르거나 같습니다",
                      column=_col(sheet, "exit_date"), value=member.exit_date, **kw)

        if member.exit_date and member.exit_date > config.base_date:
            log.error(
                "TOI_EXIT_AFTER_BASE",
                f"퇴사일({member.exit_date})이 산출기준일({config.base_date})보다 늦습니다",
                column=_col(sheet, "exit_date"), value=member.exit_date, **kw,
            )

        if (
            member.fund_payment_date
            and member.exit_date
            and member.fund_payment_date < member.exit_date
        ):
            log.warning("TOI_FUND_DATE_BEFORE_EXIT", "사외적립자산 지급일이 퇴사일보다 이릅니다",
                        column=_col(sheet, "fund_payment_date"),
                        value=member.fund_payment_date, **kw)

        if member.birth_date:
            member.age = attained_age(member.birth_date, config.base_date)
            if member.hire_date:
                member.hire_age = attained_age(member.birth_date, member.hire_date)
                if member.hire_age < config.min_age:
                    log.error(
                        "TOI_HIRE_AGE_RANGE",
                        f"입사 시점 만 연령이 {member.hire_age}세로 {config.min_age}세 미만입니다",
                        column=_col(sheet, "hire_date"), value=member.hire_date, **kw,
                    )
            if member.exit_date:
                member.exit_age = attained_age(member.birth_date, member.exit_date)
            if member.age < config.min_age:
                log.error(
                    "TOI_AGE_RANGE",
                    f"산출기준일 만 연령이 {member.age}세로 {config.min_age}세 미만입니다",
                    column=_col(sheet, "birth_date"), value=member.birth_date, **kw,
                )

        # ── 금액 정합성 ────────────────────────────────────────────
        # 전출·사업처분으로 나간 사람은 퇴직급여가 아니라 전출지급금액으로
        # 정산된다. 그쪽에 금액이 있으면 총지급금액이 0인 것이 정상이다.
        paid_as_transfer = (
            member.reason in (RetirementReason.TRANSFER_OUT, RetirementReason.DISPOSAL)
            and member.transfer_out_payment > 0
        )

        if member.total_payment <= 0 and not paid_as_transfer:
            if short_service:
                log.warning(
                    "TOI_TOTAL_MISSING_SHORT",
                    f"퇴직급여 총지급금액이 없습니다 (근속 {member.service_years():.2f}년으로 "
                    "1년 미만이라 지급 대상이 아닙니다)",
                    column=_col(sheet, "total_payment"), **kw,
                )
            else:
                log.error("TOI_TOTAL_MISSING", "퇴직급여 총지급금액이 비었거나 0 이하입니다",
                          column=_col(sheet, "total_payment"), value=member.total_payment, **kw)

        if member.fund_payment > member.total_payment > 0:
            if member.reason in (RetirementReason.TRANSFER_OUT, RetirementReason.DISPOSAL):
                # 전출·사업처분에서는 적립되어 있던 사외자산이 통째로 승계되므로
                # 당기 퇴직급여 지급액보다 클 수 있다. 오류가 아니다.
                log.warning(
                    "TOI_FUND_OVER_TOTAL_TRANSFER",
                    f"사외자산 지급금액({member.fund_payment:,.0f}원)이 "
                    f"총지급금액({member.total_payment:,.0f}원)보다 큽니다 "
                    f"({member.reason.label}이라 적립자산 승계로 보입니다)",
                    column=_col(sheet, "fund_payment"), value=member.fund_payment, **kw,
                )
            else:
                log.error(
                    "TOI_FUND_OVER_TOTAL",
                    f"사외자산 지급금액({member.fund_payment:,.0f}원)이 "
                    f"총지급금액({member.total_payment:,.0f}원)보다 큽니다",
                    column=_col(sheet, "fund_payment"), value=member.fund_payment, **kw,
                )

        if member.reason is RetirementReason.TRANSFER_OUT and member.transfer_out_payment <= 0:
            log.warning("TOI_TRANSFER_OUT_ZERO", "계열사 전출인데 전출지급금액이 0 입니다",
                        column=_col(sheet, "transfer_out_payment"), **kw)

        if is_ambiguous_reason(member.reason_raw):
            log.warning(
                "TOI_REASON_AMBIGUOUS",
                f"지급사유 '{member.reason_raw}' 는 VBA 규칙상 4(정년퇴직)로 처리했습니다. "
                "퇴직자명부 AC열 수식은 3(DC전환/당기 중간정산 후 퇴직)으로 분류하므로 "
                "규정에 맞는 값을 직접 지정하세요",
                column=_col(sheet, "reason"), value=member.reason_raw, **kw,
            )


def validate_roster(roster: Roster, config: CalculationConfig, log: IssueLog) -> IssueLog:
    """명부 전체 검증. 이슈는 ``log`` 에 누적되며 같은 객체를 돌려준다."""
    validate_active(roster.active, config, log)
    validate_retired(roster.retired, config, log)
    _cross_check(roster, config, log)
    return log


def _cross_check(roster: Roster, config: CalculationConfig, log: IssueLog) -> None:
    """두 명부에 걸친 검사."""
    active_ids = {m.employee_id: m for m in roster.active if m.employee_id}

    for member in roster.retired:
        if not member.employee_id:
            continue
        counterpart = active_ids.get(member.employee_id)
        if counterpart is None:
            continue
        # 체크리스트: DC 전환자는 두 명부에 모두 올린다. 그 외에는 같은 사번이
        # 양쪽에 있으면 안 된다.
        if member.reason is RetirementReason.DC_CONVERSION or member.plan is BenefitPlan.DC:
            continue
        log.warning(
            "XREF_ID_IN_BOTH",
            f"같은 사번이 재직자명부 {counterpart.row}행에도 있습니다 "
            "(DC 전환자가 아니면 한쪽을 지워야 합니다)",
            sheet=RETIRED_SHEET, row=member.row, seq=member.seq,
            employee_id=member.employee_id, column=_col(RETIRED_SHEET, "employee_id"),
        )

