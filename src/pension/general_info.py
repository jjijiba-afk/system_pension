"""``일반사항`` 시트에서 지급규정 초안을 읽어낸다.

자료요청서 6번 항목(회사의 퇴직금 지급규정)은 담당자가 자유서술로 채운다.
같은 뜻을 회사마다 이런 식으로 달리 적는다.

    가입자격        전 임직원 / 1년 이상 근속자 / 근속 ○년 이상이 대상
    기간산정        근로기준법에 따른 산정법 - 일수 / 근속기간 1년 이상 (월할 계산)
                    1년이 되지 않는 단수개월은 절사
    정년(직원)      만 ○○세 / 정규직 ○○세, 계약직 ○○세
    정년(임원)      없음 / 정년 없음 / 제약 無
    계산구조        ROUND(평균임금*근속년월*지급률,-1) / 기초금액 × 근속연수별 지급률

같은 뜻을 저마다 다르게 적으므로 기계가 확정할 수는 없다. 여기서는 **초안** 만
뽑고 근거 문구를 함께 남겨, 담당자가 화면에서 확인하고 고치게 한다. 읽지 못한
항목은 비워 두고 이유를 남긴다 — 조용히 기본값을 넣으면 확인 없이 넘어간다.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Final

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

__all__ = [
    "AssetMovement",
    "GeneralInfo",
    "ObligationMovement",
    "PayoutRuleDraft",
    "read_general_info",
]

GENERAL_SHEET = "일반사항"
#: 같은 내용을 담은 다른 시트 이름들. 새 양식은 규정과 자산을 갈라 두었다 —
#: 한 시트에 규정·자산·기간이 섞여 있어 어디를 채울지 보이지 않았기 때문이다.
GENERAL_SHEET_ALIASES: tuple[str, ...] = (
    GENERAL_SHEET, "예치금", "사외적립자산", "기초자료", "퇴직급여규정")

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
class ObligationMovement:
    """5-1) 퇴직급여추계액 변동내역. 증감표의 지급·전입 줄에 그대로 쓴다."""

    transfer_in: float = 0.0
    """계열사 전입 (받은 금액)."""
    merger_in: float = 0.0
    """합병으로 받은 금액."""
    benefits_paid: float = 0.0
    """퇴직금 지급액(중간정산·DC전환·위로금·전출·사업처분 제외)."""
    settlement_paid: float = 0.0
    """중간정산금."""
    dc_converted: float = 0.0
    """DC 전환 지급액."""
    other_paid: float = 0.0
    """퇴직위로금(명예퇴직금 등)."""
    transfer_out: float = 0.0
    """계열사 전출."""
    disposal: float = 0.0
    """사업처분·분할."""

    def is_empty(self) -> bool:
        return not any(
            (self.transfer_in, self.merger_in, self.benefits_paid,
             self.settlement_paid, self.dc_converted, self.other_paid,
             self.transfer_out, self.disposal)
        )


@dataclass(slots=True)
class AssetMovement:
    """5-2) 사외적립자산 변동내역과 5-3) 세부내역.

    담당자가 신탁 명세서를 보고 이미 채워 둔 표다. 화면에 다시 옮겨 적게 하면
    스무 개 넘는 숫자를 손으로 나르는 셈이라, 여기서 그대로 읽는다.
    """

    opening: float = 0.0
    closing: float = 0.0
    contributions: float = 0.0
    """부담금 납입액."""
    actual_return: float = 0.0
    """장부상 이자수익. 재측정손익 검산에 쓴다."""
    transfer_in: float = 0.0
    merger_in: float = 0.0
    benefits_paid: float = 0.0
    settlement_paid: float = 0.0
    dc_converted: float = 0.0
    transfer_out: float = 0.0
    disposal: float = 0.0
    management_fee: float = 0.0
    """운용관리수수료."""
    custody_fee: float = 0.0
    """자산관리수수료."""
    national_pension: float = 0.0
    """국민연금전환금 기말 잔액. 별도 열로 관리된다."""
    unpaid_benefits: float = 0.0
    """기준일 현재 미지급 퇴직급여. 이미 퇴직했는데 결산일까지 못 준 금액."""
    asset_ceiling: float | None = None
    """자산인식상한(문단 64). ``None`` 이면 회사가 적지 않았다는 뜻이다.

    0 과 구별해야 한다 — 0 은 '상한이 0 원' 이고 ``None`` 은 '미적용' 이다.
    """
    breakdown: dict[str, float] = field(default_factory=dict)
    """자산 분류별 공정가치(문단 142 공시)."""
    quoted: dict[str, bool] = field(default_factory=dict)
    """분류별 **활성시장 공시가격 유무**(문단 142).

    문단 142 는 분류만으로 끝나지 않고 각 분류를 공시가격이 있는 것과 없는
    것으로 다시 나누라고 한다. 적히지 않은 분류는 여기 안 들어온다 — '없음'
    으로 단정하면 시세가 있는 국공채까지 없는 쪽으로 몰린다.
    """

    def is_empty(self) -> bool:
        return not (self.opening or self.closing or self.contributions)

    @property
    def total_paid(self) -> float:
        """자산에서 빠져나간 금액 전부(수수료 포함)."""
        return (
            self.benefits_paid + self.settlement_paid + self.dc_converted
            + self.transfer_out + self.disposal
            + self.management_fee + self.custody_fee
        )

    @property
    def total_received(self) -> float:
        """자산으로 들어온 금액(부담금 제외)."""
        return self.transfer_in + self.merger_in

    @property
    def difference(self) -> float:
        """검산 차이. 기초 + 유입 − 유출 − 기말.

        서식의 '검증' 줄과 같은 계산이다. 0 이 아니면 회사가 보내온 표 자체가
        맞지 않는다는 뜻이므로, 조용히 쓰지 말고 담당자에게 알려야 한다.
        """
        return (
            self.opening + self.contributions + self.actual_return
            + self.total_received - self.total_paid - self.closing
        )


@dataclass(slots=True)
class GeneralInfo:
    """``일반사항`` 에서 읽은 것 전부."""

    raw: dict[str, str] = field(default_factory=dict)
    """항목 → 원문."""
    draft: PayoutRuleDraft = field(default_factory=PayoutRuleDraft)

    period_start: _dt.date | None = None
    """2번 대상 회계기간 기시. 산출 시작일로 쓴다."""
    period_end: _dt.date | None = None
    """2번 대상 회계기간 기말. 산출기준일로 쓴다."""
    credit_grade: str = ""
    """4번 할인율 회사채 신용등급."""
    obligation: ObligationMovement = field(default_factory=ObligationMovement)
    assets: AssetMovement = field(default_factory=AssetMovement)
    longterm_paid: float = 0.0
    """7-2) 기중 장기근속 지급액."""
    longterm_received: float = 0.0
    """7-2) 기중 장기근속 받은 금액."""

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


# ── 5·7번 항목: 표에서 숫자 긁어오기 ────────────────────────────
# 행 번호를 못박지 않고 **라벨로** 찾는다. 6번 지급규정과 달리 이 표들은
# 회사가 줄을 넣고 빼는 일이 있어, 고정 행으로 읽으면 엉뚱한 값을 집는다.

#: 라벨 → 필드. 앞뒤 공백·괄호 표기가 흔들려서 부분일치로 본다.
_OBLIGATION_LABELS: Final = (
    # '퇴직급여 지급액'(우리 양식)과 '퇴직금 지급액'(자료요청서) 둘 다 읽는다.
    ("퇴직급여 지급", "benefits_paid"),
    ("계열사 전입", "transfer_in"),
    ("합병", "merger_in"),
    ("퇴직금 지급액", "benefits_paid"),
    ("중간정산금", "settlement_paid"),
    ("DC전환", "dc_converted"),
    ("퇴직위로금", "other_paid"),
    ("계열사 전출", "transfer_out"),
    ("사업처분", "disposal"),
)

_ASSET_LABELS: Final = (
    # '퇴직급여 지급액'(우리 양식)과 '퇴직금'(자료요청서) 둘 다 읽는다.
    ("퇴직급여 지급", "benefits_paid"),
    ("부담금납입", "contributions"),
    ("부담금 납입", "contributions"),
    ("이자수익", "actual_return"),
    ("계열사 전입", "transfer_in"),
    ("합병", "merger_in"),
    ("퇴직금", "benefits_paid"),
    ("중간정산금", "settlement_paid"),
    ("DC전환", "dc_converted"),
    ("계열사 전출", "transfer_out"),
    ("사업처분", "disposal"),
    ("운용관리수수료", "management_fee"),
    ("자산관리수수료", "custody_fee"),
)


#: 부호가 없는 항목들. 표의 '(-)감소' 칸을 음수로 적는 회사가 있는데, 그대로
#: 빼면 유출이 유입으로 뒤집혀 자산이 유출액의 두 배만큼 부풀려진다. 방향은
#: 항목 이름이 이미 정해 놓았으므로 크기만 쓴다. ``actual_return`` 만 예외다 —
#: 운용손실은 실제로 음수다.
_MAGNITUDE_FIELDS: Final = frozenset(
    {
        "transfer_in", "merger_in", "contributions", "benefits_paid",
        "settlement_paid", "dc_converted", "other_paid", "transfer_out",
        "disposal", "management_fee", "custody_fee",
    }
)


def _opt_number(value: object) -> float | None:
    """셀 값을 금액으로. 빈 칸과 숫자 0 을 구별해야 해서 ``None`` 을 쓴다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _number(value: object) -> float:
    """셀 값을 금액으로. 숫자가 아니면 0."""
    return _opt_number(value) or 0.0


