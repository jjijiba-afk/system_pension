"""웹앱 JSON API 와 가정 state 변환.

브라우저(Pyodide)에서 도는 코드지만 순수 파이썬이므로 PC 에서 그대로 검증한다.
여기서 깨지면 아이패드에서도 깨진다 — 반대로 여기서 통과하면 브라우저 쪽은
표 그리기만 남는다.
"""

from __future__ import annotations

import json

import openpyxl
import pytest

from pension import assumption_form as form
from pension.assumptions import DISCOUNT_SHEET, FORMULA, load_assumptions
from pension.webui import api


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """등록 폴더를 시험용으로 갈아 끼운다. 사용자 폴더를 건드리면 안 된다."""
    monkeypatch.setenv("PENSION_HOME", str(tmp_path / "home"))
    return tmp_path


def call(op: str, **kwargs):
    """api() 를 부르고 성공 응답을 dict 로 돌려받는다."""
    response = json.loads(api(json.dumps({"op": op, **kwargs}, ensure_ascii=False)))
    assert response["ok"], response.get("error")
    return response


def call_error(op: str, **kwargs) -> str:
    response = json.loads(api(json.dumps({"op": op, **kwargs}, ensure_ascii=False)))
    assert not response["ok"]
    return response["error"]


def _kis_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "KIS_NET금리"
    ws.append(["No", "기준일자", "구분", "등급", "3월", "1년", "5년", "20년"])
    ws.append([1, None, "공모 무보증회사채", "AA0", 3.01, 3.122, 3.62, 5.26])
    ws.append([2, None, "공모 무보증회사채", "AA-", 3.11, 3.23, 3.72, 5.36])
    wb.save(path)
    return path


# ── 가정 state ───────────────────────────────────────────────────

