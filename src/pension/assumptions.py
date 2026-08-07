"""계리 기초율(가정) 정의와 로딩.

원본 통합문서의 종전 규칙 주석은 ``지급률``, ``지급률(사망)``, ``지급률(정년)`` 같은
기초율 시트를 참조하지만 배포된 샘플 파일에는 명부 시트만 들어 있다. 그래서
기초율은 별도 워크북으로 분리하고, 그 서식을 여기서 정의한다.
:func:`write_template` 로 빈 양식을 만들어 채워 넣으면 된다.

기초율 워크북 시트 구성::

    할인율        연차 | 할인율            (1행짜리면 전 기간 단일 할인율)
    임금상승률    연차 | 상승률            (Base-up)
    승급률        기준(연령/근속) | 규정명들…
    퇴직률        기준(연령/근속) | 규정명들…
    사망률        연령 | 남자 | 여자
    지급률        근속연수 | 규정명들…     (월평균임금 대비 누적 배수)
    장기급여지급률 근속연수 | 규정명들…    (일 기본급 대비 지급일수)

모든 표는 **계단식 조회** 다. 찾는 키보다 작거나 같은 가장 큰 행의 값을 쓴다.
예를 들어 퇴직률 표에 20·25·30세만 있으면 27세는 25세 행의 값을 쓴다.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from .normalize import Gender, text

__all__ = [
    "STATUTORY_RULE",
    "Assumptions",
    "BenefitScale",
    "DiscountCurve",
    "MortalityTable",
    "RateCurve",
    "RateTable",
    "SalaryScale",
    "load_assumptions",
    "write_template",
]

STATUTORY_RULE: Final = "법정"
"""지급률 규정을 찾지 못했을 때 쓰는 기본 규정명.