def _find_row(ws, *needles: str, start: int = 1, limit: int = 200) -> int:
    """``needles`` 중 하나가 들어간 첫 행. 못 찾으면 0.

    표 제목을 바꿔도 옛 파일이 계속 읽혀야 해서 여러 이름을 받는다.
    """
    for row in range(start, min(ws.max_row, limit) + 1):
        for col in range(1, min(ws.max_column, 8) + 1):
            label = text(ws.cell(row, col).value)
            if any(needle in label for needle in needles):
                return row
    return 0


#: '2) 기중 장기근속 지급액' 처럼 번호가 붙은 표 제목.
_HEADING = re.compile(r"^\s*[0-9①-⑳]+\s*[).]")


def _find_item_row(ws, needle: str, start: int = 1, limit: int = 200) -> int:
    """항목 이름이 ``needle`` 인 행. 표 제목 줄은 건너뛴다.

    표 제목이 항목 이름을 그대로 품는 경우가 있다 — ``2) 기중 장기근속 지급액``
    과 그 아래 ``장기근속 지급액``. 제목을 먼저 집으면 금액 칸이 비어 있어
    0 원으로 읽힌다.
    """
    for row in range(start, min(ws.max_row, limit) + 1):
        for col in range(2, min(ws.max_column, 8) + 1):
            label = text(ws.cell(row, col).value)
            if needle in label and not _HEADING.match(label):
                return row
    return 0


