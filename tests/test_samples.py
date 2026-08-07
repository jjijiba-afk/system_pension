"""배포 꾸러미에 같이 넣는 기본 파일들.

받는 사람이 처음 여는 파일이 곧바로 실패하면 안 된다. 양식이 프로그램 자신에게
읽히는지, 기본 기초율만으로 산출이 끝까지 도는지를 여기서 고정한다.
"""

from __future__ import annotations

import pytest

from pension.samples import (
    ROSTER_TEMPLATE,
    STANDARD_ASSUMPTIONS,
    TEMPLATE_ASSUMPTIONS,
    write_sample_pack,
)
from pension.standard_rates import MORTALITY_AGES, mortality_table


@pytest.fixture
def pack(tmp_path):
    write_sample_pack(tmp_path)
    return tmp_path


class TestRoundTrip:
    """양식이 프로그램 자신에게 읽혀야 한다. 안 그러면 양식이 아니다."""

    def test_roster_template_reads_back(self, pack) -> None:
        from pension.config import read_config
        from pension.errors import IssueLog
        from pension.readers import read_roster
        from pension.workbook import open_workbook

        book = open_workbook(pack / ROSTER_TEMPLATE)
        try:
            config = read_config(book)
            roster = read_roster(book, config, IssueLog())
        finally:
            book.close()

        assert len(roster.active) == 2
        assert len(roster.retired) == 2
        # 머리글로 열을 찾았으므로 값이 제자리에 들어가야 한다.
        assert [m.employee_id for m in roster.active] == ["A0001", "A0002"]
        assert roster.active[0].monthly_wage == 5_000_000
        assert roster.active[1].employee_type.value == "임원"
        assert roster.retired[0].total_payment == 45_000_000

    def test_standard_assumptions_load(self, pack) -> None:
        from pension.assumptions import load_assumptions
        from pension.normalize import Gender

        assumptions = load_assumptions(pack / STANDARD_ASSUMPTIONS)
        assert assumptions.discount.level_rate > 0
        assert assumptions.mortality.qx(Gender.MALE, 60) > 0
        assert assumptions.mortality.qx(Gender.FEMALE, 60) > 0

    def test_blank_template_loads(self, pack) -> None:
        from pension.assumptions import load_assumptions

        assert load_assumptions(pack / TEMPLATE_ASSUMPTIONS).discount.level_rate > 0


class TestOutOfTheBox:
    """기본 파일 두 개만으로 산출이 끝까지 돌아야 한다."""

    def test_calculation_runs_clean(self, pack, tmp_path) -> None:
        from pension.pipeline import RunOptions, run_valuation
        from pension.report import write_report

        run = run_valuation(RunOptions(
            roster_path=pack / ROSTER_TEMPLATE,
            assumptions_path=pack / STANDARD_ASSUMPTIONS,
            output_path=tmp_path / "결과.xlsx",
        ))

        assert not run.issues.has_errors()
        assert not run.issues.warnings
        assert run.valuation.headcount == 2
        assert run.valuation.dbo > 0
        write_report(run, tmp_path / "결과.xlsx")
        assert (tmp_path / "결과.xlsx").exists()

    def test_payout_sheet_drives_the_job_groups(self, pack, tmp_path) -> None:
        """기초율의 `지급규정` 시트가 명부 Input 보다 우선한다."""
        from pension.pipeline import load_inputs

        config, roster, _a, _log = load_inputs(
            pack / ROSTER_TEMPLATE, pack / STANDARD_ASSUMPTIONS
        )
        assert config.mapped_names() == ["정규직", "계약직", "임원"]
        assert {m.job_group for m in roster.active} == {"정규직"}


class TestMortality:
    """통계청 「2024년 생명표」 공표치에 맞춘 값이다."""

    def test_covers_the_working_age_range(self) -> None:
        table = mortality_table()
        assert [row[0] for row in table] == list(MORTALITY_AGES)

    def test_rates_increase_with_age(self) -> None:
        for column in (1, 2):
            rates = [row[column] for row in mortality_table()]
            assert rates == sorted(rates)
            assert all(0.0 < r < 1.0 for r in rates)

    def test_women_die_later(self) -> None:
        assert all(row[2] < row[1] for row in mortality_table())

    @pytest.mark.parametrize(
        ("sex", "column", "expected"),
        [("남자", 1, 0.656), ("여자", 2, 0.833)],
    )
    def test_survival_from_40_to_80_matches_the_published_figure(
        self, sex: str, column: int, expected: float
    ) -> None:
        """40세 생존자가 80세까지 살 확률: 남 65.6% / 여 83.3%."""
        rates = {row[0]: row[column] for row in mortality_table()}
        survival = 1.0
        for age in range(40, 80):
            survival *= 1.0 - rates[age]
        assert survival == pytest.approx(expected, abs=0.005)

    @pytest.mark.parametrize(
        ("sex", "column", "expected"),
        [("남자", 1, 0.012 / 0.644), ("여자", 2, 0.048 / 0.822)],
    )
    def test_survival_from_80_to_100_matches_the_published_figure(
        self, sex: str, column: int, expected: float
    ) -> None:
        rates = {row[0]: row[column] for row in mortality_table()}
        survival = 1.0
        for age in range(80, 100):
            survival *= 1.0 - rates[age]
        assert survival == pytest.approx(expected, abs=0.005)

    def test_no_discontinuity_where_the_two_curves_join(self) -> None:
        """80세에서 두 구간이 이어 붙는다. 표에 단절이 보이면 안 된다."""
        rates = {row[0]: (row[1], row[2]) for row in mortality_table()}
        for column in (0, 1):
            step_before = rates[79][column] / rates[78][column]
            step_across = rates[80][column] / rates[79][column]
            assert step_across == pytest.approx(step_before, rel=0.06)
