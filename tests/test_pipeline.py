"""명부 읽기 → 검증 → 산출 → 리포트까지의 전 과정."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest
from conftest import ASSET_CLOSING, write_general_sheet

from pension.config import read_config
from pension.errors import IssueLog, PensionDataError
from pension.normalize import BenefitPlan, EmployeeType, Gender, RetirementReason
from pension.pipeline import PriorPeriod, RunOptions, load_inputs, run_valuation
from pension.readers import ACTIVE_FIRST_ROW, read_roster
from pension.report import write_report
from pension.upload import ACTIVE_UPLOAD_HEADERS, RETIRED_UPLOAD_HEADERS, build_upload
from pension.validation import validate_roster


def read_all(path: Path):
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        config = read_config(wb)
        log = IssueLog()
        roster = read_roster(wb, config, log)
    finally:
        wb.close()
    validate_roster(roster, config, log)
    return config, roster, log


class TestConfig:
    def test_reads_the_base_date_and_rules(self, roster_path: Path) -> None:
        config, _, _ = read_all(roster_path)
        assert config.base_date == dt.date(2025, 12, 31)
        assert config.wage_check_amount == 1_000_000
        assert len(config.job_group_rules) == 3

    def test_maps_job_group_names(self, roster_path: Path) -> None:
        config, _, _ = read_all(roster_path)
        found = config.find_job_group("정규직")
        assert found is not None
        assert found[1].mapped_name == "1정규직"
        assert found[1].severance_nra == 60

    def test_unknown_job_group_is_not_found(self, roster_path: Path) -> None:
        config, _, _ = read_all(roster_path)
        assert config.find_job_group("파견직") is None


class TestRosterReading:
    def test_reads_every_member(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        assert len(roster.active) == 5
        assert len(roster.retired) == 2

    def test_normalizes_codes_and_dates(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        first = roster.active[0]
        assert first.employee_id == "A001"
        assert first.name == "김철수"
        assert first.gender is Gender.MALE
        assert first.birth_date == dt.date(1980, 3, 15)
        assert first.hire_date == dt.date(2005, 4, 1)
        assert first.plan is BenefitPlan.DB
        assert first.job_group == "1정규직"
        assert first.row == ACTIVE_FIRST_ROW

    def test_executive_flag(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        executive = next(m for m in roster.active if m.employee_id == "A003")
        assert executive.employee_type is EmployeeType.EXECUTIVE

    def test_blank_settlement_date_falls_back_to_hire_date(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        first = roster.active[0]
        assert first.settlement_date == first.hire_date

    def test_explicit_settlement_date_is_kept(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        settled = next(m for m in roster.active if m.employee_id == "A005")
        assert settled.settlement_date == dt.date(2018, 1, 1)

    def test_contract_expiry_maps_to_voluntary_exit(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        retiree = next(m for m in roster.retired if m.employee_id == "R002")
        assert retiree.reason is RetirementReason.VOLUNTARY


class TestValidationOnCleanData:
    def test_clean_roster_has_no_errors(self, roster_path: Path) -> None:
        _, _, log = read_all(roster_path)
        assert log.errors == [], "\n".join(str(i) for i in log.errors)

    def test_ages_and_retirement_ages_are_filled_in(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        first = roster.active[0]
        assert first.age == 45           # 1980-03-15 → 2025-12-31
        assert first.severance_nra == 60
        assert first.longterm_nra == 60

    def test_member_past_normal_retirement_age_gets_the_add_on(self, roster_path: Path) -> None:
        _, roster, _ = read_all(roster_path)
        executive = next(m for m in roster.active if m.employee_id == "A003")
        assert executive.age == 57
        assert executive.severance_nra == 65


class TestValidationCatchesBadData:
    def _corrupt(self, roster_path: Path, row: int, col: int, value: object) -> Path:
        wb = openpyxl.load_workbook(roster_path)
        wb["재직자명부"].cell(row, col, value)
        wb.save(roster_path)
        return roster_path

    def test_duplicate_employee_id(self, roster_path: Path) -> None:
        self._corrupt(roster_path, ACTIVE_FIRST_ROW + 1, 3, "A001")
        _, _, log = read_all(roster_path)
        assert any(i.code == "JAE_DUP_ID" for i in log.errors)

    def test_unknown_job_group(self, roster_path: Path) -> None:
        self._corrupt(roster_path, ACTIVE_FIRST_ROW, 5, "파견직")
        _, _, log = read_all(roster_path)
        issue = next(i for i in log.errors if i.code == "JAE_JOB_GROUP_UNKNOWN")
        assert "파견직" in issue.message

    def test_hire_date_after_the_base_date(self, roster_path: Path) -> None:
        self._corrupt(roster_path, ACTIVE_FIRST_ROW, 9, "2026-03-01")
        _, _, log = read_all(roster_path)
        assert any(i.code == "JAE_HIRE_AFTER_BASE" for i in log.errors)

    def test_wage_below_the_check_amount(self, roster_path: Path) -> None:
        self._corrupt(roster_path, ACTIVE_FIRST_ROW, 11, 500_000)
        _, _, log = read_all(roster_path)
        assert any(i.code == "JAE_WAGE_BELOW_CHECK" for i in log.errors)

    def test_missing_benefit_plan(self, roster_path: Path) -> None:
        self._corrupt(roster_path, ACTIVE_FIRST_ROW, 17, "")
        _, _, log = read_all(roster_path)
        assert any(i.code == "JAE_PLAN_MISSING" for i in log.errors)

    def test_unparseable_birth_date(self, roster_path: Path) -> None:
        self._corrupt(roster_path, ACTIVE_FIRST_ROW, 8, "생년월일없음")
        _, _, log = read_all(roster_path)
        assert any(i.code == "JAE_BIRTH_DATE" for i in log.errors)

    def test_issues_point_at_the_exact_cell(self, roster_path: Path) -> None:
        """이슈는 시트·행·열까지 짚어 준다. 'N 번째 사람' 만으로는 못 찾는다."""
        self._corrupt(roster_path, ACTIVE_FIRST_ROW + 2, 11, 100)
        _, _, log = read_all(roster_path)
        issue = next(i for i in log.errors if i.code == "JAE_WAGE_BELOW_CHECK")
        assert issue.sheet == "재직자명부"
        assert issue.row == ACTIVE_FIRST_ROW + 2
        assert issue.column == "K"
        assert issue.seq == 3

    def test_all_errors_are_collected_not_just_the_first(self, roster_path: Path) -> None:
        """첫 오류에서 멈추지 않고 전부 모은다. 명부를 한 번에 손보기 위해서다."""
        wb = openpyxl.load_workbook(roster_path)
        ws = wb["재직자명부"]
        ws.cell(ACTIVE_FIRST_ROW, 17, "")           # 제도 누락
        ws.cell(ACTIVE_FIRST_ROW + 1, 5, "파견직")   # 직군 미매칭
        ws.cell(ACTIVE_FIRST_ROW + 2, 11, 100)      # 임금 미달
        wb.save(roster_path)

        _, _, log = read_all(roster_path)
        codes = {i.code for i in log.errors}
        assert {"JAE_PLAN_MISSING", "JAE_JOB_GROUP_UNKNOWN", "JAE_WAGE_BELOW_CHECK"} <= codes


class TestUpload:
    def test_every_row_matches_its_headers(self, roster_path: Path) -> None:
        """줄 길이가 머리글과 어긋나면 그 뒤 열이 통째로 한 칸씩 밀린다.

        갯수를 숫자로 못박지 않는다 — 열을 하나 더하거나 뺄 때 시험이 먼저
        틀려 버리면, 정작 봐야 할 '머리글과 줄이 맞는가' 를 못 본다.
        """
        config, roster, _ = read_all(roster_path)
        active, retired = build_upload(roster, config)
        assert active and retired
        assert all(len(row) == len(ACTIVE_UPLOAD_HEADERS) for row in active)
        assert all(len(row) == len(RETIRED_UPLOAD_HEADERS) for row in retired)
        # 머리글에 같은 이름이 두 번 들어가면 어느 열인지 가릴 수 없다.
        assert len(set(ACTIVE_UPLOAD_HEADERS)) == len(ACTIVE_UPLOAD_HEADERS)
        assert len(set(RETIRED_UPLOAD_HEADERS)) == len(RETIRED_UPLOAD_HEADERS)

    def test_daily_base_pay_defaults_to_a_thirtieth_of_the_wage(self, roster_path: Path) -> None:
        config, roster, _ = read_all(roster_path)
        active, _ = build_upload(roster, config)
        assert active[0][12] == round(5_000_000 / 30)

    def test_rows_are_renumbered_sequentially(self, roster_path: Path) -> None:
        config, roster, _ = read_all(roster_path)
        active, _ = build_upload(roster, config)
        assert [row[0] for row in active] == list(range(1, len(active) + 1))


class TestFullRun:
    def _options(self, roster_path: Path, assumptions_path: Path, tmp_path: Path,
                 **kw) -> RunOptions:
        return RunOptions(
            roster_path=roster_path,
            assumptions_path=assumptions_path,
            output_path=tmp_path / "산출결과.xlsx",
            **kw,
        )

    def test_produces_a_positive_obligation(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run = run_valuation(self._options(roster_path, assumptions_path, tmp_path))
        assert run.valuation.dbo > 0
        assert run.valuation.service_cost > 0
        assert run.valuation.headcount == 4  # A005 는 DC 라 제외

    def test_dc_member_is_excluded_with_a_reason(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run = run_valuation(self._options(roster_path, assumptions_path, tmp_path))
        dc = next(m for m in run.valuation.members if m.employee_id == "A005")
        assert dc.dbo == 0
        assert "DC" in dc.excluded_reason

    def test_sensitivity_and_longterm_are_included(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run = run_valuation(self._options(roster_path, assumptions_path, tmp_path))
        assert run.sensitivity is not None
        assert len(run.sensitivity.cases) == 8
        assert run.longterm is not None

    def test_benefits_paid_come_from_the_retired_roster(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run = run_valuation(self._options(roster_path, assumptions_path, tmp_path))
        assert run.benefits_paid == pytest.approx(92_000_000 + 11_500_000)

    def test_rollforward_reconciles_to_the_closing_obligation(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        options = self._options(
            roster_path, assumptions_path, tmp_path,
            prior=PriorPeriod(dbo=500_000_000, service_cost=40_000_000, discount_rate=0.048),
        )
        run = run_valuation(options)
        assert run.rollforward is not None
        assert run.rollforward.closing_dbo == pytest.approx(run.valuation.dbo)

    def test_errors_stop_the_run_by_default(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        wb = openpyxl.load_workbook(roster_path)
        wb["재직자명부"].cell(ACTIVE_FIRST_ROW, 17, "")
        wb.save(roster_path)

        with pytest.raises(PensionDataError) as info:
            run_valuation(self._options(roster_path, assumptions_path, tmp_path))
        assert info.value.issues

    def test_force_flag_runs_despite_errors(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        wb = openpyxl.load_workbook(roster_path)
        wb["재직자명부"].cell(ACTIVE_FIRST_ROW, 17, "")
        wb.save(roster_path)

        run = run_valuation(
            self._options(roster_path, assumptions_path, tmp_path, allow_errors=True)
        )
        assert run.issues.has_errors()
        assert run.valuation.dbo > 0

    def test_progress_callback_reaches_completion(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        seen: list[float] = []
        run_valuation(
            self._options(roster_path, assumptions_path, tmp_path),
            lambda _m, f: seen.append(f),
        )
        assert seen == sorted(seen)
        assert seen[-1] == 1.0


class TestReport:
    def test_writes_every_expected_sheet(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run = run_valuation(RunOptions(
            roster_path=roster_path,
            assumptions_path=assumptions_path,
            output_path=tmp_path / "결과.xlsx",
        ))
        path = write_report(run, tmp_path / "결과.xlsx")

        wb = openpyxl.load_workbook(path)
        assert set(wb.sheetnames) == {
            "요약", "채무 증감", "민감도", "현금흐름", "적용가정", "이 파일 보는 법", "개인별 결과",
            "장기급여 개인별", "검증", "재직자명부", "퇴직자명부",
        }

    def test_member_sheet_has_one_row_per_person(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run = run_valuation(RunOptions(
            roster_path=roster_path,
            assumptions_path=assumptions_path,
            output_path=tmp_path / "결과.xlsx",
        ))
        path = write_report(run, tmp_path / "결과.xlsx")
        wb = openpyxl.load_workbook(path)
        assert wb["개인별 결과"].max_row == len(run.valuation.members) + 1


def test_load_inputs_returns_all_four_pieces(
    roster_path: Path, assumptions_path: Path
) -> None:
    config, roster, assumptions, log, _g = load_inputs(roster_path, assumptions_path)
    assert config.base_date == dt.date(2025, 12, 31)
    assert roster.active and roster.retired
    assert assumptions.discount.level_rate > 0
    assert not log.has_errors()


class TestGeneralSheetIsUsed:
    """``일반사항`` 에 이미 적혀 온 것은 다시 입력받지 않는다."""

    @staticmethod
    def _with_general(roster_path: Path, **kwargs) -> Path:
        wb = openpyxl.load_workbook(roster_path)
        write_general_sheet(wb.create_sheet("일반사항"), **kwargs)
        path = roster_path.with_name("일반사항포함.xlsx")
        wb.save(path)
        return path

    def test_period_end_becomes_the_base_date(
        self, roster_path: Path, assumptions_path: Path
    ) -> None:
        """2번 '기말' 이 곧 산출기준일이다 — 화면에 다시 적을 이유가 없다."""
        path = self._with_general(
            roster_path, period=(dt.date(2026, 1, 1), dt.date(2026, 3, 31))
        )
        config, _, _, _, general = load_inputs(path, assumptions_path)
        assert config.base_date == dt.date(2026, 3, 31)
        assert general.period_start == dt.date(2026, 1, 1)

    def test_an_explicit_base_date_still_wins(
        self, roster_path: Path, assumptions_path: Path
    ) -> None:
        path = self._with_general(
            roster_path, period=(dt.date(2026, 1, 1), dt.date(2026, 3, 31))
        )
        config, _, _, _, _ = load_inputs(
            path, assumptions_path, dt.date(2025, 12, 31)
        )
        assert config.base_date == dt.date(2025, 12, 31)

    def test_asset_table_flows_into_the_plan_asset_statement(
        self, roster_path: Path, assumptions_path: Path
    ) -> None:
        """담당자가 신탁 명세서를 보고 채운 표를 그대로 쓴다."""
        path = self._with_general(roster_path)
        run = run_valuation(
            RunOptions(
                roster_path=path,
                assumptions_path=assumptions_path,
                output_path=path.with_name("결과.xlsx"),
                allow_errors=True,
            )
        )
        assert run.plan_assets is not None
        assert run.plan_assets.opening_fair_value == pytest.approx(12_044_373_875.0)
        assert run.plan_assets.closing_fair_value == pytest.approx(12_993_972_710.0)
        assert run.plan_assets.contributions == pytest.approx(1_710_353_902.0)

    def test_unbalanced_asset_table_is_reported_not_swallowed(
        self, roster_path: Path, assumptions_path: Path
    ) -> None:
        """회사 표가 스스로 안 맞으면 재측정손익이 그만큼 틀어진다."""
        path = self._with_general(roster_path, closing=ASSET_CLOSING + 9_103_134.0)
        _, _, _, log, _ = load_inputs(path, assumptions_path)
        codes = [issue.code for issue in log.warnings]
        assert "GEN_ASSET_NOT_BALANCED" in codes

    def test_balanced_table_raises_no_warning(
        self, roster_path: Path, assumptions_path: Path
    ) -> None:
        path = self._with_general(roster_path)
        _, _, _, log, _ = load_inputs(path, assumptions_path)
        assert "GEN_ASSET_NOT_BALANCED" not in [i.code for i in log.warnings]

    def test_roster_without_the_sheet_is_still_fine(
        self, roster_path: Path, assumptions_path: Path
    ) -> None:
        """업로드용으로 변환한 명부에는 일반사항이 없다 — 막으면 안 된다."""
        _, _, _, log, general = load_inputs(roster_path, assumptions_path)
        assert general is not None
        assert general.assets.is_empty()
        assert not log.has_errors()


class TestAssumptionTemplateColumns:
    """기초율 양식의 열 머리글은 명부가 참조하는 '규정명' 이어야 한다.

    직군명(예: ``1정규직``)을 쓰면 산출 때 규정을 못 찾아 조용히 0 이 나온다.
    """

    def test_referenced_rule_names_come_from_the_input_sheet(self, roster_path: Path) -> None:
        config, _, _ = read_all(roster_path)
        assert config.referenced_rule_names() == ["정규임원", "정규직", "계약직"]

    def test_falls_back_to_job_group_names_when_no_rules_are_set(
        self, roster_path: Path
    ) -> None:
        wb = openpyxl.load_workbook(roster_path)
        ws = wb["Input"]
        for row in range(12, 15):
            for col in range(7, 15):
                # cell(row, col, None) 은 openpyxl 이 무시하므로 값을 직접 지운다.
                ws.cell(row, col).value = None
        wb.save(roster_path)

        config, _, _ = read_all(roster_path)
        assert config.referenced_rule_names() == ["2임원", "1정규직", "3계약직"]


class TestReportAuditTrail:
    """결과 파일 혼자서 근거자료가 되어야 한다.

    결과는 담당자 → 회사 → 감사인 사이를 메일로 돌아다니는 동안 기초율 파일과
    분리되기 마련이다. 현금흐름과 적용 가정이 결과 안에 있어야 "이 숫자가 어떻게
    나왔나" 에 답할 수 있다.
    """

    def _report(self, roster_path, assumptions_path, tmp_path):
        run = run_valuation(RunOptions(
            roster_path=roster_path,
            assumptions_path=assumptions_path,
            output_path=tmp_path / "결과.xlsx",
        ))
        return run, write_report(run, tmp_path / "결과.xlsx")

    def test_cashflow_sheet_reconciles_to_the_dbo(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        """시트의 두 열(지급액·현가계수)만으로 채무가 재계산되어야 한다."""
        run, path = self._report(roster_path, assumptions_path, tmp_path)

        ws = openpyxl.load_workbook(path, data_only=True)["현금흐름"]
        rows = [
            (ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 3).value)
            for r in range(6, ws.max_row + 1)
            if isinstance(ws.cell(r, 1).value, (int, float))
        ]
        assert rows, "현금흐름 행이 없다"
        rebuilt = sum(amount * factor for _t, amount, factor in rows)
        assert rebuilt == pytest.approx(run.valuation.dbo, rel=1e-9)

    def test_cashflow_sheet_verifies_the_single_rate(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        run, path = self._report(roster_path, assumptions_path, tmp_path)

        ws = openpyxl.load_workbook(path, data_only=True)["현금흐름"]
        rate = run.valuation.single_discount_rate()
        rows = [
            (ws.cell(r, 1).value, ws.cell(r, 2).value)
            for r in range(6, ws.max_row + 1)
            if isinstance(ws.cell(r, 1).value, (int, float))
        ]
        restated = sum(amount / (1 + rate) ** t for t, amount in rows)
        assert restated == pytest.approx(run.valuation.dbo, rel=1e-6)

    def test_assumption_sheet_snapshots_what_was_used(
        self, roster_path: Path, assumptions_path: Path, tmp_path: Path
    ) -> None:
        _run, path = self._report(roster_path, assumptions_path, tmp_path)

        ws = openpyxl.load_workbook(path, data_only=True)["적용가정"]
        cells = {
            str(ws.cell(r, c).value)
            for r in range(1, ws.max_row + 1)
            for c in range(2, 16)
            if ws.cell(r, c).value is not None
        }
        # 구역 제목들과 직군 규칙 열이 있어야 한다.
        for expected in ("할인율", "퇴직률 (기준: 연령)", "사망률 qx", "직군별 규정",
                         "명부직군", "Base-up"):
            assert expected in cells, f"'{expected}' 이(가) 적용가정 시트에 없다"
        # 직군 규칙의 적용 여부가 값으로 남아야 한다.
        assert "반영" in cells


class TestSilentZeroesAreCaught:
    """조용히 0 이 되는 길목을 막는다.

    수식이 있는데 계산된 값이 없는 칸은 **빈 칸과 똑같이 보인다.** 숫자 칸이면
    0 이 되고, 추계액이 0 이 되면 `추계액대비` 검산이 소리 없이 꺼진다.
    """

    def test_an_uncalculated_formula_is_reported(self, tmp_path: Path) -> None:
        import openpyxl

        from pension.workbook import uncalculated_formulas

        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "재직자명부"
        sheet["A1"] = 1
        sheet["B1"] = "=A1*2"          # 엑셀이 계산한 적 없는 수식
        sheet["C1"] = "글자"
        path = tmp_path / "계산안된.xlsx"
        book.save(path)

        cells, total = uncalculated_formulas(path)
        assert total == 1
        assert cells == ["재직자명부!B1"]

    def test_a_saved_value_is_not_reported(self, tmp_path: Path) -> None:
        """엑셀이 값을 함께 저장해 둔 수식은 문제가 아니다."""
        import openpyxl

        from pension.workbook import uncalculated_formulas

        book = openpyxl.Workbook()
        book.active["A1"] = 3          # 수식이 아예 없는 파일
        path = tmp_path / "값만.xlsx"
        book.save(path)

        assert uncalculated_formulas(path) == ([], 0)

    def test_xls_is_skipped_rather_than_guessed(self, tmp_path: Path) -> None:
        """`.xls` 는 수식을 꺼내 볼 수 없다. 없는 것을 있다고 하지 않는다."""
        from pension.workbook import uncalculated_formulas

        path = tmp_path / "옛서식.xls"
        path.write_bytes(b"not really an xls")
        assert uncalculated_formulas(path) == ([], 0)


class TestSavedRunsCarryTheirGeneration:
    """저장본과 지금 프로그램이 같은 판인지 말할 수 있어야 한다.

    전기 대비 검증이 저장본을 쓴다. 그 사이에 명부 서식이 바뀌었으면 차이가
    자료 때문인지 프로그램 때문인지 가릴 수 없는데, 표시가 없으면 물어볼
    방법조차 없다.
    """

    def test_a_fresh_save_matches(self) -> None:
        from pension.runs import SCHEMA, schema_gap

        assert schema_gap({"schema": SCHEMA}) == ""

    def test_an_older_generation_is_called_out(self) -> None:
        from pension.runs import SCHEMA, schema_gap

        note = schema_gap({"schema": SCHEMA - 1})
        assert str(SCHEMA) in note and "가릴 수 없" in note

    def test_a_save_from_before_stamping_is_called_out(self) -> None:
        note = __import__("pension.runs", fromlist=["x"]).schema_gap({"name": "옛것"})
        assert "세대 표시가 없" in note


class TestGenderIsNotGuessedSilently:
    """성별을 모르면 모른다고 해야 한다.

    사망률이 성별로 갈린다. 못 정한 채 남자로 두면 여성이 남성 사망률을 받는데,
    아무 표시가 없으면 담당자는 그런 일이 있었다는 것조차 모른다.
    """

    def test_an_undecidable_cell_raises_a_warning(self, roster_path: Path) -> None:
        book = openpyxl.load_workbook(roster_path)
        sheet = book["재직자명부"]
        sheet.cell(ACTIVE_FIRST_ROW, 7, "미상")      # 성별 칸
        book.save(roster_path)

        _config, roster, log = read_all(roster_path)
        assert not roster.active[0].gender_known
        assert roster.active[0].gender is Gender.MALE       # 계산은 이어진다
        assert any(i.code == "JAE_GENDER_UNKNOWN" for i in log.warnings)

    def test_a_stated_gender_is_quiet(self, roster_path: Path) -> None:
        _config, roster, log = read_all(roster_path)
        assert all(m.gender_known for m in roster.active)
        assert not [i for i in log.warnings if i.code == "JAE_GENDER_UNKNOWN"]


class TestServiceAdjustmentColumns:
    """가산·차감근속연수가 머리글로 읽히는지.

    실제 서식의 머리글은 '군경력 등 가산 근속연수' / '차감근속연수(+로 입력)'
    처럼 설명이 붙어 온다 — 정규화가 걷어내고 알아봐야 한다.
    """

    def test_reader_picks_them_up(self, tmp_path: Path) -> None:
        from pension.layout import ACTIVE_HEADER_ALIASES, find_header_row
        from pension.rostertemplate import write_roster_template

        path = write_roster_template(tmp_path / "명부.xlsx")
        book = openpyxl.load_workbook(path)
        sheet = book["재직자명부"]
        header = find_header_row(sheet, ACTIVE_HEADER_ALIASES)
        heads = {str(sheet.cell(header, c).value or ""): c
                 for c in range(1, sheet.max_column + 1)}
        # 실제 명부처럼 설명 붙은 머리글로 바꿔 본다.
        sheet.cell(header, heads["가산근속연수"], "군경력 등 \n가산 근속연수")
        sheet.cell(header, heads["차감근속연수"], "차감근속연수(+로 입력)")
        sheet.cell(header + 1, heads["가산근속연수"], 2.5)
        sheet.cell(header + 1, heads["차감근속연수"], 1)
        book.save(path)

        _config, roster, _log = read_all(path)
        first = roster.active[0]
        assert first.service_add_years == 2.5
        assert first.service_deduct_years == 1.0
        assert roster.active[1].service_add_years == 0.0