def _row_label(ws, row: int) -> str:
    """그 행의 항목 이름. 구분 열(B~D) 중 글자가 있는 마지막 칸을 쓴다.

    **숫자 칸은 이름이 아니다.** 새 서식은 이름 바로 옆에 금액이 붙어 있어,
    숫자를 걸러내지 않으면 금액이 항목 이름 자리에 들어와 어느 줄도 맞지 않는다.
    """
    for col in (4, 3, 2):
        raw = ws.cell(row, col).value
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            continue
        label = text(raw)
        if label and not label.startswith(("(+)", "(-)")):
            return label
    return ""


def _row_amount(ws, row: int, columns: tuple[int, ...]) -> float:
    """그 행의 금액. 앞 열이 비어 있으면 다음 열(합계)을 본다."""
    for col in columns:
        amount = _number(ws.cell(row, col).value)
        if amount:
            return amount
    return 0.0


def _amount_column(ws, header: int, *names: str) -> int:
    """머리글에서 금액 열을 찾는다. 못 찾으면 0."""
    for col in range(2, min(ws.max_column, 12) + 1):
        label = text(ws.cell(header, col).value)
        if any(name in label for name in names):
            return col
    return 0


def _read_obligation(ws) -> ObligationMovement:
    head = _find_row(ws, "추계액·예치금 증감", "퇴직급여추계액 증감",
                     "퇴직급여추계액 변동내역")
    result = ObligationMovement()
    if not head:
        return result
    # 표 제목과 머리글 사이에 안내 줄이 들어가기도 한다. 두 줄을 다 본다.
    # 합쳐진 표에서는 열 이름이 '금액' 이 아니라 '퇴직급여추계액' 이다.
    money = (_amount_column(ws, head + 1, "추계액", "금액")
             or _amount_column(ws, head + 2, "추계액", "금액") or 5)

    # 바로 다음 표(사외적립자산)에서 멈춘다. 회사가 줄을 하나만 끼워 넣어도
    # 고정 길이로 훑으면 그 표까지 넘어가는데, 거기에도 '계열사 전입' 같은
    # 이름이 그대로 있어 방금 읽은 금액을 0 으로 덮어쓴다.
    stop = _find_row(ws, "예치금 증감", "사외적립자산 변동내역", "대사", "검증",
                     start=head + 1) or (head + 16)
    for row in range(head + 1, stop):
        label = _row_label(ws, row)
        if not label:
            continue
        for needle, field_name in _OBLIGATION_LABELS:
            if needle in label:
                if not getattr(result, field_name):
                    # 방향은 항목 이름이 정한다 — '(-)감소' 를 음수로 적어 온
                    # 표를 그대로 빼면 지급액이 전입으로 뒤집힌다.
                    setattr(result, field_name,
                            abs(_row_amount(ws, row, (money, 5))))
                break
    return result


