"""명부 코드값 정규화.

종전 규칙 는 임직원구분·성별·퇴직사유를 여러 표기로 받아 표준값으로 바꾼다. 여기서는
그 매핑을 한 곳에 모으고, 워크시트 수식으로만 존재하던 제도구분 변환
(``퇴직자명부`` M14/M15 셀 주석)까지 코드로 옮겼다.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

__all__ = [
    "BenefitPlan",
    "EmployeeType",
    "Gender",
    "RetirementReason",
    "is_ambiguous_reason",
    "normalize_benefit_plan",
    "normalize_employee_type",
    "normalize_gender",
    "normalize_retirement_reason",
    "normalize_yes_no",
    "text",
]


def text(value: object) -> str:
    """셀 값을 공백이 정리된 문자열로. ``None`` 은 빈 문자열."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


class EmployeeType(str, Enum):
    """임직원구분."""

    EXECUTIVE = "임원"
    STAFF = "직원"


class Gender(str, Enum):
    """성별."""

    MALE = "남자"
    FEMALE = "여자"


class BenefitPlan(str, Enum):
    """퇴직급여 제도구분."""

    DB = "DB"
    DC = "DC"
    LEGACY = "퇴직금제도"


class RetirementReason(str, Enum):
    """지급(퇴직)사유 구분."""

    VOLUNTARY = "1"
    """중도퇴직"""
    DEATH = "2"
    """사망퇴직"""
    DC_CONVERSION = "3"
    """DC전환, 당기 중간정산 후 퇴직"""
    NORMAL = "4"
    """정년퇴직"""
    TRANSFER_OUT = "5"
    """계열사 전출"""
    DISPOSAL = "6"
    """사업처분/분할"""

    @property
    def label(self) -> str:
        return _REASON_LABELS[self]


_REASON_LABELS: Final[dict[RetirementReason, str]] = {
    RetirementReason.VOLUNTARY: "중도퇴직",
    RetirementReason.DEATH: "사망퇴직",
    RetirementReason.DC_CONVERSION: "DC전환",
    RetirementReason.NORMAL: "정년퇴직",
    RetirementReason.TRANSFER_OUT: "계열사 전출",
    RetirementReason.DISPOSAL: "사업처분/분할",
}

# 종전 규칙: rvar = "Y"/"y"/"임원"/"임"/2 이면 임원, 그 외 전부 직원.
_EXECUTIVE_TOKENS: Final[frozenset[str]] = frozenset({"y", "임원", "임", "2"})

# 종전 규칙: rvar = "녀"/"여"/"여자"/"여성"/2/4/6/8 이면 여자, 그 외 전부 남자.
# 숫자 코드는 주민등록번호 뒤 첫 자리 규약(짝수=여자)을 따른다.
_FEMALE_TOKENS: Final[frozenset[str]] = frozenset({"녀", "여", "여자", "여성", "f", "2", "4", "6", "8"})


def normalize_employee_type(value: object) -> EmployeeType:
    """임직원구분 정규화. 판정되지 않는 값은 종전 규칙 와 같이 '직원' 으로 본다.

    실제 명부에는 ``임원(별정)``, ``정규사원``, ``촉탁사원`` 처럼 회사 나름의
    표기가 들어온다. 종전 규칙 는 완전일치만 보아 ``임원(별정)`` 을 직원으로
    분류했는데, 임원은 정년·지급배수가 달라 그대로 두면 채무가 어긋난다.
    그래서 ``임원`` 으로 **시작하는** 값도 임원으로 본다.
    """
    token = text(value).lower()
    if token in _EXECUTIVE_TOKENS:
        return EmployeeType.EXECUTIVE
    # '임원(별정)', '임원A' 처럼 뒤에 설명이 붙는 경우. '비임원' 은 걸리지 않는다.
    if token.startswith("임원") or token.startswith("이사") or token.startswith("등기임원"):
        return EmployeeType.EXECUTIVE
    return EmployeeType.STAFF


def normalize_gender(value: object) -> Gender:
    """성별 정규화. 판정되지 않는 값은 종전 규칙 와 같이 '남자' 로 본다."""
    token = text(value).lower()
    return Gender.FEMALE if token in _FEMALE_TOKENS else Gender.MALE


def normalize_benefit_plan(value: object) -> BenefitPlan | None:
    """제도구분 정규화.

    ``퇴직자명부`` 시트 M14 셀에 수식 주석으로 남아 있던 변환 규칙을 옮긴 것이다::

        IF(OR(x="", x="미가입", x="퇴직금"), "퇴직금제도",
           IF(x="DC전환", "DC",
              IF(OR(x="혼합", x="혼합형"), "DB", x)))

    :returns: 표준 제도구분. 판정할 수 없으면 ``None``(호출부에서 오류 처리).
    """
    token = text(value)
    if token == "":
        return None
    upper = token.upper()
    if upper in {"DC", "DC전환", "DC 전환"}:
        return BenefitPlan.DC
    if upper in {"DB", "확정급여", "확정급여형", "혼합", "혼합형", "임원혼합형", "1"}:
        return BenefitPlan.DB
    if token in {"퇴직금", "퇴직금제도", "미가입", "법정제", "법정퇴직금"} or upper == "2":
        return BenefitPlan.LEGACY
    return None


def normalize_retirement_reason(value: object) -> RetirementReason | None:
    """지급(퇴직)사유 정규화.

    종전 규칙 는 셀 앞 두 글자만 잘라(``Left(cell, 2)``) 판정한다. 그래서 '중도퇴직'은
    '중도', '사업처분/분할'은 '사업' 으로 매칭된다. 같은 규칙을 유지하되
    '계약만료'(→ 중도퇴직) 처럼 시트 수식에만 있던 대응도 함께 처리한다.

    ``'임금'``(임금피크제도에 따른 중간정산)은 종전 규칙 를 따라 4(정년퇴직)로 본다.
    :func:`is_ambiguous_reason` 도 함께 참고할 것.
    """
    token = text(value)
    if token == "":
        return None
    head = token[:2]
    if head in {"1", "중도", "계약", "자진", "의원"}:
        return RetirementReason.VOLUNTARY
    if head in {"2", "사망"}:
        return RetirementReason.DEATH
    if head == "3" or head.upper() == "DC":
        return RetirementReason.DC_CONVERSION
    if head in {"4", "정년", "임금"}:
        return RetirementReason.NORMAL
    if head in {"5", "계열", "전출"}:
        return RetirementReason.TRANSFER_OUT
    if head in {"6", "사업", "분할"}:
        return RetirementReason.DISPOSAL
    return None


def is_ambiguous_reason(value: object) -> bool:
    """종전 규칙 규칙과 시트 수식의 분류가 어긋나는 지급사유인지.

    '임금피크제도에 따른 중간정산'은 종전 규칙 ``Case "4", "정년", "임금"`` 에 따라
    4(정년퇴직)로 분류되지만, 같은 통합문서 ``퇴직자명부`` AC18 수식은 3(DC전환/
    당기 중간정산 후 퇴직)으로 분류한다. 어느 쪽이 맞는지는 규정에 달렸으므로
    자동으로 고르지 않고 검증 단계에서 경고로 알린다.
    """
    return text(value)[:2] == "임금"


def normalize_yes_no(value: object, *, default: str = "") -> str:
    """``Y``/``N`` 플래그(장기급여 산출대상여부) 정규화."""
    token = text(value).upper()
    if token in {"Y", "YES", "예", "1", "TRUE", "O"}:
        return "Y"
    if token in {"N", "NO", "아니오", "아니요", "0", "FALSE", "X"}:
        return "N"
    return default
