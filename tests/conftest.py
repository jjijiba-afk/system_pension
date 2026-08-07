"""테스트용 워크북 생성기.

원본 통합문서와 같은 시트 구조·행 위치를 가진 명부를 만들어, 실제 파일 없이도
전 과정을 돌려볼 수 있게 한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from pension.assumptions import write_template
from pension.readers import ACTIVE_FIRST_ROW, RETIRED_FIRST_ROW

BASE_DATE = dt.date(2025, 12, 31)

ACTIVE_ROWS = [
    # 사번, 임직원, 직군, 성명, 성별, 생년월일, 입사일, 중간정산일, 평균임금, 제도, 장기대상
    ("A001", "직원", "정규직", "김철수", "남자", "1980-03-15", "2005-04-01", "", 5_000_000, "DB", "Y"),
    ("A002", "직원", "정규직", "이영희", "여자", "1990-11-02", "2015-01-05", "", 4_200_000, "DB", "Y"),
    ("A003", "임원", "임원", "박대표", "남자", "1968-07-20", "1995-03-02", "", 12_000_000, "DB", "N"),
    ("A004", "직원", "계약직", "최기술", "남자", "1995-01-09", "2021-06-14", "", 3_600_000, "퇴직금제도", "N"),
    ("A005", "직원", "정규직", "정미나", "여자", "1985-05-30", "2010-09-01", "2018-01-01", 4_800_000, "DC", "N"),
]

RETIRED_ROWS = [
    # 사번, 임직원, 직군, 성명, 성별, 생년월일, 입사일, 퇴사일, 사유, 제도, 총지급, 사외자산
    ("R001", "직원", "정규직", "한퇴직", "남자", "1975-02-11", "2001-03-01", "2025-06-30", 1, "DB", 92_000_000, 80_000_000),
    ("R002", "직원", "계약직", "서만료", "여자", "1992-12-01", "2022-01-03", "2025-03-31", "계약만료", "퇴직금제도", 11_500_000, 0),
]


def _write_input(ws) -> None:
    ws["A1"] = "■ 명부 vba작업 Input 사항"
    ws["B3"], ws["C3"] = "산출기준일", BASE_DATE
    ws["B5"], ws["C5"] = "평균임금 첵크금액", 1_000_000

    headers = ["명부직군", "변환직군명", "퇴직급여 정년연령", "장기급여 정년연령", "정년연령 초과자 plus 연령"]
    for col, title in enumerate(headers, start=2):
        ws.cell(11, col, title)

    # 규정명은 기초율 워크북의 열 머리글과 일치해야 한다.
    rules = [
        ("임원", "2임원", 65, 65, 2, "정규임원", "정규임원"),
        ("정규직", "1정규직", 60, 60, 2, "정규직", "정규직"),
        ("계약직", "3계약직", 60, 60, 1, "계약직", "계약직"),
    ]
    for offset, (src, mapped, nra, jnra, add, withdrawal, salary) in enumerate(rules):
        row = 12 + offset
        ws.cell(row, 2, src)
        ws.cell(row, 3, mapped)
        ws.cell(row, 4, nra)
        ws.cell(row, 5, jnra)
        ws.cell(row, 6, add)
        # 퇴직급여 지급률(G열)은 비워 두어 법정 퇴직금(배수 = 근속연수)을 쓰게 하고,
        # 장기급여 지급률(H열)만 규정을 지정한다.
        ws.cell(row, 8, withdrawal)   # 장기급여 지급률 규정
        ws.cell(row, 9, withdrawal)   # 퇴직급여 중도퇴직률 규정
        ws.cell(row, 10, salary)      # 퇴직급여 승급률 규정
        ws.cell(row, 11, withdrawal)  # 장기급여 중도퇴직률 규정
        ws.cell(row, 12, salary)      # 장기급여 승급률 규정
        ws.cell(row, 13, withdrawal)  # 퇴직자 퇴직률 규정
        ws.cell(row, 14, withdrawal)


def _write_active(ws) -> None:
    ws.cell(22, 2, "작성기준일")
    ws.cell(22, 3, BASE_DATE)
    ws.cell(23, 3, "사번")
    for offset, values in enumerate(ACTIVE_ROWS):
        row = ACTIVE_FIRST_ROW + offset
        (emp_id, kind, group, name, sex, birth, hire, settle, wage, plan, longterm) = values
        ws.cell(row, 3, emp_id)
        ws.cell(row, 4, kind)
        ws.cell(row, 5, group)
        ws.cell(row, 6, name)
        ws.cell(row, 7, sex)
        ws.cell(row, 8, birth)
        ws.cell(row, 9, hire)
        ws.cell(row, 10, settle)
        ws.cell(row, 11, wage)
        ws.cell(row, 17, plan)
        ws.cell(row, 19, longterm)
        ws.cell(row, 36, "제조원가")


def _write_retired(ws) -> None:
    ws.cell(18, 2, "작성기간")
    ws.cell(19, 3, "사번")
    for offset, values in enumerate(RETIRED_ROWS):
        row = RETIRED_FIRST_ROW + offset
        (emp_id, kind, group, name, sex, birth, hire, exit_date,
         reason, plan, total, fund) = values
        ws.cell(row, 3, emp_id)
        ws.cell(row, 4, kind)
        ws.cell(row, 5, group)
        ws.cell(row, 6, name)
        ws.cell(row, 7, sex)
        ws.cell(row, 8, birth)
        ws.cell(row, 9, hire)
        ws.cell(row, 10, exit_date)
        ws.cell(row, 12, reason)
        ws.cell(row, 13, plan)
        ws.cell(row, 14, total)
        ws.cell(row, 15, fund)
        ws.cell(row, 20, "N")
        ws.cell(row, 24, "판관비")


@pytest.fixture
def roster_path(tmp_path: Path) -> Path:
    """원본과 같은 시트 구조를 가진 명부 워크북."""
    wb = openpyxl.Workbook()
    del wb["Sheet"]
    _write_input(wb.create_sheet("Input"))
    _write_active(wb.create_sheet("재직자명부"))
    _write_retired(wb.create_sheet("퇴직자명부"))

    path = tmp_path / "명부.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def assumptions_path(tmp_path: Path) -> Path:
    """테스트용 기초율 워크북(양식 생성기가 만든 예시 값 사용)."""
    path = tmp_path / "기초율.xlsx"
    write_template(path, job_groups=["정규직", "정규임원", "계약직"])
    return path
