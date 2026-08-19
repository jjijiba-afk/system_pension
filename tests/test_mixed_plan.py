"""혼합형은 **가입한 날부터** DB 몫이 줄어든다.

`DB 50 / DC 50` 처럼 비율만 받아 전 근속에 걸면 채무가 틀린다. 10년 일한
사람이 작년에 혼합형으로 바꿨다면 그 전 10년치는 **전액 DB** 로 남아 있는데,
비율을 통째로 걸면 그 10년치의 절반이 채무에서 조용히 사라진다.

회사가 혼합형을 들이는 것은 보통 제도 개편 때다. 그 이전 근속까지 DC 로
넘기려면 근로자 동의와 실제 이전이 필요하고, 그렇게 넘긴 경우는 중간정산일로
온다 — 그러면 근속 자체가 그날부터 다시 세어지므로 여기서 걸릴 일이 없다.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pension.config import read_config
from pension.errors import IssueLog
from pension.readers import read_roster
from pension.validation import validate_active
from pension.valuation import value_member
from pension.workbook import open_workbook

from tests.test_contract_end import roster_with


def valued(path, assumptions_path, **cells):
    """명부에 칸을 채워 넣고 첫 사람을 산출한다."""
    from pension.assumptions import load_assumptions

    wb = open_workbook(roster_with(path, **cells))
    config = read_config(wb)
    log = IssueLog()
    roster = read_roster(wb, config, log)
    validate_active(roster.active, config, log)
    assumptions = load_assumptions(assumptions_path)
    member = roster.active[0]
    return value_member(member, config, assumptions), member, log


def test_service_before_joining_stays_fully_db(roster_path, assumptions_path) -> None:
    """가입 전 근속은 전액 DB 로 남는다.

    같은 비율이라도 가입일이 있으면 채무가 더 크게 나와야 한다 — 앞 구간이
    깎이지 않기 때문이다.
    """
    whole, _, _ = valued(roster_path, assumptions_path, DB비율=0.5)
    split, member, _ = valued(
        roster_path, assumptions_path,
        DB비율=0.5, 혼합형가입일=_dt.date(2024, 1, 1))

    assert whole.dbo > 0 and split.dbo > 0
    assert split.dbo > whole.dbo, "가입 전 근속이 그대로 깎였다"
    assert split.mixed_service > 0
    assert member.mixed_plan_start_date == _dt.date(2024, 1, 1)


def test_joining_at_hire_is_the_same_as_no_date(roster_path, assumptions_path) -> None:
    """입사와 동시에 가입했으면 전 근속에 비율이 걸린 것과 같다."""
    whole, _, _ = valued(roster_path, assumptions_path, DB비율=0.5)
    from_start, member, _ = valued(
        roster_path, assumptions_path, DB비율=0.5, 혼합형가입일=_dt.date(1900, 1, 1))
    assert from_start.dbo == pytest.approx(whole.dbo)


def test_no_date_leaves_every_number_untouched(roster_path, assumptions_path) -> None:
    """가입일이 없으면 종전과 한 치도 달라지지 않는다.

    귀속비율의 분자·분모에서 비중이 약분되므로, 가입일이 없을 때는 배수를
    두 토막으로 가르는 길로 들어가도 결과가 같아야 한다.
    """
    plain, _, _ = valued(roster_path, assumptions_path)
    assert plain.db_ratio == 1.0
    assert plain.mixed_service == 0.0
    assert plain.dbo > 0


def test_a_join_date_without_a_ratio_is_flagged(roster_path, assumptions_path) -> None:
    """가입일만 있고 비중이 없으면 알린다 — 둘 중 하나가 빠진 것이다."""
    _, _, log = valued(
        roster_path, assumptions_path, 혼합형가입일=_dt.date(2024, 1, 1))
    assert any(issue.code == "JAE_MIXED_NO_RATIO" for issue in log.issues)


def test_a_future_join_date_is_flagged(roster_path, assumptions_path) -> None:
    """기준일보다 늦은 가입일은 알린다. 지금은 전 근속이 확정급여다."""
    _, _, log = valued(
        roster_path, assumptions_path,
        DB비율=0.5, 혼합형가입일=_dt.date(2099, 1, 1))
    assert any(issue.code == "JAE_MIXED_FUTURE" for issue in log.issues)


def test_the_template_asks_for_the_join_date(tmp_path) -> None:
    """양식이 가입일을 묻는지. 안 물으면 아무도 안 적고 채무가 틀린 채로 간다."""
    import openpyxl

    from pension.rostertemplate import write_roster_template

    path = write_roster_template(tmp_path / "양식.xlsx")
    wb = openpyxl.load_workbook(path)
    text = "\n".join(
        str(cell.value) for sheet in wb.worksheets
        for row in sheet.iter_rows() for cell in row if cell.value is not None)
    assert "혼합형 가입일" in text
