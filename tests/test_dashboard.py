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


@pytest.fixture
def rich_run(roster_path, assumptions_path, tmp_path):
    """전기 기초율·장기급여 전기채무·자산인식상한까지 준 산출."""
    return run_valuation(RunOptions(
        roster_path=roster_path, assumptions_path=assumptions_path,
        output_path=tmp_path / "r.xlsx", allow_errors=True,
        include_sensitivity=False, include_longterm=True,
        split_remeasurement=True,
        prior=PriorPeriod(dbo=300_000_000, service_cost=30_000_000,
                          discount_rate=0.04, longterm_dbo=40_000_000,
                          assumptions_path=str(assumptions_path)),
        plan_assets=PlanAssetInput(
            opening_fair_value=200_000_000, closing_fair_value=230_000_000,
            contributions=50_000_000, benefits_paid=20_000_000,
            expected_contributions=60_000_000),
    ))


class TestRemeasurementSplit:
    def test_steps_add_up_to_the_assumption_change(self, rich_run) -> None:
        """가정별 몫의 합은 가정변경효과와 정확히 같아야 한다."""
        roll = rich_run.rollforward
        assert [k for k, _ in roll.assumption_steps] == [
            "사망률", "퇴직률", "임금상승률", "할인율"]
        assert sum(v for _, v in roll.assumption_steps) == pytest.approx(
            roll.assumption_change, abs=1e-6)

    def test_same_assumptions_move_nothing(self, rich_run) -> None:
        """전기 기초율이 당기와 같으면 가정변경은 전부 0 이다."""
        for _, amount in rich_run.rollforward.assumption_steps:
            assert amount == pytest.approx(0.0, abs=1e-6)

    def test_off_by_default(self, full_run) -> None:
        assert full_run.rollforward.assumption_steps == []

    def test_reaches_the_dashboard(self, rich_run) -> None:
        d = dashboard.build(rich_run)
        assert len(d["assumption_steps"]) == 4


class TestAssetCeiling:
    def _assets(self, **kw):
        from pension.planassets import build_plan_assets
        base = dict(opening_fair_value=0.0, closing_fair_value=500_000_000,
                    closing_dbo=300_000_000, discount_rate=0.04)
        return build_plan_assets(**{**base, **kw})

    def test_caps_the_recognised_asset(self) -> None:
        """초과적립 2억, 상한 5천만 → 자산은 5천만까지만 (문단 64)."""
        assets = self._assets(asset_ceiling=50_000_000)
        assert assets.surplus == pytest.approx(200_000_000)
        assert assets.ceiling_effect == pytest.approx(150_000_000)
        assert assets.net_liability == pytest.approx(-50_000_000)
        labels = [k for k, _ in assets.net_rows()]
        assert "자산인식상한 적용에 따른 자산차감액" in labels

    def test_ceiling_above_surplus_changes_nothing(self) -> None:
        assets = self._assets(asset_ceiling=900_000_000)
        assert assets.ceiling_effect == 0.0
        assert assets.net_liability == pytest.approx(-200_000_000)

    def test_underfunded_plan_is_untouched(self) -> None:
        """미달적립이면 상한을 넣어도 순부채가 그대로여야 한다."""
        assets = self._assets(closing_fair_value=100_000_000,
                              asset_ceiling=10_000_000)
        assert assets.surplus == 0.0
        assert assets.ceiling_effect == 0.0
        assert assets.net_liability == pytest.approx(200_000_000)

    def test_no_ceiling_keeps_the_old_table(self) -> None:
        assets = self._assets()
        assert assets.ceiling_effect == 0.0
        assert "자산인식상한 적용에 따른 자산차감액" not in [k for k, _ in assets.net_rows()]


class TestProjection:
    def test_dbo_projection_balances(self, rich_run) -> None:
        p = rich_run.projection
        assert p.opening_dbo == pytest.approx(rich_run.valuation.dbo)
        assert p.closing_dbo == pytest.approx(
            p.opening_dbo + p.service_cost + p.interest_cost - p.benefits_paid)

    def test_assets_projection_uses_expected_contributions(self, rich_run) -> None:
        p = rich_run.projection
        assert p.has_assets
        assert p.contributions == pytest.approx(60_000_000)
        assert p.closing_assets == pytest.approx(
            p.opening_assets + p.expected_return + p.contributions - p.assets_paid)

    def test_contributions_default_to_this_period(self, full_run) -> None:
        assert full_run.projection.contributions == pytest.approx(50_000_000)

    def test_reaches_the_dashboard(self, rich_run) -> None:
        d = dashboard.build(rich_run)
        assert d["projection"]["dbo"] and d["projection"]["assets"]
        assert d["projection"]["expense"][-1][0] == "합계"


class TestLongtermRollforward:
    def test_balances_and_recognises_in_profit_or_loss(self, rich_run) -> None:
        roll = rich_run.longterm_rollforward
        assert roll is not None
        assert roll.opening_dbo == pytest.approx(40_000_000)
        assert roll.closing_dbo == pytest.approx(rich_run.longterm.dbo)
        # 표가 닫혀야 한다: 기초 + 근무 + 이자 − 지급 + 재측정 = 기말
        assert roll.expected_closing_dbo + roll.remeasurement == pytest.approx(
            roll.closing_dbo)
        assert roll.profit_or_loss == pytest.approx(
            roll.service_cost + roll.interest_cost + roll.remeasurement)

    def test_absent_without_prior_longterm(self, full_run) -> None:
        assert full_run.longterm_rollforward is None

    def test_reaches_the_dashboard(self, rich_run) -> None:
        d = dashboard.build(rich_run)
        assert [k for k, _ in d["longterm_roll"]][0] == "기초 확정급여채무"
