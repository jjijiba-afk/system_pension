"""``1)일반사항`` 6번 항목에서 지급규정 초안 읽기.

실제 케이스 6건의 문구를 그대로 놓고 시험한다. 자유서술이라 기계가 확정할 수
없으므로, **읽은 것과 못 읽은 것이 정확히 갈리는지** 가 핵심이다. 못 읽은 항목을
기본값으로 조용히 메우면 담당자가 확인 없이 넘어간다.
"""

from __future__ import annotations

import datetime as _dt

import openpyxl
import pytest

from pension.actuarial import (
    FRACTION_DOWN,
    SERVICE_ANNUAL,
    SERVICE_DAILY,
    SERVICE_MONTHLY,
)
from pension.general_info import read_general_info
from pension.workbook import open_workbook

# 실제 케이스에서 그대로 가져온 문구
CASES = {
    "1": {
        "eligibility": "전 임직원",
        "service_period": "근속기간 1년 이상 (월할 계산)",
        "formula": "근속일수*(월평균임금/365)*지급률",
        "base_wage": "퇴직전 3개월 임금의 30일평균임금",
        "staff_nra": "만 60세",
        "executive_nra": "없음",
    },
    "2": {
        "eligibility": "입사후 1년 이상",
        "service_period": "입사후 1년 이상",
        "formula": "직전 3개월 평균임금",
        "base_wage": "ROUND(평균임금*근속년월*근속개수1,-1)",
        "staff_nra": "만 60세",
        "executive_nra": "정년 없음",
    },
    "4": {
        "eligibility": "1년 이상 근속한 전직원",
        "service_period": "근로기준법에 따른 산정법 - 일수",
        "formula": "(근속년수*평균임금)+위로금[대상자에한함]",
        "base_wage": "퇴직전 3개월 임금의 30일 평균임금",
        "staff_nra": "만 60세",
        "executive_nra": "없음",
    },
    "5": {
        "eligibility": "임원·정규직사원은 전 직원 대상/계약사원은 대상에서 제외",
        "service_period": "대상기간은 입사일부터 퇴직일까지로 하며 근속 3년 이상이 대상이 된다"
                          "/1년이 되지 않는 단수개월은 절사 예) 3년 10개월인 경우에는 3년",
        "formula": "기초금액 × 근속연수별 지급률 / 근속연수별 지급률은 근속연수의 1/2",
        "base_wage": "기초금액 = 퇴직년도 1개월 임금",
        "staff_nra": "정규직 60세, 계약직 60세",
        "executive_nra": "제약 無",
    },
    "10": {
        "eligibility": "",
        "service_period": "",
        "formula": "",
        "base_wage": "",
        "staff_nra": "정규직 60세",
        "executive_nra": "39세",
    },
}

_ROWS = {
    "eligibility": 110, "service_period": 111, "formula": 112, "base_wage": 113,
    "staff_nra": 114, "executive_nra": 115,
}


def _workbook(values: dict[str, str], tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "1)일반사항"
    for key, row in _ROWS.items():
        ws.cell(row, 5, values.get(key, ""))
    # 명부 시트도 있어야 실제 파일과 비슷하다.
    roster = wb.create_sheet("2)재직자명부")
    roster.cell(20, 2, "작성기준일")
    roster.cell(20, 3, _dt.datetime(2025, 12, 31))
    path = tmp_path / "명부.xlsx"
    wb.save(path)
    return open_workbook(path)


class TestCaseByCase:
    def test_case1_monthly(self, tmp_path) -> None:
        book = _workbook(CASES["1"], tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert draft.min_service_years == 1.0
        assert draft.staff_nra == 60
        assert draft.service_basis == SERVICE_MONTHLY
        assert draft.executive_unlimited is True
        assert draft.executive_nra == 60   # '없음' → 직원 정년과 동일
        assert not draft.unread

    def test_case2_rounding_to_ten_won(self, tmp_path) -> None:
        book = _workbook(CASES["2"], tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert draft.rounding_unit == 10
        assert draft.min_service_years == 1.0
        # 기간 산정방법은 어디에도 안 적혀 있다 — 지어내면 안 된다.
        assert draft.service_basis is None
        assert "근속기간 산정방법" in draft.unread

    def test_case4_statutory_days(self, tmp_path) -> None:
        book = _workbook(CASES["4"], tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert draft.service_basis == SERVICE_DAILY
        assert draft.min_service_years == 1.0

    def test_case5_three_years_and_truncation(self, tmp_path) -> None:
        book = _workbook(CASES["5"], tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert draft.min_service_years == 3.0
        assert draft.service_fraction == FRACTION_DOWN
        # '1년이 되지 않는 단수개월은 절사' 는 연 단위로 센다는 뜻이다.
        assert draft.service_basis == SERVICE_ANNUAL

    def test_case10_executive_age_is_taken_literally(self, tmp_path) -> None:
        book = _workbook(CASES["10"], tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert draft.staff_nra == 60
        assert draft.executive_nra == 39
        assert draft.executive_unlimited is False
        assert "가입자격(최소 근속연수)" in draft.unread

    def test_case3_blank_section_is_reported(self, tmp_path) -> None:
        """규정이 통째로 비어 있으면 기본값으로 메우지 않고 알린다."""
        book = _workbook({}, tmp_path)
        try:
            info = read_general_info(book)
        finally:
            book.close()
        assert info.has_payout_section is False
        assert info.draft.min_service_years is None
        assert info.draft.staff_nra is None
        assert any("비어 있" in item for item in info.draft.unread)


class TestParsing:
    @pytest.mark.parametrize(
        ("wording", "expected"),
        [
            ("없음", True), ("정년 없음", True), ("제약 無", True),
            ("해당없음", True), ("만 60세", False), ("39세", False),
        ],
    )
    def test_unlimited_executive_wordings(self, wording, expected, tmp_path) -> None:
        values = dict(CASES["1"], executive_nra=wording)
        book = _workbook(values, tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert draft.executive_unlimited is expected

    def test_evidence_is_kept_for_review(self, tmp_path) -> None:
        book = _workbook(CASES["5"], tmp_path)
        try:
            draft = read_general_info(book).draft
        finally:
            book.close()
        assert "가입자격" in draft.evidence
        assert "3년" in draft.evidence["가입자격"]

    def test_missing_sheet_is_not_an_error(self, tmp_path) -> None:
        wb = openpyxl.Workbook()
        wb.active.title = "2)재직자명부"
        path = tmp_path / "명부만.xlsx"
        wb.save(path)

        book = open_workbook(path)
        try:
            info = read_general_info(book)
        finally:
            book.close()
        assert info.draft.min_service_years is None
        assert info.draft.unread
