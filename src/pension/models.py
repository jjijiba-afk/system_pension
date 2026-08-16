"""명부 도메인 모델.

예전 방식은 항목마다 10만 칸짜리 배열을 수십 개 선언해 두고 썼다. 여기서는 임직원 한 명을 한 객체로
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
    명부의 해당 칼럼 값을 그대로 쓴다.
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
    """명부 내 순번(1-based). 검증 메시지가 사람을 가리킬 때 쓴다."""
    row: int
    """원본 시트의 행 번호."""

    employee_id: str = ""
    employee_type: EmployeeType = EmployeeType.STAFF
    employee_type_raw: str = ""
    """명부에 적힌 임직원구분 원문.

    정규화하면 '임원'/'직원' 둘뿐이라 '정규사원'과 '촉탁사원'이 한 칸에 모인다.
    직군 규칙은 원문으로도 가릴 수 있어야 해서 원본을 남긴다.
    """
    job_group_raw: str = ""
    """명부에 적힌 직군명(``Input`` B열과 대조할 키)."""
    job_group: str = ""
    """변환 직군명(``Input`` C열)."""
    job_group_index: int | None = None
    """``Input`` 직군 규칙 테이블에서의 위치(0-based)."""

    name: str = ""
    gender: Gender = Gender.MALE
    gender_known: bool = True
    """성별을 실제로 정했는지. ``False`` 면 남자로 **넘겨짚은** 것이다.

    사망률이 성별로 갈리므로, 모르는 채 계산한 사람은 이슈로 드러나야 한다.
    """
    birth_date: _dt.date | None = None
    hire_date: _dt.date | None = None
    settlement_date: _dt.date | None = None
    """중간정산일(의 익일). 공란이면 입사일로 채운다."""
    resident_number: str = ""
    """주민등록번호 **앞 7자리**. 생년월일과 성별을 여기서 뽑을 수 있다.

    뒷 여섯 자리는 받지 않는다 — 받는 순간 개인정보 등급이 올라간다.
    """

    monthly_wage: float = 0.0
    """30일 평균임금."""
    honorary_wage: float = 0.0
    """명예퇴직 산정용 임금."""
    accrued_benefit: float = 0.0
    """퇴직급여추계액(K-GAAP)."""
    daily_base_pay: float = 0.0
    """일 기본급(장기급여 휴가용). 0 이면 평균임금/30 으로 채운다."""
    db_ratio: float = 1.0
    """혼합형 제도의 DB 비중(0~1). ``DC 1% / DB 99%`` 면 0.99.

    확정급여채무는 이 비율만큼만 진다. 나머지는 DC 라 부담금으로 끝난다.
    비어 있으면 1.0(전액 DB).
    """
    remaining_contract_years: float = 0.0
    """잔여 계약기간(년). 정년이 아니라 **계약 만료** 로 나가는 사람.

    값이 있으면 퇴직 시점을 ``현재연령 + 이 값`` 으로 본다. 직군 규칙의
    정년보다 우선한다 — 2년 뒤 계약이 끝나는 사람을 정년 60세 규칙에 걸어
    두면 그 채무를 20년 뒤 것으로 잡는다.
    """
    leave_days: float = 0.0
    """근속에서 빼는 휴직 일수. 기산일을 그만큼 뒤로 민다.

    인사에서 오는 값이 **일수** 다. 연수로 환산해 적게 하면 그 자리에서
    자릿수를 틀리고, 월할·연할 기준에서는 단수까지 어긋난다.
    """

    plan: BenefitPlan | None = None
    plan_raw: str = ""
    """명부에 적힌 제도구분 원문. 해석하지 못했을 때 무엇이 적혀 있었는지 알려야 한다."""
    settlement_amount: float = 0.0
    """중간정산 지급금액."""
    longterm_target: str = ""
    """장기급여 산출대상여부(Y/N)."""
    wage_peak_age: int | None = None
    """임금피크 연령."""
    transfer_in_date: _dt.date | None = None
    transfer_in_amount: float = 0.0

    # ── 추가명부 (축소·정산·사업결합·분할) ────────────────────────
    # 결산일 명부에는 없는 사람들이다. 기중에 제도가 축소되거나 정산됐거나,
    # 사업을 사고팔며 통째로 넘어온·넘어간 집단을 **사건 시점 기준으로** 다시
    # 평가해야 소멸·인수 채무가 나온다. 결산일 가정으로 재면 그 사이의 이자와
    # 임금상승이 섞여, 정산손익이 그만큼 틀린다.
    event_kind: str = ""
    """사건 구분. ``축소`` / ``정산`` / ``사업결합`` / ``분할``."""
    event_date: _dt.date | None = None
    """사건일. 이 날짜를 산출기준일로 삼아 이 사람을 다시 평가한다."""
    event_payment: float = 0.0
    """그 사건으로 실제 지급한 금액. 소멸 채무와의 차이가 정산손익이 된다."""
    longterm_amount: float = 0.0
    """장기종업원급여 지급금액."""
    note: str = ""
    payout_multiple: float = 1.0
    """퇴직금 지급배수. 임원 누진배수처럼 개인별로 다른 배수를 담는다.

    명부에 ``2배`` / ``현재 3배`` 처럼 글자가 섞여 들어오므로 숫자만 뽑아 쓴다.
    비어 있으면 1배. 지급률 수식에서 ``배수`` 변수로 참조한다.
    """
    # ── 기간별 지급률 분할 ───────────────────────────────────────
    # 호봉제(누진제)를 쓰다가 연봉제로 바꾼 회사는, 전환 전 근속분의 누진 배수를
    # 그대로 보전해 준다. 중간정산을 하지 않았으므로 근속은 이어지지만 배수만
    # 구간에서 갈린다. 자료요청서에도 세 열이 따로 있다.
    progressive_service: float = 0.0
    """누진적용 근속연수. 전환 시점까지의 근속으로, 이 구간에만 누진 배수를 쓴다."""
    progressive_rate: float = 0.0
    """누진적용 율(연 배수). 0 이면 구간을 나누지 않는다."""
    annual_salary_date: _dt.date | None = None
    """연봉제 전환 추계일. 근거로 남긴다 — 근속 계산에는 쓰지 않는다.

    전환일에서 근속을 다시 재지 않고 명부의 ``누진적용 근속연수`` 를 그대로
    믿는다. 회사가 휴직·군경력을 반영해 이미 계산해 보낸 값이라, 날짜만 보고
    다시 재면 그 조정이 사라진다.
    """
    group_hire_date: _dt.date | None = None
    """그룹입사일. 계열사 전입자의 합산기산 근속을 잴 때 쓴다."""

    period_start: _dt.date | None = None
    """이 행이 담당하는 지급 구간의 시작일. 비면 근속 기산일부터."""
    period_end: _dt.date | None = None
    """이 행이 담당하는 지급 구간의 종료일. 비면 퇴직 시점까지.

    같은 사번이 여러 줄에 나뉘어 오는 명부가 있다. 임원 퇴직소득 세법한도가
    ``2019.12.31 이전 3배수`` / ``2020.1.1 이후 2배수`` 로 갈리는 것이 대표적
    이다. 줄마다 배수도 임금기준도 다르므로, 한 사람의 급여는 줄별 구간을
    더해서 만든다.

    **끝난 구간(종료일이 과거)의 임금은 올리지 않는다.** 그 줄에 적힌 임금이
    이미 그 시점의 기준임금이기 때문이다.
    """

    @property
    def has_period(self) -> bool:
        return self.period_start is not None or self.period_end is not None
    longterm_start_date: _dt.date | None = None
    """장기근속포상 근속의 기산일. 비면 **입사일** 을 쓴다.

    퇴직급여와 기산일이 다르다. 퇴직금을 중간정산했다고 근속포상 시계가 0 으로
    돌아가지는 않기 때문이다 — 중간정산은 이미 지급한 퇴직금을 정산한 것이지
    근속을 끊은 것이 아니다. 실제 자료요청서들도 이 칸을 따로 받는다
    ('장기근속포상 기산일 (※ 일반적으로 입사일)').
    """
    service_add_years: float = 0.0
    """지급률 근속에만 **더하는** 가산근속연수(군경력·특례 인정 등).

    할당(귀속) 근속은 움직이지 않는다 — 할당은 언제나 기산일(중간정산일)부터
    실제로 일한 기간으로 재고, 급여 배수를 찾는 근속만 이 값으로 늘린다.
    입사일을 고쳐 넣는 방식은 할당까지 함께 움직여 틀린다.
    """
    service_deduct_years: float = 0.0
    """지급률 근속에서만 **빼는** 차감근속연수.

    '연단위 절사 지급' 같은 만근속 규정을 설계하는 자리다. 윤년 때문에
    시스템 근속이 2.99/3.01년으로 흔들리므로, 차감에 버퍼를 두어 만근속을
    맞춘다. 역시 할당 근속은 건드리지 않는다.
    """
    declared_nra: int = 0
    """명부에 개인별로 적어 온 퇴직급여 정년연령. 0 이면 직군 규정을 따른다."""
    declared_longterm_nra: int = 0
    """명부에 개인별로 적어 온 장기급여 정년연령. 0 이면 직군 규정을 따른다.

    퇴직급여 정년과 다른 회사가 많다 — 임원은 퇴직급여 정년이 55세인데
    근속포상은 60세까지 받는 식이다. 한 칸으로 뭉뚱그리면 그 차이가 사라진다.
    """
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
    min_service_years: float = 0.0
    """퇴직급여 지급 대상 최소 근속연수. 직군 규칙에서 받아 온다."""
    excluded_group: bool = False
    """직군 자체가 퇴직급여 대상이 아닌지. 직군 규칙에서 받아 온다."""
    service_basis: str = "일할"
    """근속기간 산정방법. 직군 규칙에서 받아 온다."""
    service_fraction: str = "그대로"
    """근속연수 단수 처리. 직군 규칙에서 받아 온다."""
    apply_base_up: bool = True
    apply_promotion: bool = True
    apply_withdrawal: bool = True
    apply_mortality: bool = True
    """직군별 가정 적용 여부. 직군 규칙에서 받아 온다.

    끄면 그 가정의 요율을 0 으로 둔다. 예를 들어 임원을 정년까지 근무한다고
    보아 퇴직률을 적용하지 않는 회사가 있다.
    """

    def effective_daily_base_pay(self) -> float:
        """업로드 명부에 쓸 일 기본급.

        비어 있으면 30일 평균임금을 30 으로 나눠 쓴다.
        """
        if self.daily_base_pay:
            return self.daily_base_pay
        return float(round(self.monthly_wage / 30))

    def service_years(self, base_date: _dt.date) -> float:
        """중간정산일(또는 입사일) 기준 근속연수.

        가산·차감 연수를 반영하고, 회사 규정의 산정방법(일할/월할/연할)과 단수
        처리를 적용한다.
        """
        return self._service_years(base_date, self.service_fraction)

    def raw_service_years(self, base_date: _dt.date) -> float:
        """단수 처리를 하지 **않은** 근속연수.

        미래 시점의 근속을 만들 때 쓴다. 이미 절사한 값에 연수를 더하면 단수
        처리가 한 번만 적용되어, 이후 모든 시점의 근속이 어긋난다.
        """
        from .actuarial import FRACTION_KEEP

        return self._service_years(base_date, FRACTION_KEEP)

    def _service_years(self, base_date: _dt.date, fraction: str) -> float:
        return self._years_from(
            self.settlement_date or self.hire_date, base_date, fraction)

    def longterm_service_years(self, base_date: _dt.date) -> float:
        """장기근속포상 근속연수. **중간정산을 보지 않는다.**

        기산일은 명부의 :attr:`longterm_start_date`, 없으면 입사일이다.
        중간정산일부터 세면 중간정산이 있는 회사에서 10년·20년 포상을 통째로
        놓쳐 장기급여채무가 크게 과소계상된다.
        """
        return self._years_from(
            self.longterm_start_date or self.hire_date, base_date,
            self.service_fraction)

    def _years_from(self, start: _dt.date | None, base_date: _dt.date,
                    fraction: str) -> float:
        from .actuarial import service_years as _service_years

        if start is None or base_date < start:
            return 0.0
        return _service_years(
            start, base_date,
            leave_days=self.leave_days,
            basis=self.service_basis,
            fraction=fraction,
        )


@dataclass(slots=True)
class RetiredMember:
    """퇴직자 한 명. ``퇴직자명부`` 22행 이후 한 행에 대응한다."""

    seq: int
    row: int

    employee_id: str = ""
    employee_type: EmployeeType = EmployeeType.STAFF
    employee_type_raw: str = ""
    job_group_raw: str = ""
    job_group: str = ""
    job_group_index: int | None = None

    name: str = ""
    gender: Gender = Gender.MALE
    gender_known: bool = True
    """성별을 실제로 정했는지. ``False`` 면 남자로 **넘겨짚은** 것이다.

    사망률이 성별로 갈리므로, 모르는 채 계산한 사람은 이슈로 드러나야 한다.
    """
    birth_date: _dt.date | None = None
    hire_date: _dt.date | None = None
    resident_number: str = ""
    """주민등록번호 **앞 7자리**. 생년월일과 성별을 여기서 뽑을 수 있다."""
    exit_date: _dt.date | None = None
    """퇴사일(DC전환일, 전출일)."""
    fund_payment_date: _dt.date | None = None
    """사외적립자산 지급일."""

    reason: RetirementReason | None = None
    reason_raw: str = ""
    """지급사유 원문. 정규화 규칙이 갈리는 값(예: '임금피크...')을 짚기 위해 남긴다."""
    plan: BenefitPlan | None = None
    plan_raw: str = ""

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
    """산출기준일 현재 만 연령. 생일이 지났는지는 기준일로 판정한다."""
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
    extra: list[ActiveMember] = field(default_factory=list)
    """[추가명부] — 축소·정산·사업결합·분할로 기중에 드나든 사람들.

    결산일 채무에는 들어가지 않는다. 사건 시점 기준으로 따로 재어 증감표의
    소멸·인수 채무가 된다(:mod:`pension.events`).
    """
