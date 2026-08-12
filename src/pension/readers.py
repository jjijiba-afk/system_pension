"""``재직자명부``/``퇴직자명부`` 시트 읽기.

머리글이 있는 명부는 **머리글로 열을 찾는다**(:mod:`pension.layout`). 머리글이
없는 옛 서식을 위해 고정 배치를 :data:`ACTIVE_COLUMNS` / :data:`RETIRED_COLUMNS`
한 곳에 모아 두었다. 읽는 자리마다 열 번호를 흩어 놓으면 서식이 바뀔 때 고칠
곳을 빠뜨린다.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Final

from .config import CalculationConfig
from .dates import parse_roster_date
from .errors import DateParseError, IssueLog
from .layout import (
    ACTIVE_HEADER_ALIASES,
    REQUIRED_ACTIVE,
    REQUIRED_RETIRED,
    RETIRED_HEADER_ALIASES,
    resolve_layout,
)
from .models import ActiveMember, RateRules, RetiredMember, Roster
from .jobgroup import decide_employee_type
from .normalize import (
    from_resident_number,
    normalize_benefit_plan,
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

#: 같은 명부인데 통합문서마다 시트 이름이 다르다.
ACTIVE_SHEET_ALIASES: Final = (ACTIVE_SHEET, "2)재직자명부", "재직자")
RETIRED_SHEET_ALIASES: Final = (RETIRED_SHEET, "퇴직자")

#: 머리글 없는 옛 서식의 재직자명부 데이터 시작 행.
ACTIVE_FIRST_ROW: Final = 26

#: 머리글 없는 옛 서식의 퇴직자명부 데이터 시작 행.
RETIRED_FIRST_ROW: Final = 22

#: 명부 끝 판정 기준 열(생년월일). 이 열이 비면 명부가 끝난 것으로 본다.
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
        if self.index < 1:
            return ""
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
    "leave_days": _Col(15, "휴직차감일수"),
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


def _multiple(value: object) -> float:
    """퇴직금 지급배수 셀.

    실제 명부에는 ``2배``, ``현재 3배``, ``3.0`` 처럼 글자와 숫자가 섞여 들어온다.
    숫자만 뽑아 쓰고, 숫자가 없으면 1배(법정)로 본다.
    """
    if value is None or value == "":
        return 1.0
    if isinstance(value, bool):
        return 1.0
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else 1.0

    found = re.search(r"\d+(?:\.\d+)?", text(value))
    if found is None:
        return 1.0
    number = float(found.group())
    return number if number > 0 else 1.0


_ABSENT = _Col(0, "(없는 열)")


def _col_of(cols: dict[str, _Col], key: str) -> _Col:
    """인식된 열 정보. 그 서식에 없는 열이면 자리표시자를 돌려준다."""
    return cols.get(key, _ABSENT)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    number = _number(value)
    return int(number) if number else None


def _last_data_row(ws: Any, first_row: int, cols: dict[str, _Col] | None = None) -> int:
    """명부의 마지막 데이터 행.

    값이 **몇 건인지** 세어 ``첫 행 + 건수`` 까지 읽으면 안 된다. 중간에
    생년월일이 빈 행이 하나라도 있으면 명부 끝이 그만큼 잘려 마지막 사람들이
    조용히 누락된다. 여기서는 실제 마지막 행을 찾고, 앵커 열이 비어도 다른
    열에 값이 있으면 데이터로 취급한다.
    """
    if cols:
        anchor_col = cols["birth_date"].index if "birth_date" in cols else _ANCHOR_COLUMN
        others = [
            cols[k].index for k in ("employee_id", "job_group", "name", "hire_date")
            if k in cols
        ]
    else:
        anchor_col = _ANCHOR_COLUMN
        others = [3, 5, 6, 9]

    last = first_row - 1
    blank_run = 0
    row = first_row
    max_row = max(ws.max_row, first_row)
    while row <= max_row:
        anchor = ws.cell(row, anchor_col).value
        has_any = anchor not in (None, "") or any(
            ws.cell(row, c).value not in (None, "") for c in others
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



def _resolve(workbook, aliases_key: str, log: IssueLog):
    """시트를 찾고 열 배치를 확정한다."""
    from .workbook import find_sheet

    if aliases_key == "active":
        sheet = find_sheet(workbook, *ACTIVE_SHEET_ALIASES)
        aliases, base, required = ACTIVE_HEADER_ALIASES, ACTIVE_COLUMNS, REQUIRED_ACTIVE
        fallback_start = ACTIVE_FIRST_ROW
        label = ACTIVE_SHEET
    else:
        sheet = find_sheet(workbook, *RETIRED_SHEET_ALIASES)
        aliases, base, required = RETIRED_HEADER_ALIASES, RETIRED_COLUMNS, REQUIRED_RETIRED
        fallback_start = RETIRED_FIRST_ROW
        label = RETIRED_SHEET

    if sheet is None:
        raise KeyError(f"'{label}' 시트를 찾을 수 없습니다")

    defaults = {name: col.index for name, col in base.items()}
    defaults["_data_start"] = fallback_start
    layout = resolve_layout(sheet, aliases, defaults, required, log)

    # 열 번호는 인식 결과를, 이름표는 기존 표를 쓴다.
    cols = {
        name: _Col(index, base[name].label if name in base else aliases[name][0])
        for name, index in layout.columns.items()
    }
    return sheet, cols, layout


def read_active_roster(workbook, config: CalculationConfig, log: IssueLog) -> list[ActiveMember]:
    """``재직자명부`` 를 읽어 :class:`ActiveMember` 목록으로 만든다.

    형식 오류가 있어도 중단하지 않고 해당 항목만 비운 채 진행한다. 값의 정합성
    검사는 :mod:`pension.validation` 이 맡는다.
    """
    ws, cols, layout = _resolve(workbook, "active", log)
    members: list[ActiveMember] = []
    first_row = layout.data_start_row
    last_row = _last_data_row(ws, first_row, cols)

    for seq, row in enumerate(range(first_row, last_row + 1), start=1):
        def get(key: str, _row: int = row) -> Any:
            """이 행의 논리 칼럼 값. 기본인자로 행을 묶어 두어 늦은 바인딩을 피한다.

            그 서식에 없는 열이면 ``None``. 구 서식에는 규정 열이 아예 없어
            Input 시트의 직군 규칙에서 값을 받는다.
            """
            column = cols.get(key)
            return ws.cell(_row, column.index).value if column is not None else None

        employee_id = text(get("employee_id"))

        member = ActiveMember(seq=seq, row=row)
        member.employee_id = employee_id
        member.employee_type_raw = text(get("employee_type"))
        member.job_group_raw = text(get("job_group"))
        # 임직원구분 열을 따로 받지 않는다 — 직군과 겹친다. 적혀 왔으면 그쪽을
        # 쓰고, 없으면 **사람이 정한 직군 매핑** 이 정해진 뒤 다시 잡는다
        # (validation 의 직군 확정 단계). 여기서 넘겨짚지 않는다.
        member.employee_type = decide_employee_type(member.employee_type_raw)
        member.name = text(get("name"))
        member.resident_number = text(get("resident_number"))
        member.gender = normalize_gender(get("gender"))

        kw = dict(sheet=ACTIVE_SHEET, row=row, seq=seq, employee_id=employee_id)
        member.birth_date = _read_date(
            get("birth_date"), config, log, col=_col_of(cols, "birth_date"),
            code="JAE_BIRTH_DATE", required=not member.resident_number, **kw,
        )
        # 주민등록번호 앞 7자리를 받은 명부는 생년월일·성별 칸을 따로 두지
        # 않는다. 빈 칸만 채운다 — 적혀 있는 값을 덮으면 어느 쪽이 맞는지
        # 가릴 기회가 사라진다.
        if member.resident_number:
            born, sex = from_resident_number(member.resident_number)
            if member.birth_date is None:
                member.birth_date = born
            if sex is not None and not text(get("gender")):
                member.gender = sex
        member.hire_date = _read_date(
            get("hire_date"), config, log, col=_col_of(cols, "hire_date"),
            code="JAE_HIRE_DATE", required=True, **kw,
        )
        member.settlement_date = _read_date(
            get("settlement_date"), config, log, col=_col_of(cols, "settlement_date"),
            code="JAE_SETTLEMENT_DATE", required=False, **kw,
        )
        member.transfer_in_date = _read_date(
            get("transfer_in_date"), config, log, col=_col_of(cols, "transfer_in_date"),
            code="JAE_TRANSFER_IN_DATE", required=False, **kw,
        )
        member.extra_pay_base_date = _read_date(
            get("extra_pay_base_date"), config, log, col=_col_of(cols, "extra_pay_base_date"),
            code="JAE_EXTRA_PAY_DATE", required=False, **kw,
        )
        member.annual_salary_date = _read_date(
            get("annual_salary_date"), config, log,
            col=_col_of(cols, "annual_salary_date"),
            code="JAE_ANNUAL_SALARY_DATE", required=False, **kw,
        )
        member.group_hire_date = _read_date(
            get("group_hire_date"), config, log, col=_col_of(cols, "group_hire_date"),
            code="JAE_GROUP_HIRE_DATE", required=False, **kw,
        )
        member.period_start = _read_date(
            get("period_start"), config, log, col=_col_of(cols, "period_start"),
            code="JAE_PERIOD_START", required=False, **kw,
        )
        member.period_end = _read_date(
            get("period_end"), config, log, col=_col_of(cols, "period_end"),
            code="JAE_PERIOD_END", required=False, **kw,
        )

        # 중간정산일이 비었거나 입사일보다 이르면 입사일로 맞춘다. 근속
        # 기산일을 여기 하나로 모아 두면 뒤에서 두 날짜를 매번 견주지 않아도 된다.
        if member.hire_date is not None and (
            member.settlement_date is None or member.settlement_date < member.hire_date
        ):
            member.settlement_date = member.hire_date

        member.monthly_wage = _number(get("monthly_wage"))
        member.honorary_wage = _number(get("honorary_wage"))
        member.accrued_benefit = _number(get("accrued_benefit"))
        member.daily_base_pay = _number(get("daily_base_pay"))
        member.leave_days = abs(_number(get("leave_days")))
        member.remaining_contract_years = abs(_number(get("remaining_contract_years")))
        # DB비율은 `0.99` 로도 `99` 로도 온다. 1 을 넘으면 백분율로 본다 —
        # DB 비중이 1 배를 넘는 제도는 없다. 비어 있으면 전액 DB 다.
        ratio = _number(get("db_ratio"))
        member.db_ratio = (ratio / 100 if ratio > 1 else ratio) if ratio > 0 else 1.0
        member.settlement_amount = _number(get("settlement_amount"))
        member.longterm_amount = _number(get("longterm_amount"))
        member.declared_nra = int(_number(get("declared_nra")))
        member.transfer_in_amount = _number(get("transfer_in_amount"))
        member.extra_pay_base_wage = _number(get("extra_pay_base_wage"))
        member.progressive_service = _number(get("progressive_service"))
        member.progressive_rate = _number(get("progressive_rate"))

        member.plan_raw = text(get("plan"))
        member.plan = normalize_benefit_plan(member.plan_raw)
        member.longterm_target = normalize_yes_no(get("longterm_target"))
        member.wage_peak_age = _optional_int(get("wage_peak_age"))
        member.payout_multiple = _multiple(get("payout_multiple"))
        member.note = text(get("note"))
        member.cost_code = text(get("cost_code"))

        found = config.find_job_group(
            member.job_group_raw, member.employee_type.value, member.employee_type_raw
        )
        if found is not None:
            index, rule = found
            member.job_group_index = index
            member.job_group = rule.mapped_name
            member.min_service_years = rule.min_service_years
            member.excluded_group = rule.excluded
            member.service_basis = rule.service_basis
            member.service_fraction = rule.service_fraction
            member.apply_base_up = rule.apply_base_up
            member.apply_promotion = rule.apply_promotion
            member.apply_withdrawal = rule.apply_withdrawal
            member.apply_mortality = rule.apply_mortality
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
    ws, cols, layout = _resolve(workbook, "retired", log)
    members: list[RetiredMember] = []
    first_row = layout.data_start_row
    last_row = _last_data_row(ws, first_row, cols)

    for seq, row in enumerate(range(first_row, last_row + 1), start=1):
        def get(key: str, _row: int = row) -> Any:
            """이 행의 논리 칼럼 값. 기본인자로 행을 묶어 두어 늦은 바인딩을 피한다.

            그 서식에 없는 열이면 ``None``. 구 서식에는 규정 열이 아예 없어
            Input 시트의 직군 규칙에서 값을 받는다.
            """
            column = cols.get(key)
            return ws.cell(_row, column.index).value if column is not None else None

        employee_id = text(get("employee_id"))

        member = RetiredMember(seq=seq, row=row)
        member.employee_id = employee_id
        member.employee_type_raw = text(get("employee_type"))
        member.job_group_raw = text(get("job_group"))
        # 임직원구분 열을 따로 받지 않는다 — 직군과 겹친다. 적혀 왔으면 그쪽을
        # 쓰고, 없으면 **사람이 정한 직군 매핑** 이 정해진 뒤 다시 잡는다
        # (validation 의 직군 확정 단계). 여기서 넘겨짚지 않는다.
        member.employee_type = decide_employee_type(member.employee_type_raw)
        member.name = text(get("name"))
        member.resident_number = text(get("resident_number"))
        member.gender = normalize_gender(get("gender"))

        kw = dict(sheet=RETIRED_SHEET, row=row, seq=seq, employee_id=employee_id)
        member.birth_date = _read_date(
            get("birth_date"), config, log, col=_col_of(cols, "birth_date"),
            code="TOI_BIRTH_DATE", required=not member.resident_number, **kw,
        )
        # 주민등록번호 앞 7자리를 받은 명부는 생년월일·성별 칸을 따로 두지
        # 않는다. 빈 칸만 채운다 — 적혀 있는 값을 덮으면 어느 쪽이 맞는지
        # 가릴 기회가 사라진다.
        if member.resident_number:
            born, sex = from_resident_number(member.resident_number)
            if member.birth_date is None:
                member.birth_date = born
            if sex is not None and not text(get("gender")):
                member.gender = sex
        member.hire_date = _read_date(
            get("hire_date"), config, log, col=_col_of(cols, "hire_date"),
            code="TOI_HIRE_DATE", required=True, **kw,
        )
        member.exit_date = _read_date(
            get("exit_date"), config, log, col=_col_of(cols, "exit_date"),
            code="TOI_EXIT_DATE", required=False, **kw,
        )
        member.fund_payment_date = _read_date(
            get("fund_payment_date"), config, log, col=_col_of(cols, "fund_payment_date"),
            code="TOI_FUND_DATE", required=False, **kw,
        )

        # 체크리스트 5번: 퇴사일이 비면 산출기준일로 본다.
        if member.exit_date is None:
            member.exit_date = config.base_date

        member.reason_raw = text(get("reason"))
        member.reason = normalize_retirement_reason(get("reason"))
        member.plan_raw = text(get("plan"))
        member.plan = normalize_benefit_plan(member.plan_raw)

        member.total_payment = _number(get("total_payment"))
        member.fund_payment = _number(get("fund_payment"))
        member.national_pension_payment = _number(get("national_pension_payment"))
        member.longterm_payment = _number(get("longterm_payment"))
        member.other_payment = _number(get("other_payment"))
        member.transfer_out_payment = _number(get("transfer_out_payment"))

        member.longterm_target = normalize_yes_no(get("longterm_target"))
        member.note = text(get("note"))
        member.cost_code = text(get("cost_code"))

        found = config.find_job_group(
            member.job_group_raw, member.employee_type.value, member.employee_type_raw
        )
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


def describe_layout(workbook, log: IssueLog) -> list[str]:
    """두 명부의 열 인식 결과를 사람이 읽을 수 있는 줄로."""
    lines: list[str] = []
    for key in ("active", "retired"):
        try:
            _sheet, _cols, layout = _resolve(workbook, key, log)
        except KeyError as exc:
            lines.append(str(exc))
            continue
        lines.extend(layout.report.lines())
    return lines
