"""PC 편집기(tkinter) — 화면을 실제로 띄워 확인한다.

이 창은 웹앱과 같은 state 를 다루지만 구현이 따로다. 두 화면이 어긋나면
**같은 입력이 화면에 따라 다른 파일** 을 낳는다. 특히 지급률 표의 '직군 아닌
열'(``정규직·정년``, ``사망가산``)을 이 창이 들고 있지 못하던 때에는, 웹앱에서
만든 파일을 여기서 열어 저장하는 것만으로 열과 값이 통째로 사라졌다.

tkinter 와 화면(DISPLAY)이 있어야 돌아간다 — 없으면 건너뛴다.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from pension import assumption_form as form  # noqa: E402


@pytest.fixture
def editor():
    """실제 창 하나. 화면이 없으면 이 시험은 건너뛴다."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:                        # pragma: no cover
        pytest.skip(f"화면 없음: {exc}")
    root.withdraw()

    from pension.editor import AssumptionsEditor

    window = AssumptionsEditor(root, job_groups=["정규직", "임원"])
    window.withdraw()
    yield window
    window.destroy()
    root.destroy()


def benefit_rows(window):
    from pension.assumptions import BENEFIT_SHEET

    return window._grids[BENEFIT_SHEET]


class TestExtraColumns:
    """직군 아닌 열을 열고 닫아도 값이 살아 있어야 한다."""

    def test_a_split_file_survives_open_and_save(self, editor, tmp_path) -> None:
        """웹앱에서 가른 파일을 여기서 열었다 저장해도 그대로여야 한다."""
        state = form.split_benefit_by_cause(
            form.example_state(["정규직", "임원"]), "정규직")
        columns = form.grid_columns(state, "지급률")
        row = ["0"] + [""] * len(columns)
        row[columns.index("정규직·정년") + 1] = "3.0"
        state["grids"]["지급률"]["rows"] = [row]
        form.write_state(state, tmp_path / "원본.xlsx")

        editor.load_workbook(tmp_path / "원본.xlsx")
        editor.write(tmp_path / "다시.xlsx")

        back = form.read_state(tmp_path / "다시.xlsx")
        assert form.cause_split_rules(back) == ["정규직"]
        assert form.grid_columns(back, "지급률") == columns
        assert back["grids"]["지급률"]["rows"] == [["0", "", "", "", "", "3"]]

    def test_changing_job_groups_keeps_values_by_name(self, editor) -> None:
        """열이 하나 늘어도 뒤의 배수가 한 칸씩 밀리면 안 된다."""
        grid = benefit_rows(editor)
        grid.load([["10", "12.0", "24.0"]], ["사망가산"])
        assert grid.columns == ["정규직", "임원", "사망가산"]

        editor.job_group_var.set("정규직, 계약직, 임원")
        editor._apply_job_groups()

        assert grid.columns == ["정규직", "계약직", "임원", "사망가산"]
        # '임원' 의 24.0 이 새로 낀 '계약직' 자리로 밀려서는 안 된다.
        assert grid.get_rows() == [["10", "12.0", "", "24.0", ""]]

    def test_extra_column_gets_its_own_rule_row(self, editor) -> None:
        """방식·수식은 열마다 있다. 줄이 없으면 그 열은 규정 없이 남는다."""
        benefit_rows(editor).load([], ["사망가산"])
        editor._refresh_benefit_columns()
        assert "사망가산" in editor._rule_tab._rows


