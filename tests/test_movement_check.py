"""추계액 증감표가 **명부 검산** 이 되는지.

사람별 추계액만 맞대는 것으로는 부족하다. 명부에서 아예 빠진 사람은 짝지을
상대가 없어 비교조차 되지 않고, 합계도 그만큼 작아진 채로 그럴듯하다. 그래서
회사가 자기 기록에서 뽑아 온 **기말 추계액** 과 명부 합계를 맞댄다 — 한 사람이
통째로 빠지면 그 사람의 추계액만큼 두 값이 벌어진다.

기초에서 출발해 그 해에 드나든 것을 더하고 빼면 기말이 나와야 한다는 것도
함께 본다. 그 표가 스스로 맞지 않으면 기말부터가 믿을 값이 아니다.
"""

from __future__ import annotations

import openpyxl
import pytest

from pension.general_info import ObligationMovement


class TestTheTableBalancesItself:
    """기초 + 유입 − 유출 − 기말 = 0."""

    def test_a_balanced_table_has_no_difference(self) -> None:
        movement = ObligationMovement(
            opening=1_000_000_000, accrual=120_000_000,
            benefits_paid=80_000_000, settlement_paid=40_000_000,
            closing=1_000_000_000,
        )
        assert movement.difference == 0
        assert movement.has_ends()

    def test_a_missing_row_shows_up_as_a_difference(self) -> None:
        """증가분을 빠뜨리면 그만큼 어긋난다."""
        movement = ObligationMovement(
            opening=1_000_000_000, benefits_paid=80_000_000, closing=1_000_000_000)
        assert movement.difference == -80_000_000

    def test_without_both_ends_there_is_nothing_to_check(self) -> None:
        """한쪽만 적어 오면 검산이 성립하지 않는다 — 조용히 넘어간다."""
        assert not ObligationMovement(opening=1_000_000_000).has_ends()
        assert not ObligationMovement(closing=1_000_000_000).has_ends()
        assert not ObligationMovement().has_ends()


class TestAMissingPersonIsCaught:
    """명부에서 사람을 지우면 걸리는지. 이것이 이 표를 받는 이유다."""

    def run(self, roster_path, assumptions_path, tmp_path):
        from pension.pipeline import RunOptions, run_valuation

        return run_valuation(RunOptions(
            roster_path=roster_path, assumptions_path=assumptions_path,
            output_path=tmp_path / "결과.xlsx",
        ))

    @pytest.fixture
    def pack(self, tmp_path):
        """양식 한 벌. 작성 예시 두 사람의 추계액이 곧 증감표의 기말이다."""
        from pension.samples import (
            ROSTER_TEMPLATE, STANDARD_ASSUMPTIONS, write_sample_pack)

        write_sample_pack(tmp_path)
        return tmp_path / ROSTER_TEMPLATE, tmp_path / STANDARD_ASSUMPTIONS

    def test_the_generated_pack_agrees_with_itself(self, pack, tmp_path) -> None:
        roster, assumptions = pack
        run = self.run(roster, assumptions, tmp_path)
        codes = [i.code for i in run.issues.warnings]
        assert "GEN_ROSTER_TOTAL_GAP" not in codes, codes
        assert "GEN_OBLIGATION_NOT_BALANCED" not in codes, codes

    def test_deleting_one_person_is_caught(self, pack, tmp_path) -> None:
        """한 사람을 지운다. 증감표는 그대로이므로 그만큼 벌어져야 한다."""
        roster_path, assumptions = pack
        # 지울 사람은 **엔진이 읽은 대로** 고른다. 머리글 자리를 손으로 세면
        # 서식이 바뀔 때 시험이 엉뚱한 열을 지운다.
        before = self.run(roster_path, assumptions, tmp_path)
        biggest = max(before.roster.active, key=lambda m: m.accrued_benefit)
        assert biggest.accrued_benefit > 1_000_000, "문턱을 넘는 사람이 없다"

        wb = openpyxl.load_workbook(roster_path)
        wb["재직자명부"].delete_rows(biggest.row)
        wb.save(roster_path)

        run = self.run(roster_path, assumptions, tmp_path)
        assert any(i.code == "GEN_ROSTER_TOTAL_GAP" for i in run.issues.warnings), \
            "사람이 빠졌는데 아무 말이 없다 — 사람별 대조로는 못 잡는 자리다"
