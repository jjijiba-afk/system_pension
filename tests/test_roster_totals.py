"""명부 맨 윗줄의 총계와, 보내기 전 확인 목록.

작아 보이지만 값이 크다. 채우는 사람이 **자기 숫자를 바로 본다** — 한 사람을
빠뜨리거나 임금에 0 을 하나 더 붙이면 그 자리에서 합계가 튄다. 받는 쪽도
'읽은 줄 수 × 합계' 로 대조할 수 있다.

박아 넣은 숫자로 두면 안 된다. 회사가 줄을 더하거나 고치는 순간 거짓말이 되고,
거짓말인지 아닌지는 보는 사람이 알 수가 없다. **수식이라야** 따라 움직인다.
"""

from __future__ import annotations

import openpyxl
import pytest

from pension import rostertemplate as tpl

ROSTER_SHEETS = ("재직자명부", "퇴직자명부", "추가명부", "전년명부")


@pytest.fixture(scope="module")
def blank(tmp_path_factory):
    path = tpl.write_roster_template(tmp_path_factory.mktemp("양식") / "양식.xlsx")
    return path


@pytest.fixture(scope="module")
def formulas(blank):
    return openpyxl.load_workbook(blank)


@pytest.fixture(scope="module")
def values(blank):
    return openpyxl.load_workbook(blank, data_only=True)


class TestTheTotalsStrip:
    def test_every_roster_sheet_counts_its_people(self, formulas) -> None:
        for name in ROSTER_SHEETS:
            ws = formulas[name]
            heads = {str(c.value or ""): c.column for c in ws[tpl.HEADER_ROW]}
            column = heads.get("사번")
            assert column, f"[{name}] 에 사번 열이 없다"
            cell = ws.cell(1, column)
            assert str(cell.value or "").startswith("=COUNTA("), \
                f"[{name}] 인원수가 수식이 아니다: {cell.value!r}"
            assert "명" in cell.number_format

    def test_every_money_column_is_summed(self, formulas) -> None:
        """금액 열은 빠짐없이 합계가 있어야 한다.

        한 열만 빠져 있으면 채우는 사람은 그 열에 총계가 원래 없는 줄 안다 —
        하필 그 열이 틀렸을 때 아무도 못 잡는다.
        """
        for name in ROSTER_SHEETS:
            ws = formulas[name]
            for cell in ws[tpl.HEADER_ROW]:
                label = str(cell.value or "")
                if label not in tpl.MONEY_COLUMNS:
                    continue
                top = ws.cell(1, cell.column)
                assert str(top.value or "").startswith("=SUM("), \
                    f"[{name}] '{label}' 에 합계가 없다"
                assert top.number_format == tpl.MONEY_FORMAT

    def test_nothing_else_sits_on_the_top_row(self, formulas) -> None:
        """총계가 아닌 칸에 숫자를 두면 그 줄이 무슨 줄인지 흐려진다."""
        for name in ROSTER_SHEETS:
            ws = formulas[name]
            heads = {c.column: str(c.value or "") for c in ws[tpl.HEADER_ROW]}
            for cell in ws[1]:
                if cell.column == 1 or cell.value in (None, ""):
                    continue
                label = heads.get(cell.column, "")
                assert label == "사번" or label in tpl.MONEY_COLUMNS, \
                    f"[{name}] 1행 {cell.coordinate}('{label}') 에 엉뚱한 값"

    def test_the_range_reaches_past_the_data(self, formulas) -> None:
        """합계 범위가 자료보다 짧으면 뒷사람이 조용히 빠진다."""
        ws = formulas["재직자명부"]
        heads = {str(c.value or ""): c.column for c in ws[tpl.HEADER_ROW]}
        text = str(ws.cell(1, heads["30일 평균임금"]).value)
        assert f"{tpl.FIRST_DATA_ROW}:" in text
        assert str(tpl.TOTAL_LAST_ROW) in text
        assert tpl.TOTAL_LAST_ROW > 1000

    def test_the_shipped_file_shows_a_number_not_a_blank(self, values) -> None:
        """엑셀이 '제한된 보기' 로 열면 수식 칸이 비어 보인다.

        받는 사람 눈에는 표가 깨진 것처럼 보이므로, 값을 미리 심어 둔다.
        작성 예시 두 사람이 있으니 인원수는 2 여야 한다.
        """
        ws = values["재직자명부"]
        heads = {str(c.value or ""): c.column for c in ws[tpl.HEADER_ROW]}
        assert ws.cell(1, heads["사번"]).value == 2
        assert ws.cell(1, heads["30일 평균임금"]).value == pytest.approx(
            5_000_000 + 9_000_000)

    def test_it_follows_what_you_type(self, tmp_path) -> None:
        """수식이라야 값이 크다 — 줄을 고치면 합계가 따라 움직여야 한다."""
        path = tpl.write_roster_template(tmp_path / "양식.xlsx")
        wb = openpyxl.load_workbook(path)
        ws = wb["재직자명부"]
        heads = {str(c.value or ""): c.column for c in ws[tpl.HEADER_ROW]}
        wage = heads["30일 평균임금"]
        # 한 사람을 더 적어 넣는다. 합계 수식은 그대로 두었는데도 늘어야 한다.
        ws.cell(tpl.FIRST_DATA_ROW + 2, heads["사번"], "A0003")
        ws.cell(tpl.FIRST_DATA_ROW + 2, wage, 1_000_000)
        assert str(ws.cell(1, wage).value).startswith("=SUM(")
        top, bottom = str(ws.cell(1, wage).value).split("(")[1].rstrip(")").split(":")
        assert int(bottom[1:]) >= tpl.FIRST_DATA_ROW + 2, \
            f"새 줄이 합계 범위 밖이다: {top}:{bottom}"


class TestTheChecklist:
    def test_the_guide_carries_it(self, formulas) -> None:
        text = "\n".join(
            str(cell.value) for row in formulas["작성요령"].iter_rows()
            for cell in row if cell.value is not None)
        assert "보내기 전 확인" in text
        for item in tpl.CHECKLIST:
            assert item in text, f"빠진 항목: {item}"

    def test_every_item_has_a_box(self, formulas) -> None:
        """네모가 없으면 목록이 아니라 문단으로 읽힌다 — 훑고 지나간다."""
        ws = formulas["작성요령"]
        boxes = sum(1 for row in ws.iter_rows() for cell in row
                    if cell.value == "☐")
        assert boxes == len(tpl.CHECKLIST)

    def test_the_items_are_things_someone_can_actually_check(self) -> None:
        """물음표로 끝나는 확인 문장이어야 한다.

        '정확히 작성' 같은 말은 확인할 수가 없다. 회사 자료와 맞대어 보고
        예/아니오를 답할 수 있는 것만 목록에 둔다.
        """
        import re

        assert tpl.CHECKLIST
        for item in tpl.CHECKLIST:
            # 뒤에 붙은 괄호 설명은 확인 문장이 아니라 덧붙임이다.
            asked = re.sub(r"\s*\([^)]*\)\s*$", "", item)
            assert asked.endswith("가"), f"확인할 수 없는 문장: {item}"
