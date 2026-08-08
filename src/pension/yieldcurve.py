"""채권 수익률 파일에서 할인율 곡선 읽기.

K-IFRS 1019 문단 83 은 확정급여채무를 **우량회사채의 시장수익률** 로 할인하라고
한다. 그 수익률은 결산일마다 새로 받아야 하고, 실무에서는 KIS채권평가·한국자산
평가 등이 내려 주는 만기별 금리표를 그대로 쓴다.

담당자가 그 표를 보고 손으로 옮겨 적으면 자릿수를 틀리기 쉽다. 만기가 17개나
되고 퍼센트 표기(3.404)와 소수 표기(0.03404)가 섞이기 때문이다. 그래서 파일을
그대로 읽어 ``할인율`` 시트로 바꾼다.

읽는 서식은 KIS-Net 금리표다. 한 행이 (기준일자, 구분, 등급) 하나이고, 열이
``3월``·``6월``·…·``50년`` 으로 이어진다.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .normalize import text

__all__ = [
    "INVESTMENT_GRADES",
    "YieldCurve",
    "read_yield_curves",
]

#: K-IFRS 1019 의 '우량회사채' 로 통용되는 등급. 앞의 것이 실무 기본값이다.
#:
#: 무엇을 우량으로 볼지는 회계정책이라 회사가 정한다. 국내 실무에서는 AA- 이상을
#: 우량회사채로 보는 것이 일반적이고, 그중 AA0 를 쓰는 곳이 가장 많다.
INVESTMENT_GRADES: Final[tuple[str, ...]] = ("AA0", "AA+", "AA-", "AAA")

_SHEET_HINTS: Final = ("KIS_NET금리", "금리", "수익률", "yield")

#: 머리글의 만기 표기 → 연 단위. ``1년6월`` 처럼 년·월이 붙어 오는 것도 받는다.
_TENOR = re.compile(r"(?:(\d+)\s*년)?\s*(?:(\d+)\s*(?:개?월))?")


def _tenor_years(label: object) -> float | None:
    """``'1년6월'`` → 1.5, ``'3월'`` → 0.25. 만기가 아니면 ``None``."""
    token = text(label).replace(" ", "")
    if not token:
        return None
    match = _TENOR.fullmatch(token)
    if match is None:
        return None
    years, months = match.group(1), match.group(2)
    if years is None and months is None:
        return None
    return int(years or 0) + int(months or 0) / 12.0


def _as_rate(value: object) -> float | None:
    """``3.404`` → 0.03404, ``0.03404`` → 0.03404.

    같은 표에서도 퍼센트 표기와 소수 표기가 섞여 온다. 국내 회사채 금리가
    1 을 넘는 일은 사실상 없으므로(=100%), 1 보다 크면 퍼센트로 본다.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rate = float(value)
    if rate <= 0:
        return None
    return rate / 100.0 if rate > 1.0 else rate


@dataclass(slots=True)
class YieldCurve:
    """한 등급의 만기별 현물이자율."""

    grade: str
    """등급 표기(``AA0``, ``국고채`` 등)."""
    category: str = ""
    """구분(``공모 무보증회사채`` 등)."""
    base_date: _dt.date | None = None
    points: list[tuple[float, float]] = field(default_factory=list)
    """``(만기(년), 이자율)`` 오름차순."""

    @property
    def label(self) -> str:
        return f"{self.category} {self.grade}".strip()

    def rows(self) -> list[list[float]]:
        """``할인율`` 시트에 쓸 ``[연차, 할인율]`` 행."""
        return [[years, rate] for years, rate in self.points]

    def rate_at(self, years: float) -> float:
        """``years`` 시점 이자율. 표 밖이면 양 끝 값을 쓴다.

        선형보간하지 않는다. 산출 엔진이 이 표를 계단식으로 읽으므로 여기서
        보간하면 두 곳의 규칙이 달라진다.
        """
        if not self.points:
            return 0.0
        best = self.points[0][1]
        for tenor, rate in self.points:
            if tenor > years:
                break
            best = rate
        return best


def read_yield_curves(path: str | Path) -> list[YieldCurve]:
    """금리표 파일에서 등급별 곡선을 모두 읽는다.

    :returns: 파일에 적힌 순서대로. 비어 있으면 빈 목록.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(Path(path), data_only=True)

    sheet = None
    for name in workbook.sheetnames:
        if any(hint in name for hint in _SHEET_HINTS):
            sheet = workbook[name]
            break
    if sheet is None:
        sheet = workbook[workbook.sheetnames[0]]

    # 머리글 행: 만기 표기가 세 개 이상 늘어선 첫 행.
    header_row = 0
    tenors: dict[int, float] = {}
    for row in range(1, min(sheet.max_row, 20) + 1):
        found = {
            col: years
            for col in range(1, sheet.max_column + 1)
            if (years := _tenor_years(sheet.cell(row, col).value)) is not None
        }
        if len(found) >= 3:
            header_row, tenors = row, found
            break
    if not tenors:
        return []

    labels = {
        text(sheet.cell(header_row, col).value): col
        for col in range(1, sheet.max_column + 1)
    }
    date_col = labels.get("기준일자")
    category_col = labels.get("구분")
    grade_col = labels.get("등급")

    curves: list[YieldCurve] = []
    for row in range(header_row + 1, sheet.max_row + 1):
        grade = text(sheet.cell(row, grade_col).value) if grade_col else ""
        if not grade:
            continue

        points = sorted(
            (years, rate)
            for col, years in tenors.items()
            if (rate := _as_rate(sheet.cell(row, col).value)) is not None
        )
        if not points:
            # '기타이율' 처럼 0 만 채워진 행. 곡선이 아니다.
            continue

        raw_date = sheet.cell(row, date_col).value if date_col else None
        curves.append(
            YieldCurve(
                grade=grade,
                category=text(sheet.cell(row, category_col).value) if category_col else "",
                base_date=raw_date.date() if isinstance(raw_date, _dt.datetime) else None,
                points=points,
            )
        )
    return curves


def pick_curve(curves: list[YieldCurve], grade: str = "") -> YieldCurve | None:
    """등급으로 곡선 하나를 고른다. 지정이 없으면 실무 기본값 순으로 찾는다."""
    wanted = text(grade)
    if wanted:
        for curve in curves:
            if curve.grade == wanted:
                return curve
        return None
    for candidate in INVESTMENT_GRADES:
        for curve in curves:
            if curve.grade == candidate:
                return curve
    return curves[0] if curves else None