근로자퇴직급여보장법의 법정 퇴직금(계속근로 1년당 30일분 평균임금)을 뜻한다.
30일 평균임금이 급여 기준이므로 지급 배수는 근속연수와 같다.
"""

DISCOUNT_SHEET: Final = "할인율"
SALARY_SHEET: Final = "임금상승률"
PROMOTION_SHEET: Final = "승급률"
WITHDRAWAL_SHEET: Final = "퇴직률"
MORTALITY_SHEET: Final = "사망률"
BENEFIT_SHEET: Final = "지급률"
LONGTERM_SHEET: Final = "장기급여지급률"


@dataclass(slots=True)
class RateCurve:
    """정수 키(연령 또는 근속연수) 하나로 조회하는 계단식 비율 곡선."""

    points: dict[int, float] = field(default_factory=dict)
    _keys: list[int] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._keys = sorted(self.points)

    def rate(self, key: float) -> float:
        """``key`` 이하의 가장 큰 구간 값. 표가 비었거나 키가 표보다 작으면 0."""
        if not self._keys:
            return 0.0
        idx = bisect_right(self._keys, math.floor(key)) - 1
        if idx < 0:
            return self.points[self._keys[0]]
        return self.points[self._keys[idx]]

    def scaled(self, factor: float) -> RateCurve:
        """모든 값에 배수를 적용한 새 곡선. 민감도 분석에 쓴다."""
        return RateCurve({k: v * factor for k, v in self.points.items()})

    def shifted(self, delta: float) -> RateCurve:
        """모든 값에 절대값을 더한 새 곡선(음수는 0 으로 자름)."""
        return RateCurve({k: max(0.0, v + delta) for k, v in self.points.items()})

    def __bool__(self) -> bool:
        return bool(self.points)


@dataclass(slots=True)
class RateTable:
    """규정명별 :class:`RateCurve` 묶음(퇴직률·승급률)."""

    basis: str = "연령"
    """조회 기준. ``"연령"`` 또는 ``"근속"``."""
    curves: dict[str, RateCurve] = field(default_factory=dict)
    default_rule: str = ""
    """규정명을 못 찾았을 때 대신 쓸 규정명. 비면 0 을 쓴다."""

    def curve(self, rule: str) -> RateCurve:
        curve = self.curves.get(text(rule))
        if curve is None and self.default_rule:
            curve = self.curves.get(self.default_rule)
        return curve or RateCurve()

    def rate(self, rule: str, *, age: float, service: float) -> float:
        key = age if self.basis == "연령" else service
        return self.curve(rule).rate(key)

    def transformed(self, fn) -> RateTable:
        """모든 곡선에 같은 변환을 적용한 사본."""
        return RateTable(
            basis=self.basis,
            curves={name: fn(curve) for name, curve in self.curves.items()},
            default_rule=self.default_rule,
        )


@dataclass(slots=True)
class MortalityTable:
    """성별 연령별 사망률 ``qx``."""

    male: RateCurve = field(default_factory=RateCurve)
    female: RateCurve = field(default_factory=RateCurve)

    def qx(self, gender: Gender, age: float) -> float:
        curve = self.female if gender is Gender.FEMALE else self.male
        return min(1.0, max(0.0, curve.rate(age)))

    def scaled(self, factor: float) -> MortalityTable:
        return MortalityTable(self.male.scaled(factor), self.female.scaled(factor))

    def __bool__(self) -> bool:
        return bool(self.male) or bool(self.female)


@dataclass(slots=True)
class DiscountCurve:
    """할인율 기간구조.

    한 행만 주면 전 기간 단일 할인율로 본다. 여러 행이면 각 연차의 **현물이자율
    (spot rate)** 로 보고 ``v(t) = (1 + s_t)^-t`` 로 할인한다.
    """

    spot: RateCurve = field(default_factory=RateCurve)
    flat: float | None = None

    def rate(self, t: float) -> float:
        if self.flat is not None:
            return self.flat
        return self.spot.rate(t)

    def discount_factor(self, t: float) -> float:
        """``t`` 년 뒤 현금흐름의 현가계수."""
        if t <= 0:
            return 1.0
        return (1.0 + self.rate(t)) ** (-t)

    def shifted(self, delta: float) -> DiscountCurve:
        if self.flat is not None:
            return DiscountCurve(flat=max(0.0, self.flat + delta))
        return DiscountCurve(spot=self.spot.shifted(delta))

    @property
    def level_rate(self) -> float:
        """이자원가 계산 등에 쓰는 대표 할인율(1년 시점 기준)."""
        return self.rate(1)


@dataclass(slots=True)
class SalaryScale:
    """임금상승률.

    총 상승률 = **Base-up**(연차별 공통) + **승급률**(규정·연령/근속별)로 본다.
    ``1)일반사항`` 시트가 "승진·승급으로 인한 임금인상 제외" 한 Base-up 을 따로
    받는 것과 같은 구조다.
    """

    base_up: RateCurve = field(default_factory=RateCurve)
    promotion: RateTable = field(default_factory=RateTable)

    def rate(self, rule: str, *, year: int, age: float, service: float) -> float:
        return self.base_up.rate(year) + self.promotion.rate(rule, age=age, service=service)

    def shifted(self, delta: float) -> SalaryScale:
        return SalaryScale(base_up=self.base_up.shifted(delta), promotion=self.promotion)


@dataclass(slots=True)
class BenefitScale:
    """지급률 표.

    퇴직급여는 근속연수에 대한 **월평균임금 배수**(법정이면 근속연수와 동일),
    장기급여는 **일 기본급 대비 지급일수** 를 담는다.
    """

    curves: dict[str, RateCurve] = field(default_factory=dict)
    statutory_when_missing: bool = True
    """규정을 못 찾으면 법정 퇴직금(배수 = 근속연수)으로 볼지."""

    def multiple(self, rule: str, service: float) -> float:
        curve = self.curves.get(text(rule))
        if curve is None:
            return service if self.statutory_when_missing else 0.0
        return curve.rate(service)

    def milestones(self, rule: str) -> list[tuple[int, float]]:
        """(근속연수, 지급값) 목록. 장기급여의 지급 시점 산정에 쓴다."""
        curve = self.curves.get(text(rule))
        if curve is None:
            return []
        return sorted(curve.points.items())

    def has_rule(self, rule: str) -> bool:
        return text(rule) in self.curves


@dataclass(slots=True)
class Assumptions:
    """계리 산출에 필요한 기초율 일체."""

    discount: DiscountCurve = field(default_factory=DiscountCurve)
    salary: SalaryScale = field(default_factory=SalaryScale)
    withdrawal: RateTable = field(default_factory=RateTable)
    """중도퇴직률(사망 제외)."""
    mortality: MortalityTable = field(default_factory=MortalityTable)
    severance_benefit: BenefitScale = field(default_factory=BenefitScale)
    longterm_benefit: BenefitScale = field(
        default_factory=lambda: BenefitScale(statutory_when_missing=False)
    )

    label: str = "당기 가정"
    """리포트에 표시할 이름. 민감도·증감분석에서 구분자로 쓴다."""

    max_projection_years: int = 60
    """투영 상한. 정년연령이 잘못 들어와도 무한 루프에 빠지지 않게 한다."""

    def replace(self, **changes: Any) -> Assumptions:
        """일부 가정만 바꾼 사본. 민감도 분석용."""
        from dataclasses import replace as _replace

        return _replace(self, **changes)


# ────────────────────────────────────────────────────────────────
# 워크북 입출력
# ────────────────────────────────────────────────────────────────


def _rows(ws, first_row: int = 2) -> Iterable[tuple[int, list[Any]]]:
    for row in range(first_row, ws.max_row + 1):
        values = [ws.cell(row, c).value for c in range(1, ws.max_column + 1)]
        if all(v in (None, "") for v in values):
            continue
        yield row, values


def _as_number(value: object) -> float | None:
    """숫자 셀 해석. ``%`` 표기는 100 으로 나눈다."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    token = text(value).replace(",", "")
    percent = token.endswith("%")
    if percent:
        token = token[:-1]
    try:
        number = float(token)
    except ValueError:
        return None
    return number / 100.0 if percent else number


