"""웹앱 JSON API 와 가정 state 변환.

브라우저(Pyodide)에서 도는 코드지만 순수 파이썬이므로 PC 에서 그대로 검증한다.
여기서 깨지면 아이패드에서도 깨진다 — 반대로 여기서 통과하면 브라우저 쪽은
표 그리기만 남는다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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
            "kind": "현물", "escalation": "3%", "note": "순금 30돈",
        }
        # 매핑이 있으면 매핑된 조합의 행만 나간다 — 묶음마다 최소 한 행씩 둔다.
        state["mapping"] = [
            ["사원", "정사원", "생산직"], ["촉탁", "촉탁사원", "관리직"],
            ["대표", "임원（주재원）", "임원"],
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
            ["사원", "정사원", "정규직"], ["부장", "정사원", "정규직"],
            ["대표", "임원", "임원"],
        ]
        path = form.write_state(state, tmp_path / "기초율.xlsx")
        ws = openpyxl.load_workbook(path)[form.PAYOUT_SHEET]
        rows = [
            [ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 22).value]
            for r in range(2, ws.max_row + 1)
        ]
        assert rows == [
            ["사원", "정규직", "정사원"], ["부장", "정규직", "정사원"],
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


class TestRosterLibrary:
    """올렸던 명부를 목록에 두고 다음 결산에 그대로 꺼내 쓴다."""

    def test_register_and_run_from_library(self, tmp_path, roster_path) -> None:
        call("library_register", kind="명부", path=str(roster_path), name="1번단체 2412")
        listing = call("library_list")["library"]["명부"]
        assert [e["name"] for e in listing["entries"]] == ["1번단체 2412"]

        stored = call("library_path", kind="명부", name="1번단체 2412")["path"]
        state = form.example_state(["1정규직", "2임원", "3계약직"])
        assumptions = str(tmp_path / "기초율.xlsx")
        call("state_write", state=state, path=assumptions)

        result = call(
            "run", roster=stored, assumptions=assumptions, work=str(tmp_path),
            force=True, sensitivity=False, longterm=False,
        )
        assert result["run"] is True

    def test_old_xls_keeps_its_suffix(self, tmp_path) -> None:
        """.xls 를 .xlsx 로 이름만 바꿔 두면 여는 쪽이 서식을 잘못 짚는다."""
        source = tmp_path / "옛날명부.xls"
        source.write_bytes(b"\xd0\xcf\x11\xe0")  # BIFF 헤더 흉내
        call("library_register", kind="명부", path=str(source), name="옛날명부")
        stored = call("library_path", kind="명부", name="옛날명부")["path"]
        assert stored.endswith(".xls")

    def test_reregistering_replaces_rather_than_duplicates(self, tmp_path, roster_path) -> None:
        call("library_register", kind="명부", path=str(roster_path), name="같은이름")
        old = tmp_path / "같은이름.xls"
        old.write_bytes(b"\xd0\xcf\x11\xe0")
        call("library_register", kind="명부", path=str(old), name="같은이름")
        entries_after = call("library_list")["library"]["명부"]["entries"]
        assert [e["name"] for e in entries_after] == ["같은이름"]
        assert entries_after[0]["suffix"] == ".xls"


class TestBackup:
    """브라우저 저장소는 지워질 수 있다 — 기기 밖으로 내보내고 되살린다."""

    def test_export_and_restore_everything(self, tmp_path, roster_path, monkeypatch) -> None:
        curve = _kis_book(tmp_path / "금리.xlsx")
        call("library_register", kind="금리표", path=str(curve), name="KIS_2025")
        call("library_register", kind="명부", path=str(roster_path), name="1번단체")
        state = form.example_state(["1정규직", "2임원", "3계약직"])
        assumptions = str(tmp_path / "기초율.xlsx")
        call("state_write", state=state, path=assumptions)
        report = call(
            "run", roster=str(roster_path), assumptions=assumptions,
            work=str(tmp_path), force=True, sensitivity=False, longterm=False,
        )
        call("run_save", name="2412 1번단체", roster=str(roster_path),
             assumptions=assumptions, work=str(tmp_path), report=report)

        exported = call("backup_export", work=str(tmp_path))
        archive = Path(exported["path"])
        assert archive.exists() and exported["filename"].startswith("연금계리보관함_")
        assert call("library_list")["backup"] == exported["stamp"]

        # 저장소를 통째로 잃은 기기(=새 PENSION_HOME)에서 되살린다.
        monkeypatch.setenv("PENSION_HOME", str(tmp_path / "새기기"))
        assert call("library_list")["library"]["금리표"]["entries"] == []

        restored = call("backup_import", path=str(archive))
        assert [e["name"] for e in restored["library"]["금리표"]["entries"]] == ["KIS_2025"]
        assert [e["name"] for e in restored["library"]["명부"]["entries"]] == ["1번단체"]
        assert [r["name"] for r in restored["runs"]] == ["2412 1번단체"]
        # 되살린 산출의 입력이 실제로 열려야 한다.
        again = call("run_restore", name="2412 1번단체", work=str(tmp_path / "w"))
        assert Path(again["roster"]).exists()

    def test_merge_keeps_what_the_archive_does_not_have(self, tmp_path, monkeypatch) -> None:
        call("library_register", kind="금리표", path=str(_kis_book(tmp_path / "a.xlsx")),
             name="예전금리표")
        archive = Path(call("backup_export", work=str(tmp_path))["path"])

        monkeypatch.setenv("PENSION_HOME", str(tmp_path / "다른기기"))
        call("library_register", kind="금리표", path=str(_kis_book(tmp_path / "b.xlsx")),
             name="이기기금리표")
        merged = call("backup_import", path=str(archive))
        assert {e["name"] for e in merged["library"]["금리표"]["entries"]} == {
            "예전금리표", "이기기금리표",
        }

        replaced = call("backup_import", path=str(archive), replace=True)
        assert [e["name"] for e in replaced["library"]["금리표"]["entries"]] == ["예전금리표"]

    def test_rejects_a_random_zip(self, tmp_path) -> None:
        import zipfile

        bogus = tmp_path / "아무거나.zip"
        with zipfile.ZipFile(bogus, "w") as archive:
            archive.writestr("hello.txt", "hi")
        assert "보관함 파일이 아닙니다" in call_error("backup_import", path=str(bogus))


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


class TestRunHistory:
    """산출 내역 — 이름 붙여 보관하고 다음 결산 때 끌어온다."""

    def _saved_run(self, tmp_path, roster_path) -> dict:
        state = form.example_state(["1정규직", "2임원", "3계약직"])
        assumptions = str(tmp_path / "기초율.xlsx")
        call("state_write", state=state, path=assumptions)
        report = call(
            "run", roster=str(roster_path), assumptions=assumptions,
            work=str(tmp_path), force=True, sensitivity=False, longterm=False,
        )
        return call(
            "run_save", name="2412 1번단체", roster=str(roster_path),
            assumptions=assumptions, work=str(tmp_path),
            report=report, options={"force": True},
        )

    def test_save_list_restore(self, tmp_path, roster_path) -> None:
        listing = self._saved_run(tmp_path, roster_path)["runs"]
        assert [r["name"] for r in listing] == ["2412 1번단체"]
        assert listing[0]["dbo"]  # 요약에서 DBO 를 뽑아 보여 준다
        assert listing[0]["has_results"] is True

        restored = call("run_restore", name="2412 1번단체", work=str(tmp_path / "w"))
        assert Path(restored["roster"]).exists()
        assert Path(restored["assumptions"]).exists()
        assert restored["meta"]["options"] == {"force": True}

        results = call("run_results", name="2412 1번단체", work=str(tmp_path / "w"))
        assert "산출결과.xlsx" in results["files"]

    def test_same_name_overwrites(self, tmp_path, roster_path) -> None:
        self._saved_run(tmp_path, roster_path)
        listing = self._saved_run(tmp_path, roster_path)["runs"]
        assert len(listing) == 1

    def test_delete(self, tmp_path, roster_path) -> None:
        self._saved_run(tmp_path, roster_path)
        assert call("run_delete", name="2412 1번단체")["runs"] == []
        assert "없습니다" in call_error("run_restore", name="2412 1번단체")

    def test_prior_link_carries_numbers_and_assumptions(self, tmp_path, roster_path) -> None:
        """저장된 산출을 전기로 끌어오면 DBO·할인율이 서식 없이 그대로 와야 한다."""
        self._saved_run(tmp_path, roster_path)
        prior = call("run_prior", name="2412 1번단체")

        assert prior["values"]["dbo"] > 0
        assert 0 < prior["values"]["discount_rate"] < 1  # 4.5% → 0.045
        assert Path(prior["assumptions"]).exists()

        # 그 값으로 당기를 돌리면 증감분석이 붙는다.
        current = call(
            "run", roster=str(roster_path), assumptions=prior["assumptions"],
            work=str(tmp_path / "당기"), force=True, sensitivity=False, longterm=False,
            prior_dbo=prior["values"]["dbo"],
            prior_rate=prior["values"]["discount_rate"],
            prior_service_cost=prior["values"]["service_cost"],
            prior_assumptions=prior["assumptions"],
        )
        assert current["run"] is True
        assert "보험수리적손익" in [row[0] for row in current["summary"]]

    def test_prior_link_falls_back_to_summary_text(self, tmp_path, roster_path) -> None:
        """숫자를 남기지 않던 예전 저장본도 요약 문자열에서 되짚어야 한다."""
        import json as _json

        self._saved_run(tmp_path, roster_path)
        folder = Path(os.environ["PENSION_HOME"]) / "산출내역" / "2412 1번단체"
        meta = _json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        meta["report"].pop("values")
        (folder / "meta.json").write_text(
            _json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

        prior = call("run_prior", name="2412 1번단체")
        assert prior["values"]["dbo"] > 0
        assert prior["values"]["discount_rate"] == pytest.approx(0.045, abs=1e-4)

    def test_name_is_required_and_sanitized(self, tmp_path, roster_path) -> None:
        assert "산출명" in call_error(
            "run_save", name="  ", roster=str(roster_path),
            assumptions=str(roster_path),
        )
        # 경로 문자가 든 이름도 폴더 이름으로 안전해야 한다.
        state = form.example_state()
        assumptions = str(tmp_path / "a.xlsx")
        call("state_write", state=state, path=assumptions)
        saved = call(
            "run_save", name="24/12: 1번*단체", roster=str(roster_path),
            assumptions=assumptions, work=str(tmp_path), report={},
        )
        assert saved["runs"][0]["name"] == "24 12  1번 단체"