class TestAssumptionForm:
    def test_round_trip_preserves_everything(self, tmp_path) -> None:
        """state → 워크북 → state 왕복에서 입력이 그대로 남아야 한다."""
        state = form.example_state(["생산직", "관리직", "임원"])
        state["payout"]["임원"]["withdrawal"] = "미반영"
        state["payout"]["임원"]["excluded"] = True
        state["benefit_rules"]["관리직"] = {"mode": "수식", "formula": "=MIN(t, 30)"}
        state["longterm_rules"]["생산직"] = {
            "kind": "현물", "escalation": "3%", "note": "현물 포상",
        }
        # 매핑이 있으면 매핑된 조합의 행만 나간다 — 묶음마다 최소 한 행씩 둔다.
        state["mapping"] = [
            ["사원", "정규사원", "생산직"], ["촉탁", "촉탁사원", "관리직"],
            ["대표", "임원(별정)", "임원"],
        ]

        path = form.write_state(state, tmp_path / "기초율.xlsx")
        back = form.read_state(path)

        assert back["job_groups"] == ["생산직", "관리직", "임원"]
        assert back["grids"][DISCOUNT_SHEET]["rows"] == [["1", "0.045"]]
        assert back["payout"]["임원"]["withdrawal"] == "미반영"
        assert back["payout"]["임원"]["excluded"] is True
        assert back["benefit_rules"]["관리직"]["mode"] == "수식"
        # 워크북에는 엑셀이 수식으로 오해하지 않게 '=' 없이 저장된다.
        assert back["benefit_rules"]["관리직"]["formula"] == "MIN(t, 30)"
        assert back["longterm_rules"]["생산직"]["kind"] == "현물"
        assert back["longterm_rules"]["생산직"]["escalation"] == "3%"
        assert back["mapping"] == state["mapping"]

    def test_written_file_feeds_the_engine(self, tmp_path) -> None:
        """만든 워크북이 산출 엔진의 로더·규정 리더로 그대로 읽혀야 한다."""
        from pension.pipeline import _read_payout_rules

        state = form.example_state()
        state["payout"]["임원"]["mortality"] = "미반영"
        path = form.write_state(state, tmp_path / "기초율.xlsx")

        loaded = load_assumptions(path)
        assert loaded.discount.flat == pytest.approx(0.045)
        assert loaded.withdrawal.curve("정규직").rate(20) == pytest.approx(0.15)

        rules = {r.mapped_name: r for r in _read_payout_rules(path)}
        assert rules["임원"].apply_mortality is False
        assert rules["정규직"].apply_mortality is True

    def test_percent_and_comma_parsing(self) -> None:
        state = form.empty_state(["정규직"])
        state["grids"][DISCOUNT_SHEET]["rows"] = [["1", "4.5%"]]
        state["grids"]["지급률"]["rows"] = [["10", "1,000"]]
        sheets, _rules, _lt = form.state_to_sheets(state)
        assert sheets[DISCOUNT_SHEET][1] == [[1.0, 0.045]]
        assert sheets["지급률"][1] == [[10.0, 1000.0]]

    def test_problems_block_saving(self, tmp_path) -> None:
        state = form.empty_state()
        problems = form.state_problems(state)
        assert any("할인율" in p for p in problems)

        state["grids"][DISCOUNT_SHEET]["rows"] = [["1", "4.5%"]]
        state["benefit_rules"]["정규직"] = {"mode": FORMULA, "formula": ""}
        problems = form.state_problems(state)
        assert any("수식이 비어" in p for p in problems)
        with pytest.raises(ValueError):
            form.write_state(state, tmp_path / "기초율.xlsx")

    def test_mapping_rows_copy_target_rules(self, tmp_path) -> None:
        """매핑이 있으면 명부 조합마다 한 행씩, 규정 값은 묶음 것을 복사한다."""
        state = form.example_state(["정규직", "임원"])
        state["mapping"] = [
            ["사원", "정규사원", "정규직"], ["부장", "정규사원", "정규직"],
            ["대표", "임원", "임원"],
        ]
        path = form.write_state(state, tmp_path / "기초율.xlsx")
        ws = openpyxl.load_workbook(path)[form.PAYOUT_SHEET]
        rows = [
            [ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 22).value]
            for r in range(2, ws.max_row + 1)
        ]
        assert rows == [
            ["사원", "정규직", "정규사원"], ["부장", "정규직", "정규사원"],
            ["대표", "임원", "임원"],
        ]

    def test_note_lines_are_skipped_on_read(self, tmp_path) -> None:
        """양식 파일이 표 밑에 적는 설명 줄은 값으로 읽히면 안 된다."""
        state = form.example_state()
        path = form.write_state(state, tmp_path / "기초율.xlsx")
        wb = openpyxl.load_workbook(path)
        ws = wb[DISCOUNT_SHEET]
        ws.cell(ws.max_row + 2, 1, "· 결산일마다 새로 받아 바꾸세요.")
        wb.save(path)

        back = form.read_state(path)
        assert back["grids"][DISCOUNT_SHEET]["rows"] == [["1", "0.045"]]


# ── api() 경계 ───────────────────────────────────────────────────

class TestApi:
    def test_unknown_op(self) -> None:
        assert "모르는 요청" in call_error("없는것")

    def test_meta_lists_form_layout(self) -> None:
        meta = call("meta")
        assert [s["tab"] for s in meta["sheets"]] == [
            "할인율", "Base-up", "승급률", "퇴직률", "사망률", "지급률", "장기급여",
        ]
        assert meta["apply_choices"] == ["반영", "미반영"]
        assert "AA0" in meta["grades"]

    def test_state_write_reports_problems_instead_of_raising(self, tmp_path) -> None:
        state = form.empty_state()
        result = call("state_write", state=state, path=str(tmp_path / "a.xlsx"))
        assert result["written"] is False
        assert result["problems"]

    def test_state_write_and_read(self, tmp_path) -> None:
        state = form.example_state()
        path = str(tmp_path / "기초율.xlsx")
        assert call("state_write", state=state, path=path)["written"] is True
        back = call("state_read", path=path)["state"]
        assert back["job_groups"] == ["정규직", "계약직", "임원"]

    def test_standard_state_has_full_table(self) -> None:
        """내장 표준률 — 15~70세 표가 직군 열로 펼쳐져야 한다."""
        state = call("standard_state", groups=["생산직", "임원"])["state"]
        assert state["job_groups"] == ["생산직", "임원"]
        mortality = state["grids"]["사망률"]["rows"]
        assert mortality[0][0] == "15" and mortality[-1][0] == "70"
        withdrawal = state["grids"]["퇴직률"]["rows"]
        assert len(withdrawal[0]) == 3  # 연령 + 직군 2
        assert state["grids"][DISCOUNT_SHEET]["rows"]  # 기본 할인율 한 줄

    def test_formula_ops(self) -> None:
        assert call("formula_check", source="=MIN(t, 30)")["error"] == ""
        assert call("formula_check", source="=WRONG(")["error"]
        preview = call("formula_preview", formulas={"임원": "=t*2"})["preview"]
        assert preview["columns"] == ["근속", "임원"]
        assert preview["rows"][0] == ["1", "2.000"]


