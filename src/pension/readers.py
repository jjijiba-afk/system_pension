"""``재직자명부``/``퇴직자명부`` 시트 읽기.

각 시트의 열 배치는 VBA 가 ``Cells(jc1, 11).Value  ' 30일 평균임금`` 처럼 주석으로
표시해 둔 것을 :data:`ACTIVE_COLUMNS` / :data:`RETIRED_COLUMNS` 에 명시적으로
옮겼다. 열이 바뀌면 이 표만 고치면 된다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Final

from .config import CalculationConfig
from .dates import parse_roster_date
from .errors import DateParseError, IssueLog
from .models import ActiveMember, RateRules, RetiredMember, Roster
from .normalize import (
    normalize_benefit_plan,
    normalize_employee_type,
    normalize_gender,
    normalize_retirement_reason,
    normalize_yes_no,
    text,
)

__all__ = [
    "ACTIVE_FIRST_ROW",
    "ACTIVE_SHEET",
    "RETIRED_FIRST_ROW",
    "RETIRED_SHEET",
    "read_active_roster",
    "read_retired_roster",
    "read_roster",
]

ACTIVE_SHEET: Final = "재직자명부"
RETIRED_SHEET: Final = "퇴직자명부"

#: 재직자명부 데이터 시작 행. VBA ``jc1 = jc + 25``.
ACTIVE_FIRST_ROW: Final = 26

#: 퇴직자명부 데이터 시작 행. VBA ``jc1 = jc + 21``.
RETIRED_FIRST_ROW: Final = 22

#: 명부 끝 판정 기준 열(생년월일). VBA ``CountA(Range("h26:h100000"))``.
_ANCHOR_COLUMN: Final = 8

#: 앵커 열이 비어 있어도 명부가 이어질 수 있으므로 이만큼은 더 살펴본다.
_BLANK_RUN_LIMIT: Final = 50


@dataclass(frozen=True, slots=True)
class _Col:
    """열 번호와 사람이 읽는 이름."""

    index: int
    label: str

    @property
    def letter(self) -> str:
        from openpyxl.utils import get_column_letter

        return get_column_letter(self.index)


ACTIVE_COLUMNS: Final[dict[str, _Col]] = {
    "employee_id": _Col(3, "사번"),
    "employee_type": _Col(4, "임직원구분"),
    "job_group": _Col(5, "직군"),
    "name": _Col(6, "성명"),
    "gender": _Col(7, "성별"),
    "birth_date": _Col(8, "생년월일"),
    "hire_date": _Col(9, "입사일자"),
    "settlement_date": _Col(10, "중간정산일"),
    "monthly_wage": _Col(11, "30일 평균임금"),
    "honorary_wage": _Col(12, "명예퇴직 산정용 임금"),
    "accrued_benefit": _Col(13, "퇴직급여추계액"),
    "daily_base_pay": _Col(14, "일 기본급"),
    "added_service_years": _Col(15, "가산 근속연수"),
    "deducted_service_years": _Col(16, "차감 근속연수"),
    "plan": _Col(17, "퇴직급여 제도구분"),
    "settlement_amount": _Col(18, "중간정산 지급금액"),
    "longterm_target": _Col(19, "장기급여 산출대상여부"),
    "wage_peak_age": _Col(20, "임금피크 연령"),
    "transfer_in_date": _Col(21, "전입일"),
    "declared_nra": _Col(22, "정년연령"),
    "longterm_amount": _Col(23, "장기종업원급여 지급금액"),
    "transfer_in_amount": _Col(24, "전입액"),
    "payout_multiple": _Col(25, "퇴직금 지급배수"),
    "note": _Col(26, "비고"),
    "extra_rate": _Col(27, "가산(감소) 지급률"),
    "severance_benefit": _Col(28, "퇴직급여 지급률 규정"),
    "longterm_benefit": _Col(29, "장기급여 지급률 규정"),
    "severance_withdrawal": _Col(30, "퇴직급여 중도(사망)퇴직률 규정"),
    "severance_salary_increase": _Col(31, "퇴직급여 승급률 규정"),
    "longterm_withdrawal": _Col(32, "장기급여 중도(사망)퇴직률 규정"),
    "longterm_salary_increase": _Col(33, "장기급여 승급률 규정"),
    "extra_pay_base_date": _Col(34, "추가지급 기준일"),
    "extra_pay_base_wage": _Col(35, "추가지급 기본급"),
    "cost_code": _Col(36, "원가코드"),
}

RETIRED_COLUMNS: Final[dict[str, _Col]] = {
    "employee_id": _Col(3, "사번"),
    "employee_type": _Col(4, "임직원구분"),
    "job_group": _Col(5, "직군"),
    "name": _Col(6, "성명"),
    "gender": _Col(7, "성별"),
    "birth_date": _Col(8, "생년월일"),
    "hire_date": _Col(9, "입사일"),
    "exit_date": _Col(10, "퇴사일"),
    "fund_payment_date": _Col(11, "사외적립자산 지급일"),
    "reason": _Col(12, "지급(퇴직)사유 구분"),
    "plan": _Col(13, "퇴직급여 제도구분"),
    "total_payment": _Col(14, "퇴직급여 총지급금액"),
    "fund_payment": _Col(15, "사외자산 지급금액"),
    "national_pension_payment": _Col(16, "국민연금전환금 지급금액"),
    "longterm_payment": _Col(17, "장기종업원급여 지급금액"),
    "other_payment": _Col(18, "퇴직위로금 등 지급금액"),
    "transfer_out_payment": _Col(19, "전출지급금액"),
    "longterm_target": _Col(20, "장기급여 산출대상여부"),
    "note": _Col(21, "비고"),
    "severance_withdrawal": _Col(22, "퇴직급여 중도(사망)퇴직률 규정"),
    "longterm_withdrawal": _Col(23, "장기급여 중도(사망)퇴직률 규정"),
    "cost_code": _Col(24, "원가코드"),
}


def _number(value: object) -> float:
    """숫자 셀. 빈 값·해석 불가 값은 0.0.

    체크리스트에 나오는 ``1,234`` / ``1234-`` 같은 표기도 받아 준다.
    """
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    token = text(value).replace(",", "").replace(" ", "")
    if token.endswith("-"):
        token = "-" + token[:-1]
    try:
        return float(token)
    except ValueError:
        return 0.0


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    number = _number(value)
    return int(number) if number else None


def _last_data_row(ws: Any, first_row: int) -> int:
    """명부의 마지막 데이터 행.

    VBA 는 ``CountA(H:H)`` 로 **건수** 를 센 뒤 ``첫 행 + 건수`` 까지만 읽는다.
    중간에 생년월일이 빈 행이 하나라도 있으면 명부 끝이 그만큼 잘려 마지막
    사람들이 조용히 누락된다. 여기서는 실제 마지막 행을 찾고, 앵커 열이 비어도
    다른 열에 값이 있으면 데이터로 취급한다.
    """
    last = first_row - 1
    blank_run = 0
    row = first_row
    max_row = max(ws.max_row, first_row)
    while row <= max_row:
        anchor = ws.cell(row, _ANCHOR_COLUMN).value
        has_any = anchor not in (None, "") or any(
            ws.cell(row, c).value not in (None, "") for c in (3, 5, 6, 9)
        )
        if has_any:
            last = row
            blank_run = 0
        else:
            blank_run += 1
            if blank_run >= _BLANK_RUN_LIMIT:
                break
        row += 1
    return last


def _read_date(
    value: object,
    config: CalculationConfig,
    log: IssueLog,
    *,
    sheet: str,
    row: int,
    seq: int,
    employee_id: str,
    col: _Col,
    code: str,
    required: bool,
) -> _dt.date | None:
    """날짜 셀 하나를 읽고, 실패하면 이슈로 남긴다."""
    try:
        parsed = parse_roster_date(value, config.base_year)
    except DateParseError as exc:
        log.error(
            code,
            f"{col.label}을(를) 해석할 수 없습니다 ({exc.reason or '형식 불명'})",
            sheet=sheet,
            row=row,
            seq=seq,
            employee_id=employee_id,
            column=col.letter,
            value=value,
        )
        return None

    if parsed is None and required:
        log.error(
            code,
            f"{col.label}이(가) 비어 있습니다",
            sheet=sheet,
            row=row,
            seq=seq,
            employee_id=employee_id,
            column=col.letter,
        )
    return parsed


def read_active_roster(workbook, config: CalculationConfig, log: IssueLog) -> list[ActiveMember]:
    """``재직자명부`` 를 읽어 :class:`ActiveMember` 목록으로 만든다.

    형식 오류가 있어도 중단하지 않고 해당 항목만 비운 채 진행한다. 값의 정합성
    검사는 :mod:`pension.validation` 이 맡는다.
    """
    ws = workbook[ACTIVE_SHEET]
    cols = ACTIVE_COLUMNS
    members: list[ActiveMember] = []
    last_row = _last_data_row(ws, ACTIVE_FIRST_ROW)

    for seq, row in enumerate(range(ACTIVE_FIRST_ROW, last_row + 1), start=1):
        def get(key: str, _row: int = row) -> Any:
            """이 행의 논리 칼럼 값. 기본인자로 행을 묶어 두어 늦은 바인딩을 피한다."""
            return ws.cell(_row, cols[key].index).value

        employee_id = text(get("employee_id"))

        member = ActiveMember(seq=seq, row=row)
        member.employee_id = employee_id
        member.employee_type = normalize_employee_type(get("employee_type"))
        member.job_group_raw = text(get("job_group"))
        member.name = text(get("name"))
        member.gender = normalize_gender(get("gender"))

        kw = dict(sheet=ACTIVE_SHEET, row=row, seq=seq, employee_id=employee_id)
        member.birth_date = _read_date(
            get("birth_date"), config, log, col=cols["birth_date"],
            code="JAE_BIRTH_DATE", required=True, **kw,
        )
        member.hire_date = _read_date(
            get("hire_date"), config, log, col=cols["hire_date"],
            code="JAE_HIRE_DATE", required=True, **kw,
        )
        member.settlement_date = _read_date(
            get("settlement_date"), config, log, col=cols["settlement_date"],
            code="JAE_SETTLEMENT_DATE", required=False, **kw,
        )
        member.transfer_in_date = _read_date(
            get("transfer_in_date"), config, log, col=cols["transfer_in_date"],
            code="JAE_TRANSFER_IN_DATE", required=False, **kw,
        )
        member.extra_pay_base_date = _read_date(
            get("extra_pay_base_date"), config, log, col=cols["extra_pay_base_date"],
            code="JAE_EXTRA_PAY_DATE", required=False, **kw,
        )

        # VBA: 중간정산일이 비었거나 입사일보다 이르면 입사일로 맞춘다.
        if member.hire_date is not None and (
            member.settlement_date is None or member.settlement_date < member.hire_date
        ):
            member.settlement_date = member.hire_date

        member.monthly_wage = _number(get("monthly_wage"))
        member.honorary_wage = _number(get("honorary_wage"))
        member.accrued_benefit = _number(get("accrued_benefit"))
        member.daily_base_pay = _number(get("daily_base_pay"))
        member.added_service_years = _number(get("added_service_years"))
        member.deducted_service_years = _number(get("deducted_service_years"))
        member.settlement_amount = _number(get("settlement_amount"))
        member.longterm_amount = _number(get("longterm_amount"))
        member.transfer_in_amount = _number(get("transfer_in_amount"))
        member.extra_rate = _number(get("extra_rate"))
        member.extra_pay_base_wage = _number(get("extra_pay_base_wage"))

        member.plan = normalize_benefit_plan(get("plan"))
        member.longterm_target = normalize_yes_no(get("longterm_target"))
        member.wage_peak_age = _optional_int(get("wage_peak_age"))
        member.note = text(get("note"))
        member.cost_code = text(get("cost_code"))

        found = config.find_job_group(member.job_group_raw)
        if found is not None:
            index, rule = found
            member.job_group_index = index
            member.job_group = rule.mapped_name
            member.rules = RateRules(
                severance_benefit=rule.severance_benefit or text(get("severance_benefit")),
                longterm_benefit=rule.longterm_benefit or text(get("longterm_benefit")),
                severance_withdrawal=rule.severance_withdrawal or text(get("severance_withdrawal")),
                severance_salary_increase=rule.severance_salary_increase
                or text(get("severance_salary_increase")),
                longterm_withdrawal=rule.longterm_withdrawal or text(get("longterm_withdrawal")),
                longterm_salary_increase=rule.longterm_salary_increase
                or text(get("longterm_salary_increase")),
            )
        else:
            member.job_group = member.job_group_raw
            member.rules = RateRules(
                severance_benefit=text(get("severance_benefit")),
                longterm_benefit=text(get("longterm_benefit")),
                severance_withdrawal=text(get("severance_withdrawal")),
                severance_salary_increase=text(get("severance_salary_increase")),
                longterm_withdrawal=text(get("longterm_withdrawal")),
                longterm_salary_increase=text(get("longterm_salary_increase")),
            )

        members.append(member)

    return members


def read_retired_roster(workbook, config: CalculationConfig, log: IssueLog) -> list[RetiredMember]:
    """``퇴직자명부`` 를 읽어 :class:`RetiredMember` 목록으로 만든다."""
    ws = workbook[RETIRED_SHEET]
    cols = RETIRED_COLUMNS
    members: list[RetiredMember] = []
    last_row = _last_data_row(ws, RETIRED_FIRST_ROW)

    for seq, row in enumerate(range(RETIRED_FIRST_ROW, last_row + 1), start=1):
        def get(key: str, _row: int = row) -> Any:
            """이 행의 논리 칼럼 값. 기본인자로 행을 묶어 두어 늦은 바인딩을 피한다."""
            return ws.cell(_row, cols[key].index).value

        employee_id = text(get("employee_id"))

        member = RetiredMember(seq=seq, row=row)
        member.employee_id = employee_id
        member.employee_type = normalize_employee_type(get("employee_type"))
        member.job_group_raw = text(get("job_group"))
        member.name = text(get("name"))
        member.gender = normalize_gender(get("gender"))

        kw = dict(sheet=RETIRED_SHEET, row=row, seq=seq, employee_id=employee_id)
        member.birth_date = _read_date(
            get("birth_date"), config, log, col=cols["birth_date"],
            code="TOI_BIRTH_DATE", required=True, **kw,
        )
        member.hire_date = _read_date(
            get("hire_date"), config, log, col=cols["hire_date"],
            code="TOI_HIRE_DATE", required=True, **kw,
        )
        member.exit_date = _read_date(
            get("exit_date"), config, log, col=cols["exit_date"],
            code="TOI_EXIT_DATE", required=False, **kw,
        )
        member.fund_payment_date = _read_date(
            get("fund_payment_date"), config, log, col=cols["fund_payment_date"],
            code="TOI_FUND_DATE", required=False, **kw,
        )

        # 체크리스트 5번: 퇴사일이 비면 산출기준일로 본다.
        if member.exit_date is None:
            member.exit_date = config.base_date

        member.reason_raw = text(get("reason"))
        member.reason = normalize_retirement_reason(get("reason"))
        member.plan = normalize_benefit_plan(get("plan"))

        member.total_payment = _number(get("total_payment"))
        member.fund_payment = _number(get("fund_payment"))
        member.national_pension_payment = _number(get("national_pension_payment"))
        member.longterm_payment = _number(get("longterm_payment"))
        member.other_payment = _number(get("other_payment"))
        member.transfer_out_payment = _number(get("transfer_out_payment"))

        member.longterm_target = normalize_yes_no(get("longterm_target"))
        member.note = text(get("note"))
        member.cost_code = text(get("cost_code"))

        found = config.find_job_group(member.job_group_raw)
        if found is not None:
            index, rule = found
            member.job_group_index = index
            member.job_group = rule.mapped_name
            member.rules = RateRules(
                severance_withdrawal=rule.retired_severance_withdrawal
                or text(get("severance_withdrawal")),
                longterm_withdrawal=rule.retired_longterm_withdrawal
                or text(get("longterm_withdrawal")),
            )
        else:
            member.job_group = member.job_group_raw
            member.rules = RateRules(
                severance_withdrawal=text(get("severance_withdrawal")),
                longterm_withdrawal=text(get("longterm_withdrawal")),
            )

        members.append(member)

    return members


def read_roster(workbook, config: CalculationConfig, log: IssueLog) -> Roster:
    """두 명부를 한 번에 읽는다."""
    return Roster(
        active=read_active_roster(workbook, config, log),
        retired=read_retired_roster(workbook, config, log),
    )
