"""``1)일반사항`` 시트에서 지급규정 초안을 읽어낸다.

자료요청서 6번 항목(회사의 퇴직금 지급규정)은 담당자가 자유서술로 채운다.
실제로 받은 여섯 건이 이런 식이었다.

    가입자격        전 임직원 / 1년 이상 근속한 전직원 / 근속 3년 이상이 대상
    기간산정        근로기준법에 따른 산정법 - 일수 / 근속기간 1년 이상 (월할 계산)
                    1년이 되지 않는 단수개월은 절사
    정년(직원)      만 60세 / 정규직 60세, 계약직 60세
    정년(임원)      없음 / 정년 없음 / 제약 無 / 39세
    계산구조        ROUND(평균임금*근속년월*지급률,-1) / 기초금액 × 근속연수별 지급률

같은 뜻을 저마다 다르게 적으므로 기계가 확정할 수는 없다. 여기서는 **초안** 만
뽑고 근거 문구를 함께 남겨, 담당자가 화면에서 확인하고 고치게 한다. 읽지 못한
항목은 비워 두고 이유를 남긴다 — 조용히 기본값을 넣으면 확인 없이 넘어간다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .actuarial import (
    FRACTION_DOWN,
    FRACTION_HALF,
    FRACTION_UP,
    SERVICE_ANNUAL,
    SERVICE_DAILY,
    SERVICE_MONTHLY,
    SERVICE_QUARTERLY,
    SERVICE_SEMIANNUAL,
)
from .normalize import text

__all__ = ["GeneralInfo", "PayoutRuleDraft", "read_general_info"]

GENERAL_SHEET = "일반사항"

#: 6번 항목의 행 배치. 자료요청서 서식이 고정되어 있어 행 번호로 찾는다.
_ROWS = {
    "eligibility": 110,
    "service_period": 111,
    "formula": 112,
    "base_wage": 113,
    "staff_nra": 114,
    "executive_nra": 115,
    "voluntary_rate": 116,
    "death_rate": 117,
    "normal_rate": 118,
    "payment_method": 119,
}

#: 값이 적히는 열 후보. 회사마다 D~F 사이에서 달라진다.
_VALUE_COLUMNS = (5, 6, 4, 7)


@dataclass(slots=True)
class PayoutRuleDraft:
    """읽어낸 지급규정 초안 한 벌.

    ``None`` 은 "읽지 못했다" 는 뜻이다. 0 이나 기본값과 구별해야 한다.
    """

    min_service_years: float | None = None
    staff_nra: int | None = None
    executive_nra: int | None = None
    executive_unlimited: bool = False
    """임원 정년이 '없음'/'제약 無' 로 적혀 있었는지."""
    service_basis: str | None = None
    service_fraction: str | None = None
    rounding_unit: int | None = None

    evidence: dict[str, str] = field(default_factory=dict)
    """항목 → 근거가 된 원문. 화면과 리포트에 그대로 보여 준다."""
    unread: list[str] = field(default_factory=list)
    """읽지 못한 항목. 담당자가 채워야 한다."""


@dataclass(slots=True)
class GeneralInfo:
    """``1)일반사항`` 에서 읽은 것 전부."""

    raw: dict[str, str] = field(default_factory=dict)
    """항목 → 원문."""
    draft: PayoutRuleDraft = field(default_factory=PayoutRuleDraft)

    @property
    def has_payout_section(self) -> bool:
        """6번 항목에 한 글자라도 적혀 있는지."""
        return any(self.raw.get(key) for key in _ROWS)


# ── 문구 해석 ────────────────────────────────────────────────────

_YEAR_PATTERN = re.compile(r"(\d+)\s*년\s*(?:이상|이후)")
_AGE_PATTERN = re.compile(r"(?:만\s*)?(\d{2})\s*세")
_ROUND_PATTERN = re.compile(r"ROUND\s*\([^,]*,\s*(-?\d+)\s*\)", re.IGNORECASE)

#: 임원 정년이 사실상 없다는 표현들.
_UNLIMITED = ("없음", "없슴", "제약", "무제한", "미적용", "해당없음", "n/a")


def _parse_min_service(eligibility: str, service_period: str) -> tuple[float | None, str]:
    """가입자격에서 최소 근속연수.

    '1년 이상 근속한 전직원' 처럼 가입자격 칸에 적히기도 하고, '근속기간 1년
    이상 (월할 계산)' 처럼 기간산정 칸에 적히기도 한다. 둘 다 본다.
    """
    for source in (eligibility, service_period):
        found = _YEAR_PATTERN.search(source)
        if found:
            return float(found.group(1)), source
    return None, ""


def _parse_service_basis(service_period: str, formula: str) -> tuple[str | None, str]:
    """근속기간 산정방법."""
    joined = f"{service_period} {formula}"
    table = (
        ("월할", SERVICE_MONTHLY),
        ("분기할", SERVICE_QUARTERLY),
        ("분기", SERVICE_QUARTERLY),
        ("반기할", SERVICE_SEMIANNUAL),
        ("반기", SERVICE_SEMIANNUAL),
        ("연할", SERVICE_ANNUAL),
        ("일수", SERVICE_DAILY),
        ("일할", SERVICE_DAILY),
        ("근속일수", SERVICE_DAILY),
    )
    for token, basis in table:
        if token in joined:
            return basis, joined.strip()

    # '1년이 되지 않는 단수개월은 절사' 는 곧 연 단위로 센다는 뜻이다.
    if "단수" in joined and ("절사" in joined or "버림" in joined):
        return SERVICE_ANNUAL, joined.strip()
    return None, ""


def _parse_fraction(service_period: str) -> tuple[str | None, str]:
    """단수 처리."""
    if "절사" in service_period or "버림" in service_period or "절하" in service_period:
        return FRACTION_DOWN, service_period
    if "절상" in service_period or "올림" in service_period:
        return FRACTION_UP, service_period
    if "반올림" in service_period:
        return FRACTION_HALF, service_period
    return None, ""


def _parse_age(value: str) -> tuple[int | None, bool]:
    """정년연령. ``(연령, 사실상없음)``."""
    token = value.strip()
    if not token:
        return None, False
    if any(word in token for word in _UNLIMITED):
        return None, True
    found = _AGE_PATTERN.search(token)
    if found:
        return int(found.group(1)), False
    bare = re.fullmatch(r"\d{2}", token)
    return (int(bare.group()), False) if bare else (None, False)


def _parse_rounding(formula: str, base_wage: str) -> tuple[int | None, str]:
    """``ROUND(..., -1)`` 에서 반올림 단위."""
    for source in (base_wage, formula):
        found = _ROUND_PATTERN.search(source)
        if found:
            digits = int(found.group(1))
            return (10 ** (-digits) if digits < 0 else 1), source
    return None, ""


def read_general_info(workbook) -> GeneralInfo:
    """``1)일반사항`` 시트를 읽어 지급규정 초안을 만든다.

    시트가 없으면 빈 :class:`GeneralInfo` 를 돌려준다(오류가 아니다 — 명부만
    보내오는 회사도 있다).
    """
    from .workbook import find_sheet

    info = GeneralInfo()
    ws = find_sheet(workbook, GENERAL_SHEET, "1)일반사항")
    if ws is None:
        info.draft.unread.append("1)일반사항 시트가 없습니다")
        return info

    for key, row in _ROWS.items():
        value = ""
        for col in _VALUE_COLUMNS:
            if col <= ws.max_column:
                candidate = text(ws.cell(row, col).value)
                if candidate:
                    value = candidate.replace("\n", " ")
                    break
        info.raw[key] = value

    draft = info.draft
    if not info.has_payout_section:
        draft.unread.append("6번 '회사의 퇴직금 지급규정' 항목이 비어 있습니다")
        return info

    minimum, evidence = _parse_min_service(
        info.raw.get("eligibility", ""), info.raw.get("service_period", "")
    )
    if minimum is None:
        draft.unread.append("가입자격(최소 근속연수)")
    else:
        draft.min_service_years = minimum
        draft.evidence["가입자격"] = evidence

    basis, evidence = _parse_service_basis(
        info.raw.get("service_period", ""), info.raw.get("formula", "")
    )
    if basis is None:
        draft.unread.append("근속기간 산정방법")
    else:
        draft.service_basis = basis
        draft.evidence["근속 산정방법"] = evidence

    fraction, evidence = _parse_fraction(info.raw.get("service_period", ""))
    if fraction is not None:
        draft.service_fraction = fraction
        draft.evidence["단수 처리"] = evidence

    staff, _ = _parse_age(info.raw.get("staff_nra", ""))
    if staff is None:
        draft.unread.append("정년연령(직원)")
    else:
        draft.staff_nra = staff
        draft.evidence["정년(직원)"] = info.raw.get("staff_nra", "")

    executive, unlimited = _parse_age(info.raw.get("executive_nra", ""))
    draft.executive_unlimited = unlimited
    if executive is not None:
        draft.executive_nra = executive
        draft.evidence["정년(임원)"] = info.raw.get("executive_nra", "")
    elif unlimited:
        # '없음' 이면 직원 정년을 그대로 쓰고 초과자는 가산연령으로 처리한다.
        draft.executive_nra = staff
        draft.evidence["정년(임원)"] = (
            f"{info.raw.get('executive_nra', '')} → 직원 정년과 동일하게 두고 "
            "초과자는 현재연령 + 가산연령"
        )
    else:
        draft.unread.append("정년연령(임원)")

    unit, evidence = _parse_rounding(
        info.raw.get("formula", ""), info.raw.get("base_wage", "")
    )
    if unit is not None:
        draft.rounding_unit = unit
        draft.evidence["지급액 반올림"] = evidence

    return info
