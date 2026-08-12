"""명부 코드값 정규화.

임직원구분·성별·퇴직사유는 여러 표기로 들어온다. 여기서는
그 매핑을 한 곳에 모으고, 워크시트 수식으로만 존재하던 제도구분 변환
(``퇴직자명부`` M14/M15 셀 주석)까지 코드로 옮겼다.
"""

from __future__ import annotations

import datetime as _dt

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

# "Y"/"y"/"임원"/"임"/2 이면 임원, 그 외 전부 직원.
_EXECUTIVE_TOKENS: Final[frozenset[str]] = frozenset({"y", "임원", "임", "2"})

# "녀"/"여"/"여자"/"여성"/2/4/6/8 이면 여자, 그 외 전부 남자.
# 숫자 코드는 주민등록번호 뒤 첫 자리 규약(짝수=여자)을 따른다.
_FEMALE_TOKENS: Final[frozenset[str]] = frozenset({"녀", "여", "여자", "여성", "f", "2", "4", "6", "8"})


def normalize_employee_type(value: object) -> EmployeeType:
    """임직원구분 정규화. 판정되지 않는 값은 '직원' 으로 본다.

    실제 명부에는 ``임원(별정)``, ``정규사원``, ``촉탁사원`` 처럼 회사 나름의
    표기가 들어온다. 완전일치만 보면 ``임원(별정)`` 이 직원으로
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


def from_resident_number(value: object) -> tuple[_dt.date | None, Gender | None]:
    """주민등록번호 **앞 7자리** 에서 (생년월일, 성별).

    ``850305-1`` / ``8503051`` / ``850305 1`` 을 모두 읽는다. 뒷자리 한 자가
    세기와 성별을 함께 말해 준다 — 1·2 는 1900년대, 3·4 는 2000년대이고
    홀수가 남자다. 그 한 자가 없으면 성별을 정할 수 없으므로 ``None`` 을
    돌려준다(남자로 넘겨짚지 않는다).

    **뒷 여섯 자리는 보지 않는다.** 명부에 그것까지 적어 보내면 개인정보가
    한 단계 올라가므로, 양식도 앞 7자리만 달라고 적어 둔다.
    """
    digits = "".join(ch for ch in text(value) if ch.isdigit())
    if len(digits) < 6:
        return None, None

    marker = digits[6] if len(digits) > 6 else ""
    century = 2000 if marker in ("3", "4", "7", "8") else 1900
    try:
        born = _dt.date(century + int(digits[0:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return None, None

    # 아직 오지 않은 날이 생년월일일 수는 없다. 세기 자리를 잘못 적어 온
    # 것으로 보고 100년 당긴다 — 그대로 두면 연령이 음수가 된다.
    if born > _dt.date.today():
        born = born.replace(year=born.year - 100)

    if not marker:
        return born, None
    gender = Gender.MALE if int(marker) % 2 == 1 else Gender.FEMALE
    return born, gender


def normalize_gender(value: object) -> Gender:
    """성별 정규화. 판정되지 않는 값은 '남자' 로 본다."""
    token = text(value).lower()
    return Gender.FEMALE if token in _FEMALE_TOKENS else Gender.MALE


def normalize_benefit_plan(value: object) -> BenefitPlan | None:
    """제도구분 정규화.

    같은 제도를 회사마다 달리 적어 온다. ``미가입``·``퇴직금`` 은 퇴직금제도,
    ``DC전환`` 은 DC, ``혼합``·``혼합형`` 은 DB 로 본다 — 혼합형은 DB 몫이
    확정급여채무를 만들기 때문이다(그 비중은 `DB비율` 칸에서 따로 받는다).

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

    앞 두 글자만 잘라 판정한다. 그래서 '중도퇴직'은
    '중도', '사업처분/분할'은 '사업' 으로 매칭된다. 같은 규칙을 유지하되
    '계약만료'(→ 중도퇴직) 처럼 시트 수식에만 있던 대응도 함께 처리한다.

    ``'임금'``(임금피크제도에 따른 중간정산)은 4(정년퇴직)로 본다.
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
    """앞 두 글자 규칙과 시트 수식의 분류가 어긋나는 지급사유인지.

    '임금피크제도에 따른 중간정산'은 앞 두 글자 규칙에 따라
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
