"""업로드 명부(``UpLoad_Jae``/``UpLoad_Toi``) 생성.

VBA 의 "Upload 명부 Write" 구간을 옮긴 것이다. 열 순서는 원본 시트 헤더와
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

ACTIVE_UPLOAD_HEADERS: Final[tuple[str, ...]] = (
    "No.", "사번", "임직원구분", "직군", "성명", "성별",
    "생년월일", "입사일자", "중간정산일",
    "30일 평균임금", "명예퇴직 산정용 임금", "퇴직급여추계액(K-GAAP)",
    "日기본급(장기급여 휴가용)", "군경력등 가산 근속연수", "차감근속연수(+로 입력)",
    "퇴직급여 제도구분", "중간정산 지급금액", "장기급여 대상여부(Y,N)", "임금피크 연령",
    "전입일", "장기급여지급액", "전입액", "비고",
    "퇴직급여 정년연령", "장기급여 정년연령", "가산(감소) 지급률",
    "퇴직급여 지급률 규정", "장기급여 지급률 규정",
    "퇴직급여 중도(사망)퇴직률 산출 규정", "퇴직급여 승급률 산출 규정",
    "장기급여 중도(사망)퇴직률 산출 규정", "장기급여 승급률 산출 규정",
    "추가지급 기준일", "추가지급 기본급", "원가코드", "만연령",
)

RETIRED_UPLOAD_HEADERS: Final[tuple[str, ...]] = (
    "No.", "사번", "임직원구분", "직군", "성명", "성별",
    "생년월일", "입사일", "퇴사일(DC전환일, 전출일)", "사외적립자산 지급일",
    "지급(퇴직)사유 구분", "퇴직급여 제도구분",
    "퇴직급여 총지급금액", "퇴직급여 사외자산 지급금액", "퇴직급여 국민연금전환금 지급금액",
    "장기종업원급여 지급금액", "퇴직금이외의 지급금액(퇴직위로금 등)", "전출지급금액",
    "장기급여 산출대상여부", "비고",
    "퇴직급여 중도(사망)퇴직률 산출 규정", "장기급여 중도(사망)퇴직률 산출 규정",
    "원가코드",
)


def _generated_id(member: ActiveMember | RetiredMember) -> str:
    """사번이 비어 있을 때 쓰는 대체 키.

    VBA: ``c_sabeon(jc) = c_sex(jc) & c_jumin(jc) & c_nm(jc)`` (성별+생년월일+성명).
    """
    birth = member.birth_date.strftime("%Y%m%d") if member.birth_date else "00000000"
    return f"{member.gender.value}{birth}{member.name}"


def _date_cell(value: _dt.date | None) -> _dt.date | str:
    """빈 날짜는 VBA 와 같이 빈 문자열로 쓴다(0 또는 1900-01-00 방지)."""
    return value if value is not None else ""


def build_active_upload(
    members: list[ActiveMember],
    config: CalculationConfig,
    *,
    fill_missing_ids: bool = True,
) -> list[list[Any]]:
    """재직자 업로드 행 목록을 만든다.

    VBA 와 같이 입사일이 산출기준일보다 늦은 사람은 제외한다.

    :param fill_missing_ids: 사번이 비면 성별+생년월일+성명으로 채운다.
        ``UpLoad_Jae`` 시트 안내문("사번 공란 시 시스템 업로드 사번도 공란
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
            member.added_service_years,
            member.deducted_service_years,
            member.plan.value if member.plan else "",
            member.settlement_amount,
            member.longterm_target,
            member.wage_peak_age if member.wage_peak_age is not None else "",
            _date_cell(member.transfer_in_date),
            member.longterm_amount,
            member.transfer_in_amount,
            member.note,
            member.severance_nra,
            member.longterm_nra,
            member.extra_rate,
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

    VBA 와 같이 퇴사일이 산출기준일보다 늦은 사람은 제외한다.
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