@dataclass(slots=True, frozen=True)
class _AssetColumns:
    """5-2) 표의 금액 열 배치. 머리글에서 찾는다.

    열을 못박으면 회사가 신탁사별로 칸을 늘렸을 때 엉뚱한 곳을 읽는다.
    """

    db: int = 5
    pension: int = 6
    total: int = 7


def _asset_columns(ws, header: int) -> _AssetColumns:
    if not header:
        return _AssetColumns()
    db = pension = total = 0
    # 2열부터 훑는다. 새 서식은 이름 바로 옆(3열)에 금액이 붙는다 — 사이에 빈
    # 칸을 두면 화면에서 표가 한 칸 밀린 것처럼 보이기 때문이다.
    for col in range(2, min(ws.max_column, 12) + 1):
        label = text(ws.cell(header, col).value)
        if not db and ("예치금" in label or "DB퇴직연금" in label
                       or "퇴직보험" in label):
            db = col
        if not pension and "국민연금전환금" in label:
            pension = col
        if not total and "합계" in label:
            total = col
    return _AssetColumns(db=db or 5, pension=pension, total=total)


def _asset_amount(ws, row: int, cols: _AssetColumns) -> float:
    """5-2) 표 한 줄의 금액.

    합계 열을 먼저 믿으면 안 된다. 담당자가 줄을 밀려 적어 합계가 한 칸
    어긋난 파일이 실제로 있었고, 그대로 읽으면 수수료가 두 번 잡힌다.
    구성 열(DB + 국민연금전환금)이 적혀 있으면 그것을 더하고, 비어 있을
    때만 합계를 쓴다.
    """
    db = _opt_number(ws.cell(row, cols.db).value) if cols.db else None
    if db is not None:
        pension = (
            _opt_number(ws.cell(row, cols.pension).value) if cols.pension else None
        )
        return db + (pension or 0.0)
    if cols.total:
        return _number(ws.cell(row, cols.total).value)
    return 0.0


