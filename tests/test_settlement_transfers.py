"""중간정산자·계열사 전출입자를 **따로 세어** 증감표와 맞댄다.

이 사람들은 재직자·퇴직자 명부에 섞여 있다. 한 사람이 한 줄이라는 점에서는
그 편이 맞다 — 같은 사람을 두 시트에 적으면 어느 쪽이 이기는지가 애매해지고
채무가 두 번 잡히기 쉽다.

다만 섞여 있으면 **그 해에 정산·전출입한 사람이 몇이고 얼마인지** 가 어디에도
드러나지 않는다. 증감표에는 줄이 따로 있는데 명부에는 없으니, 그 줄이 틀려도
맞대어 볼 상대가 없다. 그래서 시트를 나누는 대신 **세는 자리를 만들었다.**

셀 때는 그 해에 일어난 것만 센다. 재직자명부의 중간정산일·전입일은 몇 해 전
것도 그대로 남아 있어, 통째로 더하면 옛 금액이 당기 증감표와 맞대어져 매번
틀렸다고 나온다.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pension.errors import IssueLog
from pension.general_info import GeneralInfo, ObligationMovement
from pension.models import ActiveMember, RetiredMember, Roster
from pension.pipeline import _check_settlement_and_transfers


class Config:
    """산출기준일만 있으면 되는 자리라 그것만 둔다."""

    base_date = _dt.date(2025, 12, 31)


def general_with(**movement) -> GeneralInfo:
    info = GeneralInfo()
    info.period_start = _dt.date(2025, 1, 1)
    info.obligation = ObligationMovement(**movement)
    return info


def active(seq: int, **fields) -> ActiveMember:
    member = ActiveMember(seq=seq, row=seq + 4, employee_id=f"A{seq:04d}")
    for name, value in fields.items():
        setattr(member, name, value)
    return member


def retired(seq: int, **fields) -> RetiredMember:
    member = RetiredMember(seq=seq, row=seq + 4, employee_id=f"T{seq:04d}")
    for name, value in fields.items():
        setattr(member, name, value)
    return member


def check(roster, info) -> IssueLog:
    log = IssueLog()
    _check_settlement_and_transfers(roster, Config(), info, log)
    return log


class TestSettlement:
    def test_agreeing_numbers_say_nothing(self) -> None:
        roster = Roster(active=[
            active(1, settlement_date=_dt.date(2025, 6, 30),
                   settlement_amount=120_000_000),
        ])
        log = check(roster, general_with(settlement_paid=120_000_000))
        assert log.warnings == []

    def test_a_gap_is_named(self) -> None:
        roster = Roster(active=[
            active(1, settlement_date=_dt.date(2025, 6, 30),
                   settlement_amount=80_000_000),
        ])
        log = check(roster, general_with(settlement_paid=120_000_000))
        assert [i.code for i in log.warnings] == ["GEN_MOVEMENT_ROSTER_GAP"]
        assert "중간정산금" in log.warnings[0].message

    def test_an_old_settlement_is_not_counted(self) -> None:
        """몇 해 전 중간정산은 당기 증감표와 상관이 없다.

        재직자명부의 중간정산일은 옛것도 그대로 남아 있다. 통째로 더하면 그
        회사는 매년 틀렸다는 말을 듣는다.
        """
        roster = Roster(active=[
            active(1, settlement_date=_dt.date(2016, 3, 1),
                   settlement_amount=900_000_000),
        ])
        log = check(roster, general_with(settlement_paid=0))
        assert log.warnings == []
        assert log.notices == []

    def test_an_empty_column_is_a_notice_not_a_warning(self) -> None:
        """사람별 금액을 안 적어 보내는 회사가 흔하다.

        틀렸다고 하면 매번 걸리는 잔소리가 되고, 아무 말도 안 하면 검산이
        돌았다고 오해한다. 맞대어 보지 못했다는 사실만 남긴다.
        """
        log = check(Roster(active=[active(1)]),
                    general_with(settlement_paid=120_000_000))
        assert log.warnings == []
        assert [i.code for i in log.notices] == ["GEN_MOVEMENT_NO_ROSTER"]


class TestTransfers:
    def test_transfer_in_is_counted_from_the_active_roster(self) -> None:
        roster = Roster(active=[
            active(1, transfer_in_date=_dt.date(2025, 4, 1),
                   transfer_in_amount=50_000_000),
        ])
        assert check(roster, general_with(transfer_in=50_000_000)).warnings == []
        gap = check(roster, general_with(transfer_in=90_000_000))
        assert [i.code for i in gap.warnings] == ["GEN_MOVEMENT_ROSTER_GAP"]
        assert "계열사 전입" in gap.warnings[0].message

    def test_transfer_out_is_counted_from_the_retired_roster(self) -> None:
        roster = Roster(retired=[retired(1, transfer_out_payment=30_000_000)])
        assert check(roster, general_with(transfer_out=30_000_000)).warnings == []
        gap = check(roster, general_with(transfer_out=70_000_000))
        assert [i.code for i in gap.warnings] == ["GEN_MOVEMENT_ROSTER_GAP"]
        assert "계열사 전출" in gap.warnings[0].message


class TestItStaysQuietWhenItShould:
    def test_no_movement_table_no_words(self) -> None:
        assert check(Roster(active=[active(1)]), general_with()).issues == []

    def test_rounding_is_forgiven(self) -> None:
        """단수로 몇 만 원 남는 것은 흔하다 — 그것까지 짚으면 소음이다."""
        roster = Roster(active=[
            active(1, settlement_date=_dt.date(2025, 6, 30),
                   settlement_amount=120_000_000),
        ])
        log = check(roster, general_with(settlement_paid=120_040_000))
        assert log.warnings == []

    def test_without_a_period_a_year_is_assumed(self) -> None:
        """기간을 모르면 한 해로 본다. 그 안의 정산만 세어야 한다."""
        info = general_with(settlement_paid=0)
        info.period_start = None
        roster = Roster(active=[
            active(1, settlement_date=_dt.date(2016, 3, 1),
                   settlement_amount=900_000_000),
        ])
        assert check(roster, info).warnings == []
