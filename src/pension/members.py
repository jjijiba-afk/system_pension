"""개인별 산출 결과 내보내기.

결과 리포트 안에도 ``개인별산출`` 시트가 있지만, 실무에서는 개인별 결과만 따로
받아 쓰는 경우가 잦다.

* 회계팀이 원가코드별로 갈라 제조원가/판관비에 배분한다
* 부서별로 나눠 담당자에게 확인을 받는다
* 전기 파일과 사번 기준으로 붙여 증감 원인을 사람 단위로 추적한다

그래서 퇴직급여와 장기급여를 **사번 기준 1인 1행** 으로 합치고, 원가코드·직군별
집계를 함께 담은 별도 파일을 만든다. 명부를 다시 열지 않아도 되도록 생년월일·
입사일·제도구분·적용 규정명도 같이 싣는다.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .pipeline import PensionRun

__all__ = ["MemberRow", "build_member_rows", "write_member_export"]

MEMBER_SHEET = "개인별산출"
COST_SHEET = "원가코드별집계"
GROUP_SHEET = "직군별집계"

_MONEY = "#,##0"
_YEARS = "#,##0.00"
_DATE = "yyyy-mm-dd"


@dataclass(slots=True)
class MemberRow:
    """한 사람의 퇴직급여·장기급여 산출 결과를 합친 한 줄."""

    employee_id: str = ""
    name: str = ""
    employee_type: str = ""
    job_group: str = ""
    gender: str = ""
    cost_code: str = ""
    plan: str = ""

    birth_date: _dt.date | None = None
    hire_date: _dt.date | None = None
    settlement_date: _dt.date | None = None
    age: int = 0
    past_service: float = 0.0
    retirement_age: int = 0
    projection_years: int = 0

    monthly_wage: float = 0.0
    accrued_benefit: float = 0.0
    dbo: float = 0.0
    service_cost: float = 0.0
    interest_cost: float = 0.0
    duration: float = 0.0
    funded_ratio: float = 0.0
    """DBO ÷ 퇴직급여추계액. 통상 0.7~1.1 을 벗어나면 규정 설정을 확인한다."""

    benefit_rule: str = ""
    withdrawal_rule: str = ""

    longterm_dbo: float = 0.0
    longterm_service_cost: float = 0.0
    longterm_interest_cost: float = 0.0
    longterm_next_milestone: int | None = None

    excluded_reason: str = ""
    longterm_excluded_reason: str = ""

    @property
    def total_dbo(self) -> float:
        """퇴직급여 + 장기급여 채무. 원가배분은 보통 이 합계로 한다."""
        return self.dbo + self.longterm_dbo


#: (머리글, MemberRow 속성명, 엑셀 표시서식, 열 너비)
_COLUMNS: tuple[tuple[str, str, str, int], ...] = (
    ("사번", "employee_id", "", 16),
    ("성명", "name", "", 10),
    ("임직원구분", "employee_type", "", 11),
    ("직군", "job_group", "", 12),
    ("성별", "gender", "", 7),
    ("원가코드", "cost_code", "", 14),
    ("제도구분", "plan", "", 11),
    ("생년월일", "birth_date", _DATE, 12),
    ("입사일자", "hire_date", _DATE, 12),
    ("근속기산일", "settlement_date", _DATE, 12),
    ("연령", "age", "", 7),
    ("근속연수", "past_service", _YEARS, 10),
    ("정년연령", "retirement_age", "", 9),
    ("투영연수", "projection_years", "", 9),
    ("30일 평균임금", "monthly_wage", _MONEY, 14),
    ("퇴직급여추계액", "accrued_benefit", _MONEY, 15),
    ("확정급여채무", "dbo", _MONEY, 15),
    ("당기근무원가", "service_cost", _MONEY, 14),
    ("이자원가(차기)", "interest_cost", _MONEY, 14),
    ("듀레이션", "duration", _YEARS, 10),
    ("추계액대비", "funded_ratio", "0.00", 10),
    ("지급률 규정", "benefit_rule", "", 14),
    ("퇴직률 규정", "withdrawal_rule", "", 14),
    ("장기급여채무", "longterm_dbo", _MONEY, 14),
    ("장기급여 근무원가", "longterm_service_cost", _MONEY, 15),
    ("장기급여 이자원가", "longterm_interest_cost", _MONEY, 15),
    ("다음 지급 근속", "longterm_next_milestone", "", 12),
    ("채무 합계", "total_dbo", _MONEY, 15),
    ("제외사유", "excluded_reason", "", 34),
    ("장기급여 제외사유", "longterm_excluded_reason", "", 30),
)


def build_member_rows(run: PensionRun) -> list[MemberRow]:
    """산출 결과를 사번 기준 1인 1행으로 합친다.

    사번이 비어 있는 사람도 있으므로(명부에서 자동 생성) 사번이 없으면 성명과
    생년월일로 짝을 맞춘다.
    """
    rows: list[MemberRow] = []
    index: dict[tuple[str, str], MemberRow] = {}

    for member in run.valuation.members:
        row = MemberRow(
            employee_id=member.employee_id,
            name=member.name,
            employee_type=member.employee_type,
            job_group=member.job_group,
            gender=member.gender,
            cost_code=member.cost_code,
            plan=member.plan,
            birth_date=member.birth_date,
            hire_date=member.hire_date,
            settlement_date=member.settlement_date,
            age=member.age,
            past_service=member.past_service,
            retirement_age=member.retirement_age,
            projection_years=member.projection_years,
            monthly_wage=member.monthly_wage,
            accrued_benefit=member.accrued_benefit,
            dbo=member.dbo,
            service_cost=member.service_cost,
            interest_cost=member.interest_cost,
            duration=member.duration,
            funded_ratio=member.funded_ratio_base,
            benefit_rule=member.benefit_rule,
            withdrawal_rule=member.withdrawal_rule,
            excluded_reason=member.excluded_reason,
        )
        rows.append(row)
        index[(member.employee_id, member.name)] = row

    if run.longterm is not None:
        for member in run.longterm.members:
            row = index.get((member.employee_id, member.name))
            if row is None:
                # 퇴직급여 대상이 아닌데 장기급여만 있는 경우. 흔치 않지만
                # 조용히 빠뜨리면 채무 합계가 맞지 않는다.
                row = MemberRow(
                    employee_id=member.employee_id,
                    name=member.name,
                    job_group=member.job_group,
                    age=member.age,
                    past_service=member.past_service,
                )
                rows.append(row)
                index[(member.employee_id, member.name)] = row

            row.longterm_dbo = member.dbo
            row.longterm_service_cost = member.service_cost
            row.longterm_interest_cost = member.interest_cost
            row.longterm_next_milestone = member.next_milestone
            row.longterm_excluded_reason = member.excluded_reason

    return rows


@dataclass(slots=True)
class _Bucket:
    """집계 한 칸."""

    headcount: int = 0
    monthly_wage: float = 0.0
    accrued_benefit: float = 0.0
    dbo: float = 0.0
    service_cost: float = 0.0
    interest_cost: float = 0.0
    longterm_dbo: float = 0.0

    def add(self, row: MemberRow) -> None:
        if not row.excluded_reason:
            self.headcount += 1
        self.monthly_wage += row.monthly_wage
        self.accrued_benefit += row.accrued_benefit
        self.dbo += row.dbo
        self.service_cost += row.service_cost
        self.interest_cost += row.interest_cost
        self.longterm_dbo += row.longterm_dbo

    def values(self) -> list[Any]:
        return [
            self.headcount, self.monthly_wage, self.accrued_benefit, self.dbo,
            self.service_cost, self.interest_cost, self.longterm_dbo,
            self.dbo + self.longterm_dbo,
        ]


_SUMMARY_HEADERS = (
    "인원", "30일 평균임금 합계", "퇴직급여추계액", "확정급여채무",
    "당기근무원가", "이자원가(차기)", "장기급여채무", "채무 합계",
)


def _group_by(rows: list[MemberRow], key) -> dict[str, _Bucket]:
    buckets: dict[str, _Bucket] = defaultdict(_Bucket)
    for row in rows:
        buckets[key(row) or "(미지정)"].add(row)
    return dict(sorted(buckets.items()))


def write_member_export(run: PensionRun, path: str | Path) -> Path:
    """개인별 결과를 별도 워크북으로 쓴다.

    :returns: 저장한 경로.
    """
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    path = Path(path)
    rows = build_member_rows(run)

    thin = Side(style="thin", color="BFBFBF")
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="44546A")
    total_font = Font(bold=True)
    total_fill = PatternFill("solid", fgColor="FFF2CC")
    excluded_fill = PatternFill("solid", fgColor="F2F2F2")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    wb = openpyxl.Workbook()
    del wb["Sheet"]

    # ── 개인별산출 ────────────────────────────────────────────────
    ws = wb.create_sheet(MEMBER_SHEET)
    ws.cell(1, 1, f"개인별 산출 결과 — 산출기준일 {run.config.base_date}").font = Font(
        bold=True, size=12, color="1F3864"
    )

    header_row = 3
    for col, (title, _attr, _fmt, width) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(header_row, col, title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = border
        ws.column_dimensions[get_column_letter(col)].width = width

    for offset, row in enumerate(rows, start=header_row + 1):
        for col, (_title, attr, fmt, _width) in enumerate(_COLUMNS, start=1):
            cell = ws.cell(offset, col, getattr(row, attr))
            cell.border = border
            if fmt:
                cell.number_format = fmt
            if row.excluded_reason:
                # 산출에서 빠진 사람은 회색으로 표시해 합계와 대조할 때 눈에 띄게 한다.
                cell.fill = excluded_fill

    total_row = header_row + len(rows) + 1
    ws.cell(total_row, 1, "합계").font = total_font
    ws.cell(total_row, 1).fill = total_fill
    for col, (_title, attr, fmt, _width) in enumerate(_COLUMNS, start=1):
        if attr not in {
            "monthly_wage", "accrued_benefit", "dbo", "service_cost",
            "interest_cost", "longterm_dbo", "longterm_service_cost",
            "longterm_interest_cost", "total_dbo",
        }:
            if col > 1:
                ws.cell(total_row, col).fill = total_fill
                ws.cell(total_row, col).border = border
            continue
        cell = ws.cell(total_row, col, sum(getattr(r, attr) for r in rows))
        cell.font = total_font
        cell.fill = total_fill
        cell.number_format = fmt or _MONEY
        cell.border = border

    ws.auto_filter.ref = (
        f"A{header_row}:{get_column_letter(len(_COLUMNS))}{header_row + len(rows)}"
    )

    # ── 집계 시트 ─────────────────────────────────────────────────
    def summary_sheet(name: str, first_header: str, buckets: dict[str, _Bucket]) -> None:
        sheet = wb.create_sheet(name)
        headers = [first_header, *_SUMMARY_HEADERS]
        for col, title in enumerate(headers, start=1):
            cell = sheet.cell(1, col, title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
            sheet.column_dimensions[get_column_letter(col)].width = 18 if col == 1 else 16

        for offset, (key, bucket) in enumerate(buckets.items(), start=2):
            sheet.cell(offset, 1, key).border = border
            for col, value in enumerate(bucket.values(), start=2):
                cell = sheet.cell(offset, col, value)
                cell.border = border
                if col > 2:
                    cell.number_format = _MONEY

        last = len(buckets) + 2
        sheet.cell(last, 1, "합계").font = total_font
        sheet.cell(last, 1).fill = total_fill
        sheet.cell(last, 1).border = border
        totals = _Bucket()
        for bucket in buckets.values():
            for attr in (
                "headcount", "monthly_wage", "accrued_benefit", "dbo",
                "service_cost", "interest_cost", "longterm_dbo",
            ):
                setattr(totals, attr, getattr(totals, attr) + getattr(bucket, attr))
        for col, value in enumerate(totals.values(), start=2):
            cell = sheet.cell(last, col, value)
            cell.font = total_font
            cell.fill = total_fill
            cell.border = border
            if col > 2:
                cell.number_format = _MONEY

    summary_sheet(COST_SHEET, "원가코드", _group_by(rows, lambda r: r.cost_code))
    summary_sheet(GROUP_SHEET, "직군", _group_by(rows, lambda r: r.job_group))

    wb.save(path)
    return path
