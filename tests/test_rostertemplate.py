"""회사에 보내는 명부 양식.

이 파일은 우리가 아니라 **인사·회계 담당자** 가 읽는다. 여기서 어긋나면 산출이
틀리는 게 아니라 자료가 안 오거나, 엉뚱하게 채워져 온다. 그래서 시험도
"프로그램이 읽히는가" 가 아니라 "사람이 무엇을 어디에 적어야 하는지 알 수
있는가" 를 본다.
"""

from __future__ import annotations

import openpyxl
import pytest

from pension import rostertemplate as tpl
from pension.rostertemplate import (
    ACTIVE,
    BLOCK_ROW,
    BLOCKS,
    FIRST_DATA_ROW,
    HEADER_ROW,
    PART_ROW,
    PARTS,
    RETIRED,
    build_workbook,
    write_roster_template,
)


@pytest.fixture(scope="module")
def blank(tmp_path_factory):
    path = tmp_path_factory.mktemp("양식") / "명부양식.xlsx"
    write_roster_template(path)
    book = openpyxl.load_workbook(path)
    yield book
    book.close()


class TestTheTwoParts:
    """퇴직급여와 기타장기는 열 자리로 갈려 있어야 한다.

    근속포상이 없는 회사가 대부분이다. 장기급여 열이 퇴직급여 열 사이에 끼어
    있으면 "우리도 이 칸을 채워야 하나" 를 열마다 되묻게 되고, 되물어 볼 곳이
    없으면 짐작해서 채운다. 짐작이 들어온 명부는 우리가 알아볼 방법이 없다.
    """

    @pytest.mark.parametrize("columns", [ACTIVE, RETIRED], ids=["재직자", "퇴직자"])
    def test_the_long_term_part_is_one_unbroken_run_at_the_right_end(
        self, columns
    ) -> None:
        parts = [BLOCKS[block][0] for block, *_rest in columns]
        first = parts.index("기타장기")
        assert set(parts[first:]) == {"기타장기"}, (
            "기타장기 파트가 끊겨 있다. 통째로 지우면 퇴직급여 열까지 사라진다")
        assert set(parts[:first]) == {"퇴직급여"}

    @pytest.mark.parametrize("columns", [ACTIVE, RETIRED], ids=["재직자", "퇴직자"])
    def test_every_block_belongs_to_a_declared_part(self, columns) -> None:
        for block, *_rest in columns:
            assert BLOCKS[block][0] in PARTS

    @pytest.mark.parametrize("sheet", ["재직자명부", "퇴직자명부"])
    def test_the_sheet_shows_the_part_above_the_block(self, blank, sheet) -> None:
        ws = blank[sheet]
        bands = [c.value for c in ws[PART_ROW] if c.value]
        assert bands == ["퇴직급여", "기타장기"], (
            "맨 윗줄이 파트 두 개로 갈려 보여야 한다")
        assert "필수" in [c.value for c in ws[BLOCK_ROW] if c.value]

    def test_dropping_the_long_term_part_still_reads(self, blank, tmp_path) -> None:
        """기타장기를 통째로 지운 명부도 그대로 읽혀야 한다.

        "없으면 지우고 보내세요" 라고 적어 놓고 지운 것이 안 읽히면, 그 말을
        믿은 회사만 손해를 본다.
        """
        from pension.layout import (
            ACTIVE_HEADER_ALIASES,
            find_data_start,
            find_header_row,
            normalize_header,
        )

        path = tmp_path / "기타장기없음.xlsx"
        book = build_workbook()
        ws = book["재직자명부"]
        parts = [BLOCKS[block][0] for block, *_rest in ACTIVE]
        first = parts.index("기타장기") + 1          # 1부터 세는 열 번호
        ws.delete_cols(first, len(ACTIVE) - first + 1)
        book.save(path)

        reopened = openpyxl.load_workbook(path)
        sheet = reopened["재직자명부"]
        assert find_header_row(sheet, ACTIVE_HEADER_ALIASES) == HEADER_ROW
        assert find_data_start(sheet, HEADER_ROW) == FIRST_DATA_ROW

        known = {normalize_header(name): key
                 for key, names in ACTIVE_HEADER_ALIASES.items() for name in names}
        found = {known.get(normalize_header(c.value))
                 for c in sheet[HEADER_ROW] if c.value}
        assert "hire_date" in found, "퇴직급여 열까지 함께 사라졌다"
        assert "longterm_target" not in found
        reopened.close()


