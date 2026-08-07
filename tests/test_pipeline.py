"""명부 읽기 → 검증 → 산출 → 리포트까지의 전 과정."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

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
        """VBA 는 'N 번째 임직원' 만 알려 줬다. 여기서는 시트·행·열까지 준다."""
        self._corrupt(roster_path, ACTIVE_FIRST_ROW + 2, 11, 100)
        _, _, log = read_all(roster_path)
        issue = next(i for i in log.errors if i.code == "JAE_WAGE_BELOW_CHECK")
        assert issue.sheet == "재직자명부"
        assert issue.row == ACTIVE_FIRST_ROW + 2
        assert issue.column == "K"
        assert issue.seq == 3

    def test_all_errors_are_collected_not_just_the_first(self, roster_path: Path) -> None:
        """VBA 는 첫 오류에서 멈춘다. 여기서는 전부 모은다."""
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
    def test_column_counts_match_the_original_sheets(self, roster_path: Path) -> None:
        config, roster, _ = read_all(roster_path)
        active, retired = build_upload(roster, config)
        assert len(ACTIVE_UPLOAD_HEADERS) == 36
        assert len(RETIRED_UPLOAD_HEADERS) == 23
        assert all(len(row) == 36 for row in active)
        assert all(len(row) == 23 for row in retired)

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
            "산출요약", "증감분석", "민감도분석", "개인별산출",
            "장기급여", "검증리포트", "UpLoad_Jae", "UpLoad_Toi",
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
        assert wb["개인별산출"].max_row == len(run.valuation.members) + 1


def test_load_inputs_returns_all_four_pieces(
    roster_path: Path, assumptions_path: Path
) -> None:
    config, roster, assumptions, log = load_inputs(roster_path, assumptions_path)
    assert config.base_date == dt.date(2025, 12, 31)
    assert roster.active and roster.retired
    assert assumptions.discount.level_rate > 0
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