def _read_assets(ws) -> AssetMovement:
    head = _find_row(ws, "예치금 증감", "사외적립자산 변동내역")
    # 합쳐진 표에서는 ① 제목 한 줄이 추계액·예치금 둘 다 담당한다.
    result = AssetMovement()
    if not head:
        return result

    # 표의 머리글(구분 | DB퇴직연금 | 국민연금전환금 | 합계) 바로 다음 줄이
    # 기초 잔액, '검증' 바로 앞줄이 기말 잔액이다. 두 줄 모두 항목 이름 대신
    # 날짜가 적혀 있어 라벨로는 찾을 수 없다.
    header = _find_row(ws, "국민연금전환금", start=head)
    verify = _find_row(ws, "대사", "검증", start=head)

    # 국민연금전환금까지 더한 금액을 쓴다. DB퇴직연금 열만 보면 전환금이
    # 통째로 빠져, 전환금을 가진 회사에서 자산이 그만큼 모자라게 잡힌다.
    cols = _asset_columns(ws, header)

    if header:
        result.opening = _asset_amount(ws, header + 1, cols)
    if verify:
        result.closing = _asset_amount(ws, verify - 1, cols)
        if cols.pension:
            result.national_pension = _number(ws.cell(verify - 1, cols.pension).value)

    last = verify - 1 if verify else head + 18
    for row in range(header + 1 if header else head + 1, last):
        label = _row_label(ws, row)
        if not label:
            continue
        for needle, field_name in _ASSET_LABELS:
            if needle in label:
                # 라벨이 여러 개 걸리면(퇴직금 vs 퇴직금 지급액) 먼저 맞는 것을 쓴다.
                if not getattr(result, field_name):
                    amount = _asset_amount(ws, row, cols)
                    if field_name in _MAGNITUDE_FIELDS:
                        amount = abs(amount)
                    setattr(result, field_name, amount)
                break

    detail = _find_row(ws, "예치금 구성", "사외적립자산 세부내역")
    if detail:
        # 표 제목 바로 아래가 머리글이라는 보장이 없다 — 그 사이에 안내 줄이
        # 한 줄 들어가기도 한다. 머리글을 이름으로 찾아 그 열을 쓴다.
        head_row = _find_row(ws, "자산 분류", start=detail) or (detail + 1)
        name_col = next(
            (col for col in (2, 3)
             if text(ws.cell(head_row, col).value).startswith("자산")), 3)
        detail = head_row
        # 세부내역 다음에 오는 표에서 멈춘다. 고정 길이로 훑으면 그 표의 금액이
        # 자산 분류로 딸려 들어와, 분류별 합계가 기말 잔액과 어긋난다.
        stop = _find_row(ws, "그 밖의 입력", start=detail + 1) or (detail + 14)
        for row in range(detail + 1, stop):
            label = text(ws.cell(row, name_col).value)
            if not label or "합계" in label:
                continue
            amount = _row_amount(ws, row, (name_col + 1, 5, 6, 7))
            if amount:
                # '⑴ 현금 및 현금등가물' → '현금 및 현금등가물'
                clean = re.sub(r"^[^가-힣A-Za-z]+", "", label)
                result.breakdown[clean] = amount
                mark = text(ws.cell(row, name_col + 2).value)
                if mark in ("있음", "없음"):
                    result.quoted[clean] = mark == "있음"

    # '그 밖의 입력' — 표에 있으면서도 아무도 읽지 않으면, 채워 보낸 사람은
    # 화면에 손으로 한 번 더 적어야 한다. 물어봤으면 읽어야 한다.
    extras = _find_row(ws, "그 밖의 입력")
    if extras:
        for row in range(extras + 1, extras + 8):
            label = text(ws.cell(row, 2).value)
            if "자산인식상한" in label:
                result.asset_ceiling = _opt_number(ws.cell(row, 3).value)
            elif "미지급 퇴직급여" in label:
                result.unpaid_benefits = abs(_number(ws.cell(row, 3).value))
    return result


def _read_period(ws) -> tuple[_dt.date | None, _dt.date | None]:
    """2번 대상 회계기간(기시·기말)."""
    from .dates import to_date

    head = _find_row(ws, "대상 회계기간")
    if not head:
        return None, None
    for row in range(head, head + 5):
        for col in (2, 3, 4):
            if "기시" not in text(ws.cell(row, col).value):
                continue
            return (
                to_date(ws.cell(row + 1, col).value),
                to_date(ws.cell(row + 1, col + 1).value),
            )
    return None, None



def _labelled_date(ws, *names: str) -> _dt.date | None:
    """``이름 | 값`` 으로 적힌 날짜. 새 [기본정보] 시트를 읽는 방법이다."""
    from .dates import to_date

    wanted = {name.replace(" ", "") for name in names}
    for row in range(1, min(ws.max_row, 30) + 1):
        if text(ws.cell(row, 1).value).replace(" ", "") not in wanted:
            continue
        try:
            return to_date(ws.cell(row, 2).value)
        except Exception:      # noqa: BLE001 — 날짜가 아니면 없는 것으로 본다
            return None
    return None


#: 새 [퇴직급여규정] 시트의 항목 이름 → 저장할 열쇠.
_RULE_LABELS: Final = {
    "가입자격": "eligibility",
    "근속기간산정": "service_period",
    "계산구조": "formula",
    "기준임금": "base_wage",
    "정년직원": "staff_nra",
    "정년임원": "executive_nra",
    "중도퇴직지급률": "voluntary_rate",
    "사망퇴직지급률": "death_rate",
    "정년퇴직지급률": "normal_rate",
    "지급방법": "payment_method",
}