def _as_rate(value: object) -> float | None:
    """비율 셀 해석. ``5%`` / ``0.05`` / ``5`` 를 모두 0.05 로 읽는다.

    엑셀 백분율 서식은 이미 0.05 로 저장되므로, 1 을 넘는 값만 "퍼센트 단위로
    적은 것" 으로 본다. 비율은 1(=100%)을 넘을 수 없으니 안전한 규칙이다.
    지급률처럼 1 을 넘을 수 있는 값에는 :func:`_as_number` 를 쓴다.
    """
    number = _as_number(value)
    if number is None:
        return None
    return number / 100.0 if number > 1.0 else number


def _as_int(value: object) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(float(text(value)))
    except ValueError:
        return None


def _read_rate_table(wb, sheet_name: str, *, as_rate: bool = True) -> RateTable:
    """규정명이 열 머리글인 표를 :class:`RateTable` 로.

    :param as_rate: 값을 비율(0~1)로 볼지. 지급률처럼 1 을 넘는 배수는 ``False``.
    """
    if sheet_name not in wb.sheetnames:
        return RateTable()
    ws = wb[sheet_name]
    parse = _as_rate if as_rate else _as_number

    header = [text(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)]
    if not header:
        return RateTable()
    basis = "근속" if "근속" in header[0] else "연령"

    rules = [(idx + 2, name) for idx, name in enumerate(header[1:]) if name]
    curves: dict[str, dict[int, float]] = {name: {} for _, name in rules}

    for row, _ in _rows(ws):
        key = _as_int(ws.cell(row, 1).value)
        if key is None:
            continue
        for col, name in rules:
            rate = parse(ws.cell(row, col).value)
            if rate is not None:
                curves[name][key] = rate

    table = RateTable(
        basis=basis,
        curves={name: RateCurve(points) for name, points in curves.items() if points},
    )
    # 규정이 하나뿐이면 이름을 못 맞춰도 그 값을 쓰도록 기본값으로 지정한다.
    if len(table.curves) == 1:
        table.default_rule = next(iter(table.curves))
    return table


def _read_benefit_scale(wb, sheet_name: str, *, statutory: bool) -> BenefitScale:
    """지급률 표. 값은 배수·일수이므로 비율 환산을 하지 않는다."""
    table = _read_rate_table(wb, sheet_name, as_rate=False)
    return BenefitScale(curves=dict(table.curves), statutory_when_missing=statutory)


def _read_single_curve(wb, sheet_name: str) -> RateCurve:
    if sheet_name not in wb.sheetnames:
        return RateCurve()
    ws = wb[sheet_name]
    points: dict[int, float] = {}
    for row, _ in _rows(ws):
        key = _as_int(ws.cell(row, 1).value)
        rate = _as_rate(ws.cell(row, 2).value)
        if key is not None and rate is not None:
            points[key] = rate
    return RateCurve(points)


def _read_mortality(wb) -> MortalityTable:
    if MORTALITY_SHEET not in wb.sheetnames:
        return MortalityTable()
    ws = wb[MORTALITY_SHEET]
    male: dict[int, float] = {}
    female: dict[int, float] = {}
    for row, _ in _rows(ws):
        age = _as_int(ws.cell(row, 1).value)
        if age is None:
            continue
        m = _as_rate(ws.cell(row, 2).value)
        f = _as_rate(ws.cell(row, 3).value)
        if m is not None:
            male[age] = m
        if f is not None:
            female[age] = f
    return MortalityTable(RateCurve(male), RateCurve(female))


