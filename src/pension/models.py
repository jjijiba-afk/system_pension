"""명부 도메인 모델.

VBA 는 항목마다 ``c_sabeon(1 To 100000)`` 처럼 10만 칸짜리 배열을 40개 넘게
선언해 쓴다(모듈당 약 30MB 의 정적 배열). 여기서는 임직원 한 명을 한 객체로
묶어 명부 크기에 비례하는 메모리만 쓴다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .normalize import BenefitPlan, EmployeeType, Gender, RetirementReason

__all__ = ["ActiveMember", "RateRules", "RetiredMember", "Roster"]


@dataclass(slots=True)
class RateRules:
    """개인별로 확정된 기초율 규정명 묶음.

    ``Input`` 시트 G~N 열에 값이 있으면 직군 단위로 일괄 적용하고, 비어 있으면
    명부의 해당 칼럼 값을 그대로 쓴다(VBA ``If toi_beta(jkn_j) = "" Then ...``).
    """

    severance_benefit: str = ""
    """퇴직급여 지급률 규정"""
    longterm_benefit: str = ""
    """장기급여 지급률 규정"""
    severance_withdrawal: str = ""
    """퇴직급여 중도(사망)퇴직률 산출 규정"""
    severance_salary_increase: str = ""
    """퇴직급여 승급률 산출 규정"""
    longterm_withdrawal: str = ""
    """장기급여 중도(사망)퇴직률 산출 규정"""
    longterm_salary_increase: str = ""
    """장기급여 승급률 산출 규정"""


@dataclass(slots=True)
class ActiveMember:
    """재직자 한 명. ``재직자명부`` 26행 이후 한 행에 대응한다."""

    seq: int
    """명부 내 순번(1-based). VBA 오류 메시지의 "N 번째 임직원" 과 같다."""
    row: int
    """원본 시트의 행 번호."""

    employee_id: str = ""
    employee_type: EmployeeType = EmployeeType.STAFF
    job_group_raw: str = ""
    """명부에 적힌 직군명(``Input`` B열과 대조할 키)."""
    job_group: str = ""
    """변환 직군명(``Input`` C열)."""
    job_group_index: int | None = None
    """``Input`` 직군 규칙 테이블에서의 위치(0-based)."""

    name: str = ""
    gender: Gender = Gender.MALE
    birth_date: _dt.date | None = None
    hire_date: _dt.date | None = None
    settlement_date: _dt.date | None = None
    """중간정산일(의 익일). 공란이면 입사일로 채운다."""

    monthly_wage: float = 0.0
    """30일 평균임금."""
    honorary_wage: float = 0.0
    """명예퇴직 산정용 임금."""
    accrued_benefit: float = 0.0
    """퇴직급여추계액(K-GAAP)."""
    daily_base_pay: float = 0.0
    """일 기본급(장기급여 휴가용). 0 이면 평균임금/30 으로 채운다."""
    added_service_years: float = 0.0
    """군경력 등 가산 근속연수."""
    deducted_service_years: float = 0.0
    """차감 근속연수(양수로 입력)."""

    plan: BenefitPlan | None = None
    settlement_amount: float = 0.0
    """중간정산 지급금액."""
    longterm_target: str = ""
    """장기급여 산출대상여부(Y/N)."""
    wage_peak_age: int | None = None
    """임금피크 연령."""
    transfer_in_date: _dt.date | None = None
    transfer_in_amount: float = 0.0
    longterm_amount: float = 0.0
    """장기종업원급여 지급금액."""
    note: str = ""
    payout_multiple: float = 1.0
    """퇴직금 지급배수. 임원 누진배수처럼 개인별로 다른 배수를 담는다.

    명부에 ``2배`` / ``현재 3배`` 처럼 글자가 섞여 들어오므로 숫자만 뽑아 쓴다.
    비어 있으면 1배. 지급률 수식에서 ``배수`` 변수로 참조한다.
    """
    extra_rate: float = 0.0
    """가산(감소) 지급률."""
    extra_pay_base_date: _dt.date | None = None
    """전별금 등 추가지급 기준일."""
    extra_pay_base_wage: float = 0.0
    cost_code: str = ""

    rules: RateRules = field(default_factory=RateRules)

    # ── 산출 결과 ────────────────────────────────────────────────
    age: int = 0
    """산출기준일 현재 만 연령."""
    hire_age: int = 0
    """입사 시점 만 연령."""
    severance_nra: int = 0
    """퇴직급여 정년연령."""
    longterm_nra: int = 0
    """장기급여 정년연령."""

    def effective_daily_base_pay(self) -> float:
        """업로드 명부에 쓸 일 기본급.

        VBA: ``If c_kibonkp(jc) = 0 Then Round(c_imkm(jc) / 30, 0)``.
        """
        if self.daily_base_pay:
            return self.daily_base_pay
        return float(round(self.monthly_wage / 30))

    def service_years(self, base_date: _dt.date) -> float:
        """중간정산일(또는 입사일) 기준 근속연수. 가산·차감 연수를 반영한다."""
        start = self.settlement_date or self.hire_date
        if start is None or base_date < start:
            return 0.0
        raw = (base_date - start).days / 365.25
        return max(0.0, raw + self.added_service_years - self.deducted_service_years)


@dataclass(slots=True)
class RetiredMember:
    """퇴직자 한 명. ``퇴직자명부`` 22행 이후 한 행에 대응한다."""

    seq: int
    row: int

    employee_id: str = ""
    employee_type: EmployeeType = EmployeeType.STAFF
    job_group_raw: str = ""
    job_group: str = ""
    job_group_index: int | None = None

    name: str = ""
    gender: Gender = Gender.MALE
    birth_date: _dt.date | None = None
    hire_date: _dt.date | None = None
    exit_date: _dt.date | None = None
    """퇴사일(DC전환일, 전출일)."""
    fund_payment_date: _dt.date | None = None
    """사외적립자산 지급일."""

    reason: RetirementReason | None = None
    reason_raw: str = ""
    """지급사유 원문. 정규화 규칙이 갈리는 값(예: '임금피크...')을 짚기 위해 남긴다."""
    plan: BenefitPlan | None = None

    total_payment: float = 0.0
    """퇴직급여 총지급금액."""
    fund_payment: float = 0.0
    """퇴직급여 사외자산 지급금액."""
    national_pension_payment: float = 0.0
    """국민연금 전환금 지급금액."""
    longterm_payment: float = 0.0
    """장기종업원급여 지급금액."""
    other_payment: float = 0.0
    """퇴직위로금 등 퇴직금 이외 지급금액."""
    transfer_out_payment: float = 0.0
    """전출 지급금액."""

    longterm_target: str = ""
    note: str = ""
    cost_code: str = ""
    rules: RateRules = field(default_factory=RateRules)

    # ── 산출 결과 ────────────────────────────────────────────────
    age: int = 0
    """산출기준일 현재 만 연령(VBA 와 동일하게 기준일 기준으로 센다)."""
    hire_age: int = 0
    exit_age: int = 0
    """퇴사 시점 만 연령."""

    def service_years(self) -> float:
        """입사일부터 퇴사일까지의 근속연수."""
        if self.hire_date is None or self.exit_date is None:
            return 0.0
        return max(0.0, (self.exit_date - self.hire_date).days / 365.25)


@dataclass(slots=True)
class Roster:
    """정규화가 끝난 명부 한 벌."""

    active: list[ActiveMember] = field(default_factory=list)
    retired: list[RetiredMember] = field(default_factory=list)