def _rules_by_label(ws) -> dict[str, str]:
    """지급규정을 **이름으로** 읽는다.

    옛 양식은 행 번호를 못박아 두었다(110~119행). 회사가 줄을 하나만 끼워 넣어도
    전부 어긋나는데, 어긋난 채로 초안이 만들어지면 담당자는 왜 엉뚱한 값이
    나왔는지 알 수 없다.
    """
    found: dict[str, str] = {}
    for row in range(1, min(ws.max_row, 60) + 1):
        for column in (1, 2):
            label = text(ws.cell(row, column).value).replace(" ", "")
            label = label.replace("(", "").replace(")", "")
            key = _RULE_LABELS.get(label)
            if not key:
                continue
            value = text(ws.cell(row, column + 1).value)
            if value:
                found.setdefault(key, value.replace("\n", " "))
    return found


def read_general_info(workbook) -> GeneralInfo:
    """``일반사항`` 시트를 읽어 지급규정 초안을 만든다.

    시트가 없으면 빈 :class:`GeneralInfo` 를 돌려준다(오류가 아니다 — 명부만
    보내오는 회사도 있다).
    """
    from .workbook import find_sheet

    info = GeneralInfo()
    # 옛 양식은 규정·자산·기간이 한 시트에 섞여 있었고, 새 양식은 뜻이 다른
    # 것을 갈라 두 시트로 나눴다. 어느 쪽이든 읽는다.
    general = find_sheet(workbook, GENERAL_SHEET)
    # '예치금' 은 지금 이름, '사외적립자산' 은 종전 이름이다. 퇴직금제도만 둔
    # 단체는 사외적립자산이라는 말을 안 쓰므로 이름을 바꿨고, 옛 파일도 계속
    # 읽어야 하므로 둘 다 본다. 규정·기본정보는 [기초자료] 한 장으로 합쳤다.
    money_ws = find_sheet(workbook, "예치금", "사외적립자산") or general
    rules_ws = find_sheet(workbook, "기초자료", "퇴직급여규정") or general
    basics_ws = find_sheet(workbook, "기초자료", "기본정보") or general
    if general is None and money_ws is None and rules_ws is None:
        info.draft.unread.append("일반사항 시트가 없습니다")
        return info

    ws = money_ws or rules_ws or basics_ws
    if money_ws is not None:
        info.period_start, info.period_end = _read_period(money_ws)
        info.obligation = _read_obligation(money_ws)
        info.assets = _read_assets(money_ws)

    # 새 양식은 회계기간을 [기본정보] 에 '산출 시작일 / 산출기준일' 로 적는다.
    # 옛 양식의 '2번 대상 회계기간' 이 없으므로 여기서도 찾아본다 — 못 읽으면
    # 이자원가를 1년으로 환산해 버려, 결산기가 바뀐 해에 조용히 틀린다.
    if info.period_end is None and basics_ws is not None:
        info.period_start = _labelled_date(basics_ws, "산출 시작일", "산출시작일")
        info.period_end = _labelled_date(basics_ws, "산출기준일", "결산일")

    # 신용등급은 양식마다 다른 시트에 있다. [기본정보] 를 먼저 본다.
    grades = ("AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "국고채")
    for sheet in (basics_ws, ws):
        if sheet is None or info.credit_grade:
            continue
        grade_row = _find_row(sheet, "회사채 신용등급")
        if not grade_row:
            continue
        for row in range(grade_row, grade_row + 8):
            for col in range(2, 7):
                candidate = text(sheet.cell(row, col).value).upper()
                if candidate in grades:
                    info.credit_grade = candidate
                    break
            if info.credit_grade:
                break

    paid_row = _find_item_row(ws, "장기근속 지급액")
    if paid_row:
        info.longterm_paid = abs(_row_amount(ws, paid_row, (3, 5, 4)))
    received_row = _find_item_row(ws, "장기근속 받은금액")
    if received_row:
        info.longterm_received = abs(_row_amount(ws, received_row, (5, 4)))

    by_label = _rules_by_label(rules_ws) if rules_ws is not None else {}
    for key, row in _ROWS.items():
        value = by_label.get(key, "")
        if not value and rules_ws is not None:
            for col in _VALUE_COLUMNS:
                if col <= rules_ws.max_column:
                    candidate = text(rules_ws.cell(row, col).value)
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
