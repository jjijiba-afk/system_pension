"""정리된 명부(``재직자명부``/``퇴직자명부``) 생성.

정리된 명부를 쓰는 구간이다. 열 순서는 종전 시트 헤더와
1:1 로 맞춰 두었으므로, 기존 업로드 절차를 그대로 쓸 수 있다.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Final

from .config import CalculationConfig
from .models import ActiveMember, RetiredMember, Roster

__all__ = [
    "ACTIVE_UPLOAD_HEADERS",
    "RETIRED_UPLOAD_HEADERS",
    "build_active_upload",
    "build_retired_upload",
    "build_upload",
]

# 머리글은 **회사에 보내는 명부 양식과 같은 말** 을 쓴다. 결과 파일을 열어
# 원자료를 되짚는 사람과 명부를 채운 사람이 다른 말을 보면, 같은 칸인 줄
# 모르고 다시 물어보게 된다. 열 자리와 순서는 그대로다.
#
# 주민등록번호 앞 7자리는 여기 쓰지 않는다. 받은 자리에서 생년월일·성별로
# 풀어 두었으므로 더 쓸 데가 없고, 결과 파일에까지 옮겨 적으면 개인정보가
# 한 부 더 늘어난다.
ACTIVE_UPLOAD_HEADERS: Final[tuple[str, ...]] = (
    "순번", "사번", "임직원구분", "직군", "성명", "성별",
    "생년월일", "입사일자", "중간정산일",
    "30일 평균임금", "명예퇴직 기준임금", "추계액",
    "1일 통상임금", "휴직차감일수", "계약종료일", "잔여계약기간",
    "퇴직급여 제도구분", "DB비율", "혼합형 가입일", "중간정산 지급금액", "장기급여 대상", "임금피크 연령",
    "전입일", "장기급여 기지급액", "전입 인수액", "비고",
    "정년연령", "장기급여 정년연령",
    "지급률 규정", "장기급여 지급률 규정",
    "퇴직률 규정", "승급률 규정",
    "장기급여 퇴직률 규정", "장기급여 승급률 규정",
    "추가지급 기준일", "추가지급 기본급", "원가코드", "만 연령",
)

RETIRED_UPLOAD_HEADERS: Final[tuple[str, ...]] = (
    "순번", "사번", "임직원구분", "직군", "성명", "성별",
    "생년월일", "입사일자", "퇴사일", "사외자산 지급일",
    "퇴직사유", "퇴직급여 제도구분",
    "퇴직급여 총지급액", "사외자산 지급액", "국민연금 전환금",
    "장기급여 지급액", "퇴직위로금 등", "전출 지급액",
    "장기급여 대상", "비고",
    "퇴직률 규정", "장기급여 퇴직률 규정",
    "원가코드",
)


def _generated_id(member: ActiveMember | RetiredMember) -> str:
    """사번이 비어 있을 때 쓰는 대체 키.

    성별+생년월일+성명을 이어 붙인다.
    """
    birth = member.birth_date.strftime("%Y%m%d") if member.birth_date else "00000000"
    return f"{member.gender.value}{birth}{member.name}"


def _date_cell(value: _dt.date | None) -> _dt.date | str:
    """빈 날짜는 빈 문자열로 쓴다(0 또는 1900-01-00 방지)."""
    return value if value is not None else ""


def build_active_upload(
    members: list[ActiveMember],
    config: CalculationConfig,
    *,
    fill_missing_ids: bool = True,
) -> list[list[Any]]:
    """재직자 업로드 행 목록을 만든다.

    입사일이 산출기준일보다 늦은 사람은 제외한다.

    :param fill_missing_ids: 사번이 비면 성별+생년월일+성명으로 채운다.
        정리된 재직자명부의 안내문("사번 공란 시 시스템 업로드 사번도 공란
        처리됩니다")대로 공란을 유지하려면 ``False``.
    """
    rows: list[list[Any]] = []
    for member in members:
        if member.hire_date is not None and member.hire_date > config.base_date:
            continue

        employee_id = member.employee_id
        if not employee_id and fill_missing_ids:
            employee_id = _generated_id(member)

        rows.append([
            len(rows) + 1,
            employee_id,
            member.employee_type.value,
            member.job_group,
            member.name,
            member.gender.value,
            _date_cell(member.birth_date),
            _date_cell(member.hire_date),
            _date_cell(member.settlement_date),
            member.monthly_wage,
            member.honorary_wage,
            member.accrued_benefit,
            member.effective_daily_base_pay(),
            member.leave_days,
            _date_cell(member.contract_end_date),
            member.remaining_contract_years,
            member.plan.value if member.plan else "",
            member.db_ratio,
            _date_cell(member.mixed_plan_start_date),
            member.settlement_amount,
            member.longterm_target,
            member.wage_peak_age if member.wage_peak_age is not None else "",
            _date_cell(member.transfer_in_date),
            member.longterm_amount,
            member.transfer_in_amount,
            member.note,
            member.severance_nra,
            member.longterm_nra,
            member.rules.severance_benefit,
            member.rules.longterm_benefit,
            member.rules.severance_withdrawal,
            member.rules.severance_salary_increase,
            member.rules.longterm_withdrawal,
            member.rules.longterm_salary_increase,
            _date_cell(member.extra_pay_base_date),
            member.extra_pay_base_wage,
            member.cost_code,
            member.age,
        ])
    return rows


def build_retired_upload(
    members: list[RetiredMember],
    config: CalculationConfig,
    *,
    fill_missing_ids: bool = True,
) -> list[list[Any]]:
    """퇴직자 업로드 행 목록을 만든다.

    퇴사일이 산출기준일보다 늦은 사람은 제외한다.
    """
    rows: list[list[Any]] = []
    for member in members:
        if member.exit_date is not None and member.exit_date > config.base_date:
            continue

        employee_id = member.employee_id
        if not employee_id and fill_missing_ids:
            employee_id = _generated_id(member)

        rows.append([
            len(rows) + 1,
            employee_id,
            member.employee_type.value,
            member.job_group,
            member.name,
            member.gender.value,
            _date_cell(member.birth_date),
            _date_cell(member.hire_date),
            _date_cell(member.exit_date),
            _date_cell(member.fund_payment_date),
            member.reason.value if member.reason else "",
            member.plan.value if member.plan else "",
            member.total_payment,
            member.fund_payment,
            member.national_pension_payment,
            member.longterm_payment,
            member.other_payment,
            member.transfer_out_payment,
            member.longterm_target,
            member.note,
            member.rules.severance_withdrawal,
            member.rules.longterm_withdrawal,
            member.cost_code,
        ])
    return rows


def build_upload(
    roster: Roster,
    config: CalculationConfig,
    *,
    fill_missing_ids: bool = True,
) -> tuple[list[list[Any]], list[list[Any]]]:
    """재직·퇴직 업로드 행을 한 번에 만든다."""
    return (
        build_active_upload(roster.active, config, fill_missing_ids=fill_missing_ids),
        build_retired_upload(roster.retired, config, fill_missing_ids=fill_missing_ids),
    )
