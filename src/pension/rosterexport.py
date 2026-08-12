"""받아 온 명부를 **지금 양식** 으로 다시 낸다.

회사가 보내오는 명부는 열 순서도 머리글도 제각각이고, 열 개수가 모자라거나
가운데에 자기네 열이 끼어 있다. 그것을 매 결산마다 눈으로 맞춰 보는 대신,
한 번 올린 명부를 우리 양식 그대로 되받아 다음 해에 그것을 채워 보내면 된다.

**원본 값을 그대로 옮긴다.** 날짜를 고쳐 쓰거나 성별을 채워 넣거나 규정명이
빈 칸을 메우지 않는다. 산출이 넘겨짚은 값이 회사가 적어 보낸 것처럼 되돌아
오면, 다음 해에는 그것이 원본 행세를 하기 때문이다. 이 모듈이 하는 일은
**열의 자리를 옮기는 것뿐** 이다.

알아보지 못한 열은 버리지 않고 오른쪽에 그대로 붙인다. 회사가 쓰는 열을
지워 돌려주면 그쪽 담당자가 자기 자료를 잃는다.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .normalize import text

__all__ = ["ExportReport", "SheetReport", "relayout_roster"]


@dataclass(slots=True)
class SheetReport:
    """명부 한 장을 옮긴 결과."""

    sheet: str
    """원본 시트 이름. 못 찾았으면 빈 문자열."""
    header_row: int = 0
    """머리글을 찾은 행. 0 이면 못 찾았다."""
    rows: int = 0
    """옮긴 사람 수."""
    matched: list[tuple[str, str]] = field(default_factory=list)
    """``(원본 머리글, 우리 양식의 열 이름)``. 자리를 옮긴 열."""
    carried: list[str] = field(default_factory=list)
    """알아보지 못해 오른쪽에 그대로 붙인 원본 머리글."""


@dataclass(slots=True)
class ExportReport:
    """되받은 명부가 어떻게 만들어졌는지."""

    active: SheetReport
    retired: SheetReport
    basics: dict[str, Any] = field(default_factory=dict)
    """[기본정보] 에 옮겨 적은 값."""
    groups: list[tuple] = field(default_factory=list)
    """[기본정보] 직군 규칙 표에 옮겨 적은 줄."""

    def summary(self) -> str:
        parts = []
        for report in (self.active, self.retired):
            if report.header_row:
                parts.append(f"{report.sheet} {report.rows}명"
                             f"(열 {len(report.matched)}개 정리"
                             + (f", {len(report.carried)}개 그대로"
                                if report.carried else "") + ")")
        return " · ".join(parts) or "옮길 명부를 찾지 못했습니다"


def _alias_index(aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """정규화한 머리글 → 필드명. 앞의 별칭이 이긴다."""
    from .layout import normalize_header

    found: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            found.setdefault(normalize_header(name), key)
    return found


def _clean(value: object) -> Any:
    """셀 값 하나. 뜻은 건드리지 않고 담을 수 있는 모양으로만 맞춘다."""
    if value is None:
        return ""
    if isinstance(value, _dt.datetime):
        # 시각이 붙어 있으면 엑셀에서 '2020-03-02 00:00:00' 으로 보인다.
        # 날짜만 남기는 것은 뜻을 바꾸는 것이 아니라 표기를 고르는 일이다.
        return value.date() if value.time() == _dt.time() else value
    if isinstance(value, str):
        return value.strip()
    return value


def _read_sheet(sheet, columns: list, aliases: dict[str, tuple[str, ...]],
                name: str) -> tuple[list[dict[str, Any]], tuple[str, ...], SheetReport]:
    """한 장을 읽어 ``(줄들, 덧붙일 열, 보고)`` 로.

    줄의 열쇠는 **우리 양식의 열 이름** 이다 — 그대로 :func:`build_workbook`
    에 넘길 수 있다.
    """
    from .layout import find_data_start, find_header_row, normalize_header
    from .rostertemplate import label_for

    report = SheetReport(sheet=name)
    if sheet is None:
        return [], (), report

    header_row = find_header_row(sheet, aliases)
    if not header_row:
        # 머리글이 없으면 어느 칸이 무엇인지 짚을 근거가 없다. 자리를 옮기는
        # 일이므로 짐작으로 옮기면 통째로 어긋난 명부를 돌려주게 된다.
        return [], (), report
    report.header_row = header_row

    known = label_for(columns, aliases)                   # 필드 → 우리 열 이름
    by_alias = _alias_index(aliases)                      # 머리글 → 필드
    ours = {normalize_header(label): label for _b, label, *_r in columns}

    taken: set[str] = set()
    plan: list[tuple[int, str]] = []                      # (원본 열 번호, 우리 열 이름)
    extras: list[str] = []
    for column in range(1, min(sheet.max_column, 120) + 1):
        raw = text(sheet.cell(header_row, column).value)
        key = normalize_header(raw)
        if not key:
            continue
        target = ours.get(key) or known.get(by_alias.get(key, ""), "")
        if not target or target in taken:
            # 알아보지 못했거나, 같은 뜻의 열이 이미 하나 잡혔다. 버리지 않고
            # 원본 머리글 그대로 오른쪽에 붙인다.
            label = raw or f"열{column}"
            while label in taken:
                label += " "
            extras.append(label)
            plan.append((column, label))
            report.carried.append(raw)
        else:
            plan.append((column, target))
            report.matched.append((raw, target))
        taken.add(plan[-1][1])

    anchor = next(
        (c for c, target in plan
         if target in (known.get("employee_id"), known.get("birth_date"),
                       known.get("name"))),
        plan[0][0] if plan else 0,
    )

    rows: list[dict[str, Any]] = []
    for row in range(find_data_start(sheet, header_row), sheet.max_row + 1):
        if anchor and not text(sheet.cell(row, anchor).value):
            continue                                     # 사람이 없는 줄
        line: dict[str, Any] = {"순번": len(rows) + 1}
        for column, target in plan:
            value = _clean(sheet.cell(row, column).value)
            if value != "":
                line[target] = value
        if len(line) > 1:
            rows.append(line)

    report.rows = len(rows)
    return rows, tuple(extras), report


def _basics_of(book, actives: int) -> tuple[dict[str, Any], list[tuple]]:
    """[기본정보] 에 옮겨 적을 것. 못 읽으면 그 항목만 빈다."""
    from .config import read_config
    from .general_info import read_general_info
    from .workbook import find_sheet

    values: dict[str, Any] = {}

    sheet = find_sheet(book, "기본정보")
    if sheet is not None:
        for row in range(1, min(sheet.max_row, 12) + 1):
            label = text(sheet.cell(row, 1).value)
            if label in ("단체명", "상시근로자 수"):
                cell = _clean(sheet.cell(row, 2).value)
                if cell != "":
                    values[label] = cell

    try:
        info = read_general_info(book)
    except Exception:
        info = None
    if info is not None:
        if info.period_end:
            values["산출기준일"] = info.period_end.isoformat()
        if info.period_start:
            values["산출 시작일"] = info.period_start.isoformat()
        if info.credit_grade:
            values["회사채 신용등급"] = info.credit_grade

    groups: list[tuple] = []
    try:
        config = read_config(book)
    except Exception:
        config = None
    if config is not None:
        values.setdefault("산출기준일", config.base_date.isoformat())
        values["평균임금 하한 점검액"] = config.wage_check_amount
        groups = [
            (r.source_name or r.mapped_name, r.mapped_name,
             r.severance_nra, r.longterm_nra, r.over_nra_add_age)
            for r in config.job_group_rules
        ]

    values.setdefault("상시근로자 수", actives)
    return values, groups


def relayout_roster(source: str | Path, target: str | Path) -> ExportReport:
    """``source`` 명부를 지금 양식으로 옮겨 ``target`` 에 저장한다.

    ``.xls`` 를 넣어도 ``.xlsx`` 로 나온다 — 옛 서식으로 돌려줄 이유가 없다.
    """
    from . import rostertemplate as tpl
    from .layout import ACTIVE_HEADER_ALIASES, RETIRED_HEADER_ALIASES
    from .readers import ACTIVE_SHEET_ALIASES, RETIRED_SHEET_ALIASES
    from .workbook import find_sheet, open_workbook

    source, target = Path(source), Path(target)
    book = open_workbook(source)
    try:
        active_ws = find_sheet(book, *ACTIVE_SHEET_ALIASES)
        retired_ws = find_sheet(book, *RETIRED_SHEET_ALIASES)
        actives, active_extras, active_report = _read_sheet(
            active_ws, tpl.ACTIVE, ACTIVE_HEADER_ALIASES, "재직자명부")
        retirees, retired_extras, retired_report = _read_sheet(
            retired_ws, tpl.RETIRED, RETIRED_HEADER_ALIASES, "퇴직자명부")

        if not active_report.header_row and not retired_report.header_row:
            raise ValueError(
                "명부에서 머리글 행을 찾지 못했습니다. "
                "재직자명부의 열 이름 줄(보통 3행)이 있는지 확인하세요"
            )

        basics, groups = _basics_of(book, len(actives))
    finally:
        book.close()

    workbook = tpl.build_workbook(
        note="올려받은 명부를 지금 양식으로 옮긴 것입니다. "
             "값은 손대지 않았고 열의 자리만 옮겼습니다.",
        basics=basics,
        groups=groups or None,
        actives=actives,
        retirees=retirees,
        active_extras=active_extras,
        retired_extras=retired_extras,
    )
    workbook.save(target)
    tpl._embed_values(target)
    return ExportReport(active=active_report, retired=retired_report,
                        basics=basics, groups=groups)
