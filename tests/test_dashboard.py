"""분석 화면 자료 — 산출 결과와 어긋나면 안 된다."""

from __future__ import annotations

import pytest

from pension import dashboard
from pension.pipeline import PlanAssetInput, PriorPeriod, RunOptions, run_valuation


@pytest.fixture
def full_run(roster_path, assumptions_path, tmp_path):
    """전기·자산·장기급여·민감도까지 켠 산출 — 모든 구획이 채워진다."""
    return run_valuation(RunOptions(
        roster_path=roster_path, assumptions_path=assumptions_path,
        output_path=tmp_path / "r.xlsx", allow_errors=True,
        include_sensitivity=True, include_longterm=True,
        prior=PriorPeriod(dbo=300_000_000, service_cost=30_000_000,
                          discount_rate=0.04),
        plan_assets=PlanAssetInput(
            opening_fair_value=200_000_000, closing_fair_value=230_000_000,
            contributions=50_000_000, benefits_paid=20_000_000),
    ))


@pytest.fixture
def bare_run(roster_path, assumptions_path, tmp_path):
    """전기·자산·민감도·장기급여를 모두 끈 산출."""
    return run_valuation(RunOptions(
        roster_path=roster_path, assumptions_path=assumptions_path,
        output_path=tmp_path / "r.xlsx", allow_errors=True,
        include_sensitivity=False, include_longterm=False,
    ))


class TestBuild:
    def test_totals_match_the_run(self, full_run) -> None:
        """지표는 산출 결과를 그대로 옮긴 값이어야 한다."""
        d = dashboard.build(full_run)
        val = full_run.valuation
        assert d["totals"]["dbo"] == pytest.approx(val.dbo)
        assert d["totals"]["sc"] == pytest.approx(val.service_cost)
        assert d["totals"]["headcount"] == val.headcount
        assert d["totals"]["duration"] == pytest.approx(val.duration)
        assert d["base_date"] == str(full_run.config.base_date)

    def test_member_scenarios_sum_to_the_traces(self, full_run) -> None:
        """근거표 줄의 합이 그 시나리오의 채무여야 화면이 검산이 된다."""
        d = dashboard.build(full_run)
        assert len(d["scenarios"]) == 9          # 기준 + 충격 8
        for scenario in d["scenarios"]:
            assert sum(r["dbo"] for r in scenario["trace"]) == pytest.approx(
                scenario["dbo"])
            assert sum(r["service_cost"] for r in scenario["trace"]) == pytest.approx(
                scenario["service_cost"])

    def test_base_scenario_matches_the_full_run(self, full_run) -> None:
        """기준 시나리오는 전체 산출의 그 사람 몫과 원 단위까지 같아야 한다."""
        d = dashboard.build(full_run)
        picked = next(m for m in full_run.valuation.members
                      if m.employee_id == d["profile"]["사번"])
        assert d["scenarios"][0]["dbo"] == pytest.approx(picked.dbo)

    def test_group_totals_add_up(self, full_run) -> None:
        d = dashboard.build(full_run)
        assert sum(g["dbo"] for g in d["groups"]) == pytest.approx(d["totals"]["dbo"])
        assert sum(g["n"] for g in d["groups"]) == d["totals"]["headcount"]

    def test_maturity_covers_every_flow(self, full_run) -> None:
        d = dashboard.build(full_run)
        flows = full_run.valuation.cash_flows()
        assert sum(row[1] for row in d["maturity"]) == pytest.approx(sum(flows.values()))

    def test_employee_id_picks_that_person(self, full_run) -> None:
        d = dashboard.build(full_run, employee_id="A001")
        assert d["profile"]["사번"] == "A001"
        assert d["profile"]["성명"] == "김철수"

    def test_unknown_employee_is_a_clear_error(self, full_run) -> None:
        with pytest.raises(ValueError, match="찾지 못했습니다"):
            dashboard.build(full_run, employee_id="Z999")

    def test_excluded_member_cannot_be_picked(self, full_run) -> None:
        """DC 가입자는 채무가 없어 해부할 것이 없다 — 이유를 밝히고 막는다."""
        with pytest.raises(ValueError, match="찾지 못했습니다"):
            dashboard.build(full_run, employee_id="A005")

    def test_bare_run_leaves_optional_blocks_empty(self, bare_run) -> None:
        """끄고 산출한 항목은 빈 채로 와야 화면이 '없다'고 말할 수 있다."""
        d = dashboard.build(bare_run)
        assert d["rollforward"] == []      # 최초 인식 증감표는 감춘다
        assert d["assets"] == []
        assert d["sensitivity"] == []
        assert d["totals"]["lt_head"] == 0
        # 그래도 본체는 그려져야 한다.
        assert d["totals"]["dbo"] > 0
        assert d["scenarios"][0]["trace"]

    def test_curves_come_through_for_the_chart(self, full_run) -> None:
        d = dashboard.build(full_run)
        assert set(d["curves"]) == {"중도퇴직률", "승급률", "사망률"}
        assert d["curves"]["사망률"]["남자"]
        assert d["curve_axis"]["사망률"] == "연령"