class TestLibraryOps:
    def test_register_list_pin_remove(self, tmp_path) -> None:
        source = _kis_book(tmp_path / "금리.xlsx")
        result = call(
            "library_register", kind="금리표", path=str(source), name="KIS_2025",
        )
        assert result["registered"] == "KIS_2025"
        assert result["library"]["금리표"]["default"] == "KIS_2025"

        listing = call("library_list")["library"]
        assert [e["name"] for e in listing["금리표"]["entries"]] == ["KIS_2025"]

        call("library_pin", kind="금리표", name="KIS_2025")
        assert call("library_list")["library"]["금리표"]["pinned"] == "KIS_2025"

        call("library_remove", kind="금리표", name="KIS_2025")
        after = call("library_list")["library"]["금리표"]
        assert after["entries"] == [] and after["pinned"] == ""

    def test_curve_rows_from_registered(self, tmp_path) -> None:
        source = _kis_book(tmp_path / "금리.xlsx")
        call("library_register", kind="금리표", path=str(source), name="KIS_2025")

        grades = call("curve_grades")["grades"]
        assert any("AA0" in g for g in grades)

        curve = call("curve_rows", grade="AA0")
        assert curve["rows"][0] == ["0.25", "3.0100%"]  # 3월 만기가 살아 있어야 한다
        assert curve["rows"][-1] == ["20", "5.2600%"]

    def test_rates_state_roundtrip(self, tmp_path) -> None:
        """등록한 표준률 워크북을 편집 state 로 불러온다."""
        state = form.example_state(["정규직", "임원"])
        path = form.write_state(state, tmp_path / "표준.xlsx")
        call("library_register", kind="표준률", path=str(path), name="회사표준")

        back = call("rates_state")["state"]
        assert back["job_groups"] == ["정규직", "임원"]

    def test_missing_registration_is_a_clear_error(self) -> None:
        assert "등록" in call_error("curve_rows", grade="AA0")


class TestRosterOps:
    def test_scan_and_groups(self, roster_path) -> None:
        found = call("roster_scan", path=str(roster_path), groups=[])["found"]
        assert found, "명부에서 직군 조합을 찾아야 한다"
        pairs = {(f["source"], f["kind"]) for f in found}
        assert ("정규직", "직원") in pairs
        executive = next(f for f in found if f["kind"] == "임원")
        assert executive["suggest"] == "임원"

        groups = call("roster_groups", path=str(roster_path))["groups"]
        assert groups == ["2임원", "1정규직", "3계약직"]


class TestRunOp:
    def test_full_run(self, tmp_path, roster_path) -> None:
        """편집 state 로 만든 기초율 + 명부 → 산출까지 한 바퀴."""
        state = form.example_state(["1정규직", "2임원", "3계약직"])
        assumptions = str(tmp_path / "기초율.xlsx")
        assert call("state_write", state=state, path=assumptions)["written"] is True

        result = call(
            "run", roster=str(roster_path), assumptions=assumptions,
            work=str(tmp_path), force=True, sensitivity=False, longterm=False,
        )
        assert result["run"] is True
        labels = [row[0] for row in result["summary"]]
        assert "확정급여채무 (DBO)" in labels
        assert (tmp_path / "산출결과.xlsx").exists()
        assert (tmp_path / "개인별결과.xlsx").exists()