class TestCauseSplit:
    """지급률 규정 탭의 [사유별 차등] 체크."""

    def test_checking_it_makes_three_columns(self, editor) -> None:
        editor._toggle_cause_split("정규직", True)

        assert benefit_rows(editor).columns == [
            "정규직", "임원", "정규직·중도", "정규직·사망", "정규직·정년"]
        # 퇴직사유 연결까지 끝나야 손댈 곳이 없다.
        linked = {(r[0], r[1]): r[2] for r in editor._cause_tab.get_values()}
        assert linked[("정규직", "정년")] == "정규직·정년"
        assert editor._rule_tab.split == ["정규직"]

    def test_the_new_columns_are_editable_here(self, editor) -> None:
        """갈라만 놓고 방식을 못 고치면 이 창에서는 반쪽이다."""
        editor._toggle_cause_split("정규직", True)
        assert "정규직·정년" in editor._rule_tab._rows
        editor._rule_tab._rows["정규직·정년"]["mode"].set("누진")
        assert editor.state()["benefit_rules"]["정규직·정년"]["mode"] == "누진"

    def test_merging_takes_the_columns_and_values_away(
        self, editor, monkeypatch
    ) -> None:
        from pension import editor as module

        editor._toggle_cause_split("정규직", True)
        columns = benefit_rows(editor).columns
        row = ["0"] + [""] * len(columns)
        row[columns.index("정규직·정년") + 1] = "3.0"
        benefit_rows(editor).set_rows([row])

        # 값이 사라지는 조작이라 확인을 묻는다. 여기서는 '예' 로 답한다.
        monkeypatch.setattr(module.messagebox, "askyesno", lambda *a, **k: True)
        editor._toggle_cause_split("정규직", False)

        assert benefit_rows(editor).columns == ["정규직", "임원"]
        assert benefit_rows(editor).get_rows() == []
        assert editor._cause_tab.get_values() == []

    def test_saying_no_to_the_merge_leaves_everything(self, editor, monkeypatch) -> None:
        """되돌릴 수 없는 조작이라, 아니오면 체크도 원래대로 돌아와야 한다."""
        from pension import editor as module

        editor._toggle_cause_split("정규직", True)
        monkeypatch.setattr(module.messagebox, "askyesno", lambda *a, **k: False)
        editor._toggle_cause_split("정규직", False)

        assert "정규직·정년" in benefit_rows(editor).columns
        assert editor._rule_tab._rows["정규직"]["split"].get() is True

    def test_only_the_chosen_group_splits(self, editor) -> None:
        editor._toggle_cause_split("정규직", True)
        assert "임원·정년" not in benefit_rows(editor).columns
        assert editor._rule_tab._rows["임원"].get("split") is not None

    def test_a_cause_column_offers_no_checkbox(self, editor) -> None:
        """가른 열 자신에게 다시 물으면 끝없이 갈라진다."""
        editor._toggle_cause_split("정규직", True)
        assert "split" not in editor._rule_tab._rows["정규직·정년"]

    def test_the_engine_reads_what_this_window_writes(self, editor, tmp_path) -> None:
        from pension.assumptions import CAUSE_NORMAL, load_assumptions

        editor._toggle_cause_split("정규직", True)
        columns = benefit_rows(editor).columns
        row = ["0"] + [""] * len(columns)
        row[columns.index("정규직·정년") + 1] = "3.0"
        benefit_rows(editor).set_rows([row])
        editor._grids["할인율"].set_rows([["1", "4.5%"]])

        editor.write(tmp_path / "기초율.xlsx")
        causes = load_assumptions(tmp_path / "기초율.xlsx").exit_causes
        assert causes.get("정규직", CAUSE_NORMAL).benefit_rule == "정규직·정년"


class TestAddColumnButton:
    """[항목 추가] — '사망가산' 처럼 직군 아닌 지급률을 이 창에서도 만든다."""

    def test_adding_a_column_gives_it_a_rule_row(self, editor, monkeypatch) -> None:
        from pension import editor as module

        monkeypatch.setattr(module.simpledialog, "askstring", lambda *a, **k: "사망가산")
        benefit_rows(editor).add_column()

        assert benefit_rows(editor).columns == ["정규직", "임원", "사망가산"]
        assert "사망가산" in editor._rule_tab._rows
        # 퇴직사유의 '가산 규정' 에서 고를 수 있어야 쓸모가 있다.
        assert "사망가산" in editor._cause_tab.columns

    def test_a_duplicate_name_is_refused(self, editor, monkeypatch) -> None:
        from pension import editor as module

        monkeypatch.setattr(module.simpledialog, "askstring", lambda *a, **k: "정규직")
        monkeypatch.setattr(module.messagebox, "showwarning", lambda *a, **k: None)
        benefit_rows(editor).add_column()
        assert benefit_rows(editor).columns == ["정규직", "임원"]

    def test_removing_a_column_takes_its_values(self, editor, monkeypatch) -> None:
        from pension import editor as module

        monkeypatch.setattr(module.simpledialog, "askstring", lambda *a, **k: "사망가산")
        benefit_rows(editor).add_column()
        benefit_rows(editor).set_rows([["10", "12.0", "24.0", "3.0"]])

        benefit_rows(editor).remove_column()
        assert benefit_rows(editor).columns == ["정규직", "임원"]
        assert benefit_rows(editor).get_rows() == [["10", "12.0", "24.0"]]