def load_assumptions(path: str | Path, *, label: str = "당기 가정") -> Assumptions:
    """기초율 워크북을 읽는다.

    :param path: ``.xlsx``/``.xlsm`` 경로.
    :raises FileNotFoundError: 파일이 없을 때.
    :raises ValueError: 할인율 시트가 비어 있을 때(할인율 없이는 산출 불가).
    """
    import openpyxl

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"기초율 파일을 찾을 수 없습니다: {path}")

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    try:
        spot = _read_single_curve(wb, DISCOUNT_SHEET)
        if not spot:
            raise ValueError(
                f"'{DISCOUNT_SHEET}' 시트에서 할인율을 읽지 못했습니다. "
                "A열에 연차, B열에 할인율을 입력하세요"
            )
        flat = next(iter(spot.points.values())) if len(spot.points) == 1 else None

        return Assumptions(
            discount=DiscountCurve(spot=spot, flat=flat),
            salary=SalaryScale(
                base_up=_read_single_curve(wb, SALARY_SHEET),
                promotion=_read_rate_table(wb, PROMOTION_SHEET),
            ),
            withdrawal=_read_rate_table(wb, WITHDRAWAL_SHEET),
            mortality=_read_mortality(wb),
            severance_benefit=_read_benefit_scale(wb, BENEFIT_SHEET, statutory=True),
            longterm_benefit=_read_benefit_scale(wb, LONGTERM_SHEET, statutory=False),
            label=label,
        )
    finally:
        wb.close()


def write_template(path: str | Path, *, job_groups: Iterable[str] = ()) -> Path:
    """빈 기초율 워크북 양식을 만든다.

    담당자가 채워 넣을 시트 구조와 예시 몇 줄을 넣어 준다.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    path = Path(path)
    rules = [text(g) for g in job_groups if text(g)] or ["정규직", "임원"]

    wb = openpyxl.Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="44546A")
    note_font = Font(italic=True, color="808080")

    def make(name: str, headers: list[str], note: str, rows: list[list[Any]]) -> None:
        ws = wb.create_sheet(name)
        for col, title in enumerate(headers, start=1):
            cell = ws.cell(1, col, title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[cell.column_letter].width = max(12, len(title) + 4)
        for offset, values in enumerate(rows, start=2):
            for col, value in enumerate(values, start=1):
                ws.cell(offset, col, value)
        ws.cell(len(rows) + 3, 1, note).font = note_font
        ws.freeze_panes = "A2"

    make(
        DISCOUNT_SHEET, ["연차", "할인율"],
        "· 한 줄만 넣으면 전 기간 단일 할인율입니다. 여러 줄이면 각 연차의 현물이자율(spot)로 봅니다.",
        [[1, 0.045]],
    )
    make(
        SALARY_SHEET, ["연차", "Base-up 상승률"],
        "· 승진·승급 제외한 공통 인상률입니다. 마지막 줄의 값이 그 이후 전 기간에 적용됩니다.",
        [[1, 0.03], [6, 0.025]],
    )
    make(
        PROMOTION_SHEET, ["연령", *rules],
        "· 승급(호봉·승진) 상승률. A1 을 '근속'으로 바꾸면 근속연수 기준으로 조회합니다.",
        [[20, *[0.02] * len(rules)], [40, *[0.01] * len(rules)], [55, *[0.0] * len(rules)]],
    )
    make(
        WITHDRAWAL_SHEET, ["연령", *rules],
        "· 사망을 제외한 연간 중도퇴직률입니다. A1 을 '근속'으로 바꾸면 근속연수 기준입니다.",
        [[20, *[0.15] * len(rules)], [35, *[0.06] * len(rules)], [50, *[0.02] * len(rules)]],
    )
    make(
        MORTALITY_SHEET, ["연령", "남자", "여자"],
        "· 연간 사망률 qx. 경험생명표 등 사용한 표의 출처를 함께 남겨 두세요.",
        [[20, 0.00040, 0.00020], [40, 0.00120, 0.00060], [60, 0.00600, 0.00250]],
    )
    make(
        BENEFIT_SHEET, ["근속연수", *rules],
        "· 30일 평균임금 대비 누적 지급배수입니다. 비워 두면 법정 퇴직금(배수 = 근속연수)으로 봅니다.",
        [[1, *[1.0] * len(rules)], [10, *[10.0] * len(rules)], [20, *[20.0] * len(rules)]],
    )
    make(
        LONGTERM_SHEET, ["근속연수", *rules],
        "· 근속 포상·장기근속휴가의 지급일수입니다(일 기본급 × 일수). 해당 근속연수 도달 시 지급으로 봅니다.",
        [[10, *[10] * len(rules)], [20, *[20] * len(rules)], [30, *[30] * len(rules)]],
    )

    del wb["Sheet"]
    wb.save(path)
    return path