class TestTheLongTermRuleSheet:
    """근속포상 규정을 적을 자리.

    퇴직급여규정 안에 한 칸으로 끼워 두었더니, 없는 회사는 "뭘 적으라는 건지"
    로 읽고 있는 회사는 한 칸에 못 적어 규정집을 통째로 첨부해 보냈다. 어느
    쪽이든 우리가 되물어야 했다.
    """

    def test_it_is_its_own_sheet_next_to_the_severance_rules(self, blank) -> None:
        names = blank.sheetnames
        assert names.index("장기급여규정") == names.index("퇴직급여규정") + 1

    def test_it_asks_for_the_schedule_row_by_row(self, blank) -> None:
        ws = blank["장기급여규정"]
        heads = {c.value for row in ws.iter_rows() for c in row if c.value}
        for want in ("규정명", "근속년수", "지급 내용", "지급기준 (금액 환산)"):
            assert want in heads, f"'{want}' 을 묻지 않는다"

    def test_the_payout_words_match_what_the_engine_takes(self) -> None:
        """지급방법 낱말이 산출가정과 달라지면 우리가 손으로 옮기며 뜻을 바꾼다."""
        from pension.assumptions import LONGTERM_TYPES

        assert tpl.LONGTERM_KINDS == LONGTERM_TYPES

    def test_the_timings_offered_are_the_ones_the_engine_knows(self) -> None:
        from pension.assumptions import LONGTERM_TIMINGS

        note = dict((label, note) for label, _s, note in tpl.LONGTERM_RULE_ROWS)
        for timing in LONGTERM_TIMINGS:
            assert timing in note["지급시점"]

    def test_a_blank_form_still_shows_what_an_answer_looks_like(self, blank) -> None:
        """예시가 없으면 '대상' 칸은 대개 비어서 돌아온다."""
        ws = blank["장기급여규정"]
        written = [c.value for row in ws.iter_rows(min_col=2, max_col=6)
                   for c in row if isinstance(c.value, str)]
        assert any("휴가" in v for v in written)
        assert any("소멸" in v or "이월" in v for v in written)

    def test_it_points_back_at_the_roster_columns(self, blank) -> None:
        """규정 시트와 명부가 서로를 가리켜야 한 바퀴가 닫힌다."""
        ws = blank["장기급여규정"]
        text = "\n".join(str(c.value) for row in ws.iter_rows() for c in row
                         if c.value)
        assert "장기급여 대상" in text
        assert "장기급여 기산일" in text


class TestTheGuideStaysHonest:
    def test_every_sheet_is_named_in_the_guide(self, blank) -> None:
        ws = blank["작성요령"]
        named = {c.value for row in ws.iter_rows(max_col=1) for c in row if c.value}
        for sheet in blank.sheetnames:
            if sheet != "작성요령":
                assert sheet in named, f"[{sheet}] 을 작성요령이 소개하지 않는다"

    def test_the_column_table_lists_every_column(self, blank) -> None:
        ws = blank["작성요령"]
        listed = [c.value for row in ws.iter_rows(min_col=3, max_col=3)
                  for c in row if c.value]
        for _block, label, *_rest in ACTIVE + RETIRED:
            assert label in listed, f"'{label}' 열이 작성요령에 없다"

    def test_the_header_row_is_frozen(self, blank) -> None:
        """수백 줄을 채우는 동안 열 이름이 화면에서 사라지면 칸을 밀려 적는다."""
        for sheet in ("재직자명부", "퇴직자명부"):
            assert blank[sheet].freeze_panes == f"A{FIRST_DATA_ROW}"
