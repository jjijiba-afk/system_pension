"""계약종료일을 **날짜로** 받는다.

계약직의 남은 근무기간을 햇수로 받아 왔다. 두 가지가 어긋난다.

기준일이 바뀌면 틀린다
    전기 명부를 그대로 다시 쓰면 남은 기간이 한 해 줄었는데도 그대로 2년이다.
    채무가 한 해치만큼 부풀고, 명부만 봐서는 눈치챌 수가 없다.
적는 사람이 반올림한다
    1년 5개월 남은 사람이 흔히 2년으로 온다. 정년까지 20년 남은 사람이라면
    반년쯤은 묻히지만, 계약직은 그 반년이 근무기간의 3할이다.

날짜는 둘 다 겪지 않고, 회사 인사자료에도 날짜로 들어 있다.
"""

from __future__ import annotations

import datetime as _dt

import openpyxl
import pytest

from pension.config import read_config
from pension.errors import IssueLog
from pension.readers import ACTIVE_FIRST_ROW, read_roster
from pension.validation import validate_active
from pension.workbook import open_workbook

#: 재직자명부 머리글 줄. 여기에 열을 하나 더 붙여 읽히는지 본다.
HEADER_ROW = 23
FREE_COLUMN = 40


def roster_with(path, **cells):
    """명부에 열을 더 붙이고 첫 사람에게 값을 적는다.

    새 열은 **머리글로만** 찾으므로, 머리글 줄이 실제로 서 있어야 한다. 시험용
    명부는 사번 한 칸만 적혀 있어 머리글 줄로 인식되지 않는다 — 표준 열 이름을
    제자리에 채워 넣어 진짜 명부와 같은 모양으로 만든다.
    """
    from pension.readers import ACTIVE_COLUMNS

    wb = openpyxl.load_workbook(path)
    ws = wb["재직자명부"]
    for name, column in ACTIVE_COLUMNS.items():
        ws.cell(HEADER_ROW, column.index, column.label)
    for offset, (title, value) in enumerate(cells.items()):
        column = FREE_COLUMN + offset
        ws.cell(HEADER_ROW, column, title)
        ws.cell(ACTIVE_FIRST_ROW, column, value)
    wb.save(path)
    return path


def first_member(path):
    wb = open_workbook(path)
    config = read_config(wb)
    log = IssueLog()
    roster = read_roster(wb, config, log)
    # 검증까지 돌려야 진짜 산출과 같은 길이다 — 계약이 끝난 사람을 짚는 것도
    # 거기서 한다.
    validate_active(roster.active, config, log)
    return roster.active[0], config, log


def test_a_date_becomes_the_remaining_years(roster_path) -> None:
    """날짜만 적어도 남은 기간이 나온다."""
    member, config, log = first_member(
        roster_with(roster_path, 계약종료일=_dt.date(2027, 6, 30)))

    assert member.contract_end_date == _dt.date(2027, 6, 30)
    expected = (_dt.date(2027, 6, 30) - config.base_date).days / 365.25
    assert member.remaining_contract_years == pytest.approx(expected)
    assert not log.has_errors()


def test_the_date_wins_over_the_written_years(roster_path) -> None:
    """둘 다 오면 날짜를 쓴다 — 햇수 쪽이 낡았을 수 있다.

    조용히 고르지는 않는다. 어느 쪽이 낡았는지는 회사만 알므로, 반년 넘게
    어긋나면 어느 쪽으로 계산했는지 말해 준다.
    """
    member, config, log = first_member(roster_with(
        roster_path, 계약종료일=_dt.date(2027, 6, 30), 잔여계약기간=9))

    expected = (_dt.date(2027, 6, 30) - config.base_date).days / 365.25
    assert member.remaining_contract_years == pytest.approx(expected)
    assert any(issue.code == "JAE_CONTRACT_MISMATCH" for issue in log.issues), \
        "날짜로 덮었으면서 말해 주지 않았다"


def test_close_enough_does_not_nag(roster_path) -> None:
    """반년 안쪽으로 맞으면 굳이 말하지 않는다 — 그것까지 짚으면 소음이다."""
    base = read_config(open_workbook(roster_path)).base_date
    end = base + _dt.timedelta(days=730)
    _, _, log = first_member(
        roster_with(roster_path, 계약종료일=end, 잔여계약기간=2))
    assert not any(issue.code == "JAE_CONTRACT_MISMATCH" for issue in log.issues)


def test_written_years_still_work_on_their_own(roster_path) -> None:
    """날짜 열이 없는 옛 명부도 그대로 읽힌다."""
    member, _, log = first_member(roster_with(roster_path, 잔여계약기간=3))
    assert member.contract_end_date is None
    assert member.remaining_contract_years == 3
    assert not log.has_errors()


def test_an_expired_contract_is_flagged_not_silently_zeroed(roster_path) -> None:
    """이미 끝난 계약은 0 으로 밀어 넣지 않고 사람에게 알린다.

    남은 기간을 0 으로 만들면 채무가 통째로 사라진다. 계약을 갱신했는데 명부에
    옛 날짜가 남은 것인지, 정말 나간 사람이 재직자명부에 남은 것인지는 회사만
    안다 — 우리가 조용히 정할 일이 아니다.
    """
    base = read_config(open_workbook(roster_path)).base_date
    member, _, log = first_member(
        roster_with(roster_path, 계약종료일=base - _dt.timedelta(days=40)))

    assert member.remaining_contract_years == 0.0
    assert any(issue.code == "JAE_CONTRACT_ENDED" for issue in log.issues)


def test_the_template_asks_for_the_date(tmp_path) -> None:
    """양식이 날짜를 먼저 묻는지. 안 물으면 아무도 안 적는다."""
    from pension.rostertemplate import write_roster_template

    path = write_roster_template(tmp_path / "양식.xlsx")
    wb = openpyxl.load_workbook(path)
    text = "\n".join(
        str(cell.value) for sheet in wb.worksheets
        for row in sheet.iter_rows() for cell in row if cell.value is not None)
    assert "계약종료일" in text
