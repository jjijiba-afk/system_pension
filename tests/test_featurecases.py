"""특이사항 한 가지씩만 담은 명부 한 벌.

이 벌의 존재 이유는 **차이가 설명되는 것** 이다. 사람도 가정도 같고 한 칸만
달라졌으므로, 채무가 어느 쪽으로 움직여야 하는지는 계산 없이도 말할 수 있다.
그 방향이 맞는지를 여기서 못박는다 — 값 자체는 엔진이 낸 것이라 엔진을 검증할
수 없지만, **방향** 은 엔진 밖에서 판단한 것이라 검증이 된다.
"""

from __future__ import annotations

import openpyxl
import pytest

from pension.rostertemplate import FIRST_DATA_ROW, HEADER_ROW
from pension.featurecases import (
    BASE_SPEC,
    FEATURES,
    FLAT,
    measure_feature_pack,
    write_feature_pack,
)


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    """한 벌을 한 번만 만들어 나눠 쓴다. 명부 열한 벌을 산출하므로 오래 걸린다."""
    directory = tmp_path_factory.mktemp("특이사항")
    write_feature_pack(directory)
    return directory


@pytest.fixture(scope="module")
def measured(pack):
    return measure_feature_pack(pack)


def _rows(path, sheet="재직자명부"):
    book = openpyxl.load_workbook(path, data_only=True)
    ws = book[sheet]
    head = {c.value: c.column for c in ws[HEADER_ROW] if c.value}
    out = [
        {name: ws.cell(r, col).value for name, col in head.items()}
        for r in range(FIRST_DATA_ROW, ws.max_row + 1)
        if ws.cell(r, head["사번"]).value
    ]
    book.close()
    return out


class TestOnePopulation:
    """모든 명부가 **같은 사람** 이어야 한다.

    명부마다 다시 뽑으면 채무 차이가 특이사항 때문인지 사람이 달라서인지
    가릴 수 없다. 그러면 이 벌 전체가 쓸모없어진다.
    """

    def test_everyone_is_the_same_across_the_pack(self, pack) -> None:
        def who(path):
            return [
                (r["사번"], str(r["생년월일"]), str(r["입사일자"]), r["직군"])
                for r in _rows(path)
            ]

        baseline = who(pack / f"{BASE_SPEC.title}.xlsx")
        assert len(baseline) == BASE_SPEC.active
        for index, feature in enumerate(FEATURES, start=1):
            # 중간정산·임금단위는 근속 기산과 임금을 바꾸지만 사람은 그대로다.
            path = pack / f"{index}_{feature.key}.xlsx"
            assert [w[:1] + w[3:] for w in who(path)] == [
                w[:1] + w[3:] for w in baseline
            ], f"{path.name} 의 사람이 기준 명부와 다르다"

    def test_one_assumptions_workbook_for_all(self, pack) -> None:
        """명부마다 다른 기초율을 붙이면 차이가 어디서 왔는지 알 수 없다."""
        assert (pack / "기초율.xlsx").exists()
        assert not list(pack.glob("*_기초율.xlsx"))


class TestOneFeatureEach:
    def test_the_baseline_carries_none_of_them(self, pack) -> None:
        """기준 명부에는 특이사항 칸이 하나도 차 있지 않아야 한다."""
        columns = ("휴직차감일수", "중간정산일", "임금피크 연령", "임원지급배수",
                   "정년연령", "명예퇴직 기준임금", "추가지급 기본급",
                   "DB비율", "잔여계약기간")
        rows = _rows(pack / f"{BASE_SPEC.title}.xlsx")
        for column in columns:
            assert not any(r.get(column) for r in rows), f"기준 명부에 '{column}' 이 있다"
        assert not any(str(r.get("퇴직급여 제도구분")) == "DC" for r in rows)

    @pytest.mark.parametrize(
        "index,feature", list(enumerate(FEATURES, start=1)),
        ids=[f.key for f in FEATURES],
    )
    def test_the_feature_covers_its_whole_scope(self, pack, index, feature) -> None:
        """한 사람이 아니라 **범위 전체** 에 걸려 있어야 한다."""
        rows = _rows(pack / f"{index}_{feature.key}.xlsx")
        if feature.scope == "전원":
            targets = rows
        elif feature.scope == "임원":
            # 임직원구분 열은 양식에서 뺐다 — 임원인지는 직군으로 안다.
            targets = [r for r in rows if "임원" in str(r.get("직군"))]
        else:
            targets = [r for r in rows if str(r.get("직군")) == feature.scope]

        assert targets, f"{feature.scope} 에 해당하는 사람이 없다"
        marked = [r for r in targets if r.get("비고")]
        # 중간정산은 정산일보다 늦게 입사한 사람을 건너뛰므로 전원은 아니다.
        least = 0.5 if feature.key == "중간정산" else 1.0
        assert len(marked) >= len(targets) * least, (
            f"{feature.key}: 범위 {len(targets)}명 중 {len(marked)}명만 걸렸다"
        )
        # 범위 밖에는 걸리지 않아야 한다.
        if feature.scope != "전원":
            outside = [r for r in rows if r not in targets and r.get("비고")]
            assert not outside, f"{feature.key} 가 범위 밖 {len(outside)}명에도 걸렸다"


class TestTheObligationMovesTheRightWay:
    """이 벌의 값어치는 전부 여기 있다."""

    def test_the_baseline_is_a_sane_obligation(self, measured) -> None:
        first = measured[0]
        assert first.headcount == BASE_SPEC.active
        assert 5e9 < first.dbo < 1e11
        assert first.errors == 0

    @pytest.mark.parametrize("key", [f.key for f in FEATURES])
    def test_it_moves_as_the_note_promises(self, measured, key) -> None:
        row = next(m for m in measured if m.name.endswith(f"_{key}"))
        assert row.verdict == "맞음", (
            f"{row.name}: {row.expect} 라고 적어 두었는데 {row.moved} 했다 "
            f"({row.change:+,.0f}원)"
        )

    def test_the_ones_that_should_not_move_do_not_move_at_all(self, measured) -> None:
        """'변화 없음' 은 1원도 움직이지 않아야 한다.

        쓰이지 않아야 할 칸이 조금이라도 새어 들어가면 여기서 걸린다.
        """
        for row in measured:
            if row.expect == FLAT:
                assert row.change == 0, f"{row.name} 이 {row.change:+,.0f}원 움직였다"

    def test_dc_removes_exactly_the_contract_group(self, measured) -> None:
        """계약직을 통째로 DC 로 바꿨으면 그 직군 몫만큼만 빠져야 한다."""
        dc = next(m for m in measured if m.name.endswith("_DC전환"))
        assert dc.headcount < measured[0].headcount


class TestTheReportSaysWhatItIs:
    def test_it_does_not_call_the_numbers_an_answer(self, pack) -> None:
        """엔진이 낸 값을 '정답' 이라고 적으면 안 된다 — 엔진이 틀리면 같이 틀린다."""
        text = (pack / "특이사항_한가지씩_안내.txt").read_text(encoding="utf-8")
        assert "이 프로그램이 산출한 값" in text
        assert "기준값" in text
        assert "기준 대비 방향" in text
        for feature in FEATURES:
            assert feature.title in text
            assert feature.why in text
