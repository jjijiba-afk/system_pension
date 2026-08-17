"""사번 조회 — 한 명 재산출이 전체 산출과 같은 값을 내는지가 전부다."""

from __future__ import annotations

import pytest

from pension.memberdetail import lookup
from pension.pipeline import load_inputs
from pension.valuation import value_member, value_roster


class TestLookup:
    def test_matches_full_run_to_the_won(self, roster_path, assumptions_path) -> None:
        """조회 화면의 채무가 전체 산출의 그 사람 몫과 달라지면 안 된다."""
        config, roster, assumptions, _log, _ = load_inputs(roster_path, assumptions_path)
        full = value_roster(roster, config, assumptions)
        expected = next(m for m in full.members if m.employee_id == "A001")

        detail = lookup(str(roster_path), str(assumptions_path), "A001")
        row = detail["rows"][0]
        assert row["result"]["확정급여채무 (DBO)"] == pytest.approx(expected.dbo)
        assert row["result"]["당기근무원가"] == pytest.approx(expected.service_cost)
        assert row["result"]["퇴직급여추계액"] == pytest.approx(expected.accrued_benefit)

    def test_trace_rows_add_up(self, roster_path, assumptions_path) -> None:
        """연차별 근거 줄의 합 = 그 사람의 채무·근무원가. 근거표가 검산이 된다."""
        detail = lookup(str(roster_path), str(assumptions_path), "A001")
        row = detail["rows"][0]
        assert sum(t["dbo"] for t in row["trace"]) == pytest.approx(
            row["result"]["확정급여채무 (DBO)"])
        assert sum(t["service_cost"] for t in row["trace"]) == pytest.approx(
            row["result"]["당기근무원가"])
        # 근거 줄에는 사람이 재계산할 수 있는 요소가 다 있어야 한다.
        first = row["trace"][0]
        for key in ("t", "timing", "age", "service", "wage", "withdrawal",
                    "mortality", "survival", "cause", "exit_probability",
                    "benefit", "attributed", "unit", "discount"):
            assert key in first

    def test_trace_does_not_change_the_engine(self, roster_path, assumptions_path) -> None:
        """trace 를 켜고 꺼도 산출 숫자는 비트 단위로 같아야 한다."""
        config, roster, assumptions, _log, _ = load_inputs(roster_path, assumptions_path)
        member = next(m for m in roster.active if m.employee_id == "A001")
        plain = value_member(member, config, assumptions)
        traced = value_member(member, config, assumptions, trace=[])
        assert plain.dbo == traced.dbo
        assert plain.service_cost == traced.service_cost

    def test_excluded_member_shows_reason(self, roster_path, assumptions_path) -> None:
        """DC 가입자도 조회는 되고, 왜 채무가 없는지 이유가 나온다."""
        detail = lookup(str(roster_path), str(assumptions_path), "A005")
        row = detail["rows"][0]
        assert "DC" in row["excluded"]
        assert row["result"]["확정급여채무 (DBO)"] == 0.0

    def test_retired_member_found_in_retired_roster(
        self, roster_path, assumptions_path
    ) -> None:
        detail = lookup(str(roster_path), str(assumptions_path), "R001")
        assert detail["rows"] == []
        assert detail["retired"][0]["성명"] == "한퇴직"
        assert detail["retired"][0]["퇴직급여 지급총액"] == 92_000_000

    def test_unknown_id_is_a_clear_error(self, roster_path, assumptions_path) -> None:
        with pytest.raises(ValueError, match="찾지 못했습니다"):
            lookup(str(roster_path), str(assumptions_path), "Z999")
        with pytest.raises(ValueError, match="사번을 입력"):
            lookup(str(roster_path), str(assumptions_path), "  ")

    def test_longterm_block_present_for_targets(
        self, roster_path, assumptions_path
    ) -> None:
        """장기급여 대상자(명부 Y)는 장기급여 블록도 같이 나온다."""
        detail = lookup(str(roster_path), str(assumptions_path), "A001")
        assert detail["longterm"], "장기급여 규정이 있는데 블록이 없다"
        block = detail["longterm"][0]
        if not block["excluded"]:
            assert sum(t["dbo"] for t in block["trace"]) == pytest.approx(
                block["result"]["장기급여채무"])


class TestTheExtraPaymentNotice:
    """명부 [추가지급 기본급] 안내가 **사실을 말하는지.**

    예전에는 그 칸이 차 있기만 하면 "산출에 더해지지 않았습니다" 라고 했다.
    시키는 대로 규정에 옮겨 적어 실제로 더해지고 있는데도 같은 문구가 그대로
    떠서, 아직 안 걸린 줄 알고 다시 손대게 됐다. 안내가 틀리면 맞는 설정을
    사람이 되돌린다.
    """

    def _note(self, roster_path, assumptions_path, *, multiple=None):
        from pension.assumptions import CAUSE_DEATH, CauseBenefit, CauseBenefits
        from pension.memberdetail import _active_block

        config, roster, assumptions, _log, _ = load_inputs(
            roster_path, assumptions_path)
        member = next(m for m in roster.active if m.employee_id == "A001")
        member.extra_pay_base_wage = 50_000_000
        if multiple is not None:
            rule = member.rules.severance_benefit or member.job_group
            assumptions.exit_causes = CauseBenefits(rules={
                (rule, CAUSE_DEATH): CauseBenefit(roster_extra_multiple=multiple)})
        block = _active_block(member, config, assumptions)
        return block["applied"]["명부 추가지급 기본급"]

    def test_it_says_so_when_the_rule_does_not_use_the_column(
        self, roster_path, assumptions_path
    ) -> None:
        note = self._note(roster_path, assumptions_path)
        assert "더해지지 않았습니다" in note
        # 어디에 적어야 하는지까지 말해 줘야 한다.
        assert "명부 추가지급 배수" in note

    def test_it_confirms_once_the_rule_uses_the_column(
        self, roster_path, assumptions_path
    ) -> None:
        note = self._note(roster_path, assumptions_path, multiple=1)
        assert "더해지지 않았습니다" not in note, note
        assert "더해집니다" in note, note
        assert "사망" in note, note

    def test_it_shows_the_multiple_when_it_is_not_one(
        self, roster_path, assumptions_path
    ) -> None:
        """절반만 얹는 규정이면 그 사실이 보여야 한다."""
        note = self._note(roster_path, assumptions_path, multiple=0.5)
        assert "0.5" in note, note
